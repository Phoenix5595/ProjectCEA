"""Owned loopback-only DAV server storing real Calendar and Files bytes.

The fixture intentionally implements HTTP behavior, not mocked client returns:
conditional ETags, UID queries, uploads, MOVE and GET operate on persisted
in-memory resources. Bind port zero and use it only from the owning test.
"""
from __future__ import annotations

import contextlib
import base64
import functools
import html
import hashlib
import http.server
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Iterator
from urllib.parse import unquote, urlsplit
from xml.etree import ElementTree as ET

TOKEN = "fixture-only-key"


@dataclass
class DAVState:
    files: dict[str, bytes] = field(default_factory=dict)
    etags: dict[str, str] = field(default_factory=dict)
    calendars: dict[str, bytes] = field(default_factory=dict)
    uploads: dict[str, dict[str, bytes]] = field(default_factory=dict)
    upload_destinations: dict[str, str] = field(default_factory=dict)
    upload_events: list[tuple[str, str, str]] = field(default_factory=list)
    counters: dict[str, int] = field(default_factory=dict)
    active: int = 0
    max_inflight: int = 0
    accept_then_timeout: bool = False
    drop_final_ack: bool = False
    duplicate_uid: bool = False
    changed_412: bool = False
    auth_failure: bool = False
    quota: bool = False
    corrupt_get: bool = False
    absent: bool = False
    missing_calendar: bool = False
    files_readonly: bool = False
    drop_delete_ack: bool = False
    request_delay: float = 0.0
    lock: threading.RLock = field(default_factory=threading.RLock, repr=False)
    instance_id: str = field(default_factory=lambda: uuid.uuid4().hex)

    def count(self, method: str) -> None:
        with self.lock:
            self.counters[method] = self.counters.get(method, 0) + 1


def _calendar_events(payload: bytes):
    from icalendar import Calendar
    return [event for event in Calendar.from_ical(payload).walk() if event.name == "VEVENT"]


@contextlib.contextmanager
def loopback_dav() -> Iterator[tuple[str, DAVState]]:
    state = DAVState()

    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args):
            return


        def _body(self) -> bytes:
            return self.rfile.read(int(self.headers.get("Content-Length", "0")))

        def _reply(self, status: int, body: bytes = b"", headers: dict[str, str] | None = None):
            self.send_response(status)
            self.send_header("Content-Length", str(len(body)))
            for key, value in (headers or {}).items():
                self.send_header(key, value)
            self.end_headers()
            if body:
                self.wfile.write(body)

        def _authorized(self) -> bool:
            if state.auth_failure:
                self.close_connection = True
                self._reply(401)
                return False
            expected = "Basic "+base64.b64encode(b"fixture:fixture-only-password").decode()
            if self.headers.get("Authorization", "") not in (expected, "Bearer fixture-only-key"):
                self.close_connection = True
                self._reply(401)
                return False
            return True

        def _path(self) -> str:
            return unquote(urlsplit(self.path).path).rstrip("/") or "/"

        def _upload_destination(self) -> str | None:
            value = self.headers.get("Destination")
            parsed = urlsplit(value or "")
            target = unquote(parsed.path)
            expected_netloc = f"127.0.0.1:{self.server.server_port}"
            if (not value or parsed.scheme != "http" or parsed.netloc != expected_netloc
                    or not target.startswith("/dav/files/fixture/") or target.endswith("/")
                    or parsed.query or parsed.fragment):
                self._reply(400)
                return None
            return value

        def _property_xml(self, path: str) -> bytes:
            if path == "/dav":
                href = "/dav/"
                props = '<d:current-user-principal><d:href>/dav/principals/users/fixture</d:href></d:current-user-principal><d:owner><d:href>/dav/principals/users/fixture</d:href></d:owner><d:resourcetype><d:collection/></d:resourcetype><oc:id>fixture-instance</oc:id>'
            elif path.endswith("/principals/users/fixture"):
                href = path
                props = '<d:current-user-principal><d:href>'+path+'</d:href></d:current-user-principal><c:calendar-home-set><d:href>/dav/calendars/fixture/</d:href></c:calendar-home-set><oc:home-set><d:href>/dav/files/fixture/</d:href></oc:home-set><d:resourcetype><d:principal/></d:resourcetype>'
            elif path == "/dav/calendars/fixture":
                href = path
                if state.missing_calendar:
                    return b'<d:multistatus xmlns:d="DAV:"/>'
                return ('<d:multistatus xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav"><d:response><d:href>/dav/calendars/fixture/culture/</d:href><d:propstat><d:prop><d:resourcetype><d:collection/><c:calendar/></d:resourcetype><d:displayname>Culture</d:displayname></d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response></d:multistatus>').encode()
            elif path == "/dav/calendars/fixture/culture":
                href = path
                props = '<d:resourcetype><d:collection/><c:calendar/></d:resourcetype><d:displayname>Culture</d:displayname>'
            elif path == "/dav/files/fixture":
                href = path
                permissions = "G" if state.files_readonly else "GCK"
                props = f'<d:resourcetype><d:collection/></d:resourcetype><oc:permissions>{permissions}</oc:permissions><oc:id>fixture-files-root-{state.instance_id}</oc:id>'
            else:
                href = path
                props = '<d:resourcetype><d:collection/></d:resourcetype>'
            return (f'<?xml version="1.0"?><d:multistatus xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav" xmlns:oc="http://owncloud.org/ns"><d:response><d:href>{href}</d:href><d:propstat><d:prop>{props}</d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response></d:multistatus>').encode()

        def do_PROPFIND(self):
            state.count("PROPFIND")
            self._body()
            if not self._authorized(): return
            path = self._path()
            if "/uploads/" in path and path not in state.uploads:
                return self._reply(404)
            body = self._property_xml(path)
            self._reply(207, body, {"Content-Type": "application/xml"})

        def do_REPORT(self):
            state.count("REPORT")
            if not self._authorized(): return
            query = ET.fromstring(self._body())
            uid = ""
            for prop in query.iter("{urn:ietf:params:xml:ns:caldav}prop-filter"):
                if prop.attrib.get("name") == "UID":
                    uid = prop.findtext("{urn:ietf:params:xml:ns:caldav}text-match", "")
            matching = [(href, data) for href, data in tuple(state.calendars.items())
                        if any(str(event.get("UID", "")) == uid for event in _calendar_events(data))]
            if state.duplicate_uid and len(matching) == 1:
                href, data = matching[0]
                duplicate = href+"-duplicate.ics"
                state.calendars[duplicate] = data
                state.etags[duplicate] = state.etags[href]
                matching.append((duplicate,data))
            results = [
                f'<d:response><d:href>{html.escape(href)}</d:href><d:propstat><d:prop>'
                f'<d:getetag>{html.escape(state.etags[href])}</d:getetag>'
                f'<c:calendar-data>{html.escape(data.decode())}</c:calendar-data></d:prop>'
                '<d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>'
                for href, data in matching]
            xml = ('<d:multistatus xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">'+''.join(results)+'</d:multistatus>').encode()
            self._reply(207, xml)

        def do_GET(self):
            state.count("GET")
            if not self._authorized(): return
            path = self._path()
            data = state.files.get(path, state.calendars.get(path))
            if data is None or state.absent:
                return self._reply(404)
            if state.corrupt_get:
                data = data + b"corrupt"
            self._reply(200, data, {"ETag": state.etags.get(path, '"'+hashlib.sha256(data).hexdigest()+'"')})

        def do_PUT(self):
            state.count("PUT")
            if not self._authorized(): return
            path, data = self._path(), self._body()
            if state.quota: return self._reply(507)
            if "/uploads/" in path:
                collection, chunk = path.rsplit("/", 1)
                destination = self._upload_destination()
                if destination is None: return
                if collection not in state.uploads:
                    return self._reply(404)
                if (state.upload_destinations.get(collection) != destination
                        or not chunk.isascii() or not chunk.isdigit()
                        or len(chunk) > 5 or not 1 <= int(chunk) <= 10_000):
                    return self._reply(400)
                state.uploads.setdefault(collection, {})[chunk] = data
                state.upload_events.append((collection, chunk, destination))
                return self._reply(201)
            if self.headers.get("If-None-Match") == "*" and path in state.calendars:
                return self._reply(412)
            current = state.etags.get(path)
            if self.headers.get("If-Match") and self.headers.get("If-Match") != current:
                if state.changed_412:
                    state.calendars[path] = b"BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:foreign\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
                return self._reply(412)
            state.calendars[path] = data
            etag = '"'+hashlib.sha256(data).hexdigest()+'"'
            state.etags[path] = etag
            if state.accept_then_timeout:
                state.accept_then_timeout = False
                self.close_connection = True
                return
            self._reply(201, headers={"ETag": etag})

        def do_MKCOL(self):
            state.count("MKCOL")
            if not self._authorized(): return
            path = self._path()
            if "/uploads/" in path:
                destination = self._upload_destination()
                if destination is None: return
                state.upload_destinations[path] = destination
                state.uploads.setdefault(path, {})
            self._reply(201)

        def do_MOVE(self):
            state.count("MOVE")
            if not self._authorized(): return
            if state.quota: return self._reply(507)
            source = self._path()
            destination_header = self._upload_destination()
            if destination_header is None: return
            destination = unquote(urlsplit(destination_header).path).rstrip("/")
            if self.headers.get("Overwrite") == "F" and destination in state.files:
                return self._reply(412)
            # Chunk v2 merges actual uploaded chunks into .file on final MOVE.
            if source.endswith("/.file"):
                collection = source[:-len("/.file")]
                if state.upload_destinations.get(collection) != destination_header:
                    return self._reply(400)
                if collection not in state.uploads:
                    return self._reply(404)
                chunks = state.uploads[collection]
                data = b"".join(chunks[k] for k in sorted(chunks, key=int))
            else:
                data = state.files.get(source)
            if data is None: return self._reply(404)
            if source.endswith("/.file"):
                state.uploads.pop(collection, None)
                state.upload_destinations.pop(collection, None)
            state.files[destination] = data
            state.etags[destination] = '"'+hashlib.sha256(data).hexdigest()+'"'
            if state.drop_final_ack:
                state.drop_final_ack = False
                self.close_connection = True
                return
            self._reply(201)

        def do_DELETE(self):
            state.count("DELETE")
            if not self._authorized(): return
            path = self._path()
            existed = path in state.calendars or path in state.files
            if path in state.calendars:
                current = state.etags[path]
                if self.headers.get("If-Match") and self.headers["If-Match"] != current:
                    return self._reply(412)
                del state.calendars[path]
            state.files.pop(path, None)
            state.etags.pop(path, None)
            if state.drop_delete_ack:
                state.drop_delete_ack = False
                self.close_connection = True
                return
            self._reply(204 if existed else 404)

        def do_POST(self):
            state.count("POST")
            if not self._authorized(): return
            body = self._body()
            path = self._path()
            if "/uploads/" in path:
                state.uploads.setdefault(path.rsplit("/", 1)[0], {})[path.rsplit("/", 1)[-1]] = body
                return self._reply(201)
            self._reply(404)

    def counted(method):
        @functools.wraps(method)
        def dispatch(handler):
            with state.lock:
                state.active += 1
                state.max_inflight = max(state.max_inflight, state.active)
            try:
                if state.request_delay:
                    time.sleep(state.request_delay)
                return method(handler)
            finally:
                with state.lock:
                    state.active -= 1
        return dispatch

    for method_name in ("PROPFIND", "REPORT", "GET", "PUT", "MKCOL", "MOVE", "DELETE", "POST"):
        setattr(Handler, "do_"+method_name, counted(getattr(Handler, "do_"+method_name)))

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def fixture_http_client():
    """Return a client that cannot inherit proxy configuration from the host."""
    import httpx

    return httpx.AsyncClient(auth=httpx.BasicAuth("fixture", "fixture-only-password"), trust_env=False, timeout=5.0)
