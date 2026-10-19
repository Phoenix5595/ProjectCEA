"""Bounded async DAV discovery shared by Calendar and Files, never public shares."""
from __future__ import annotations

from dataclasses import dataclass
import os
from urllib.parse import quote, unquote, urljoin, urlsplit
from uuid import NAMESPACE_URL, UUID, uuid5
from xml.etree import ElementTree as ET

import httpx

DAV = 'DAV:'
CALDAV = 'urn:ietf:params:xml:ns:caldav'
OC = 'http://owncloud.org/ns'
MAX_DAV_XML_BYTES = 1048576


class DavError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def validate_dav_url(url: str, *, origin: str | None = None, collection: str | None = None) -> str:
    parsed = urlsplit(url)
    allowed_http = (os.getenv('CALDAV_ALLOW_HTTP_TEST','').lower()=='true'
                    and parsed.hostname in ('127.0.0.1','localhost','::1'))
    if (parsed.scheme != 'https' and not (parsed.scheme=='http' and allowed_http)) or not parsed.hostname:
        raise DavError('calendar_url_invalid')
    if parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment:
        raise DavError('calendar_url_invalid')
    if any(part in ('.','..') for part in unquote(parsed.path).split('/')) or '\\' in parsed.path:
        raise DavError('calendar_url_invalid')
    if origin is not None:
        expected = urlsplit(origin)
        if (parsed.scheme,parsed.hostname,parsed.port or (443 if parsed.scheme=='https' else 80)) != (
                expected.scheme,expected.hostname,expected.port or (443 if expected.scheme=='https' else 80)):
            raise DavError('calendar_remote_conflict')
    if collection is not None:
        parent = urlsplit(collection)
        if not parsed.path.startswith(parent.path.rstrip('/')+'/'):
            raise DavError('calendar_remote_conflict')
    return url


def resolve_dav_href(base: str, href: str, *, collection: str | None = None) -> str:
    return validate_dav_url(urljoin(base,href),origin=base,collection=collection)


@dataclass(frozen=True,slots=True)
class DavReply:
    status: int
    headers: httpx.Headers
    body: bytes


async def dav_request(client: httpx.AsyncClient, method: str, url: str, *, content: bytes | None = None,
                      headers: dict[str,str] | None = None, max_bytes: int = MAX_DAV_XML_BYTES) -> DavReply:
    validate_dav_url(url)
    async with client.stream(method,url,content=content,headers=headers) as response:
        body = bytearray()
        async for part in response.aiter_bytes(65536):
            if len(body)+len(part)>max_bytes:
                raise DavError('calendar_remote_response_invalid')
            body.extend(part)
        return DavReply(response.status_code,response.headers,bytes(body))


def require_dav_success(reply: DavReply) -> None:
    if reply.status in (401,403):
        raise DavError('calendar_auth_failed')
    if reply.status==404:
        raise DavError('calendar_collection_unavailable')
    if not 200<=reply.status<300:
        raise DavError('calendar_transport_failed' if reply.status>=500 else 'calendar_remote_conflict')


def parse_dav_xml(payload: bytes) -> ET.Element:
    if len(payload)>MAX_DAV_XML_BYTES or b'<!DOCTYPE' in payload.upper() or b'<!ENTITY' in payload.upper():
        raise DavError('calendar_remote_response_invalid')
    try:
        return ET.fromstring(payload)
    except ET.ParseError as error:
        raise DavError('calendar_remote_response_invalid') from error


def successful_properties(response: ET.Element) -> dict[str,ET.Element]:
    result = {}
    for propstat in response.findall(f'{{{DAV}}}propstat'):
        status = propstat.findtext(f'{{{DAV}}}status','')
        if len(status.split())<2 or status.split()[1]!='200':
            continue
        prop = propstat.find(f'{{{DAV}}}prop')
        if prop is not None:
            result.update((child.tag,child) for child in prop)
    return result


def propfind_body(properties: tuple[tuple[str,str],...]) -> bytes:
    root = ET.Element(f'{{{DAV}}}propfind')
    prop = ET.SubElement(root,f'{{{DAV}}}prop')
    for namespace,name in properties:
        ET.SubElement(prop,f'{{{namespace}}}{name}')
    return ET.tostring(root,encoding='utf-8',xml_declaration=True)


async def _properties(client: httpx.AsyncClient, url: str, properties: tuple[tuple[str,str],...],
                      *, depth: int = 0) -> list[tuple[str,dict[str,ET.Element]]]:
    reply = await dav_request(client,'PROPFIND',url,content=propfind_body(properties),
                              headers={'Depth':str(depth),'Content-Type':'application/xml; charset=utf-8'})
    require_dav_success(reply)
    root = parse_dav_xml(reply.body)
    return [(response.findtext(f'{{{DAV}}}href',''),successful_properties(response))
            for response in root.findall(f'{{{DAV}}}response')]


def _href(properties: dict[str,ET.Element], namespace: str, name: str) -> str:
    value = properties.get(f'{{{namespace}}}{name}')
    if value is None:
        raise DavError('calendar_collection_unavailable')
    href = value.findtext(f'{{{DAV}}}href','')
    if not href:
        raise DavError('calendar_collection_unavailable')
    return href


@dataclass(frozen=True,slots=True)
class DavDiscovery:
    principal_url: str
    calendar_home_url: str
    files_root_url: str
    uploads_root_url: str
    instance_id: UUID
    calendars: tuple[dict[str,str],...]


async def discover_nextcloud(client: httpx.AsyncClient, base_url: str) -> DavDiscovery:
    """Verify the authenticated principal, existing calendars and writable Files root.

    Files root is derived from the server's canonical principal UID, never the
    supplied login spelling or a Calendar collection path, then independently
    authenticated with PROPFIND. No folder or calendar is created by discovery.
    """
    base_url = validate_dav_url(base_url).rstrip('/')+'/'
    principal_rows = await _properties(client,base_url,((DAV,'current-user-principal'),))
    if len(principal_rows)!=1:
        raise DavError('calendar_collection_unavailable')
    principal = resolve_dav_href(base_url,_href(principal_rows[0][1],DAV,'current-user-principal'))
    home_rows = await _properties(client,principal,((CALDAV,'calendar-home-set'),))
    if len(home_rows)!=1:
        raise DavError('calendar_collection_unavailable')
    home = resolve_dav_href(base_url,_href(home_rows[0][1],CALDAV,'calendar-home-set')).rstrip('/')+'/'
    rows = await _properties(client,home,((DAV,'displayname'),(DAV,'resourcetype')),depth=1)
    calendars = []
    for href,properties in rows:
        kind = properties.get(f'{{{DAV}}}resourcetype')
        if kind is None or kind.find(f'{{{CALDAV}}}calendar') is None:
            continue
        title = properties.get(f'{{{DAV}}}displayname')
        name = title.text or '' if title is not None else ''
        calendars.append({'name':name,'url':resolve_dav_href(home,href,collection=home).rstrip('/')+'/'})
    principal_path = urlsplit(principal).path
    marker = '/principals/users/'
    if marker not in principal_path:
        raise DavError('calendar_files_unavailable')
    dav_prefix,encoded_user = principal_path.split(marker,1)
    encoded_user = encoded_user.strip('/')
    if not encoded_user or '/' in encoded_user or '/' in unquote(encoded_user):
        raise DavError('calendar_files_unavailable')
    origin = base_url.split('://',1)[0]+'://'+urlsplit(base_url).netloc
    user = quote(unquote(encoded_user),safe='')
    files_root = validate_dav_url(origin+dav_prefix+'/files/'+user+'/',origin=base_url)
    file_rows = await _properties(client,files_root,((DAV,'resourcetype'),(OC,'permissions'),(OC,'id')))
    if len(file_rows)!=1:
        raise DavError('calendar_files_unavailable')
    props = file_rows[0][1]
    kind = props.get(f'{{{DAV}}}resourcetype')
    permissions = props.get(f'{{{OC}}}permissions')
    global_id = props.get(f'{{{OC}}}id')
    if (kind is None or kind.find(f'{{{DAV}}}collection') is None or permissions is None
            or not {'G','C','K'}.issubset(permissions.text or '') or global_id is None or not global_id.text):
        raise DavError('calendar_files_unavailable')
    uploads_root = validate_dav_url(origin+dav_prefix+'/uploads/'+user+'/',origin=base_url)
    return DavDiscovery(principal,home,files_root,uploads_root,
                        uuid5(NAMESPACE_URL,'sj-cea-nextcloud-root:'+global_id.text),tuple(calendars))
