"""PostgreSQL binary fields with exact inverse mappings, including array shape."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
import json
import re
import struct
from uuid import UUID

PG_EPOCH = datetime(2000,1,1,tzinfo=UTC)
UNIX_OFFSET_US = 946684800000000
UNIX_OFFSET_DAYS = 10957
OIDS = {'boolean':16,'bytea':17,'smallint':21,'integer':23,'bigint':20,'text':25,
        'real':700,'double precision':701,'date':1082,'timestamp without time zone':1114,
        'timestamp with time zone':1184,'numeric':1700,'uuid':2950,'jsonb':3802,'json':114}
ALIASES = {'int2':'smallint','int4':'integer','int8':'bigint','float4':'real','float8':'double precision',
           'bool':'boolean','timestamptz':'timestamp with time zone','timestamp':'timestamp without time zone',
           'varchar':'text','character varying':'text','char':'text','character':'text','name':'text'}


class FieldEncodingError(ValueError):
    pass


def kind(typ: str) -> str:
    typ = typ.lower()
    if typ.endswith('[]'):
        return kind(typ[:-2])+'[]'
    if re.fullmatch(r'(?:character varying|character|varchar|char)\(\d+\)',typ):
        return 'text'
    if typ.startswith('numeric('):
        if not re.fullmatch(r'numeric\(\d+,-?\d+\)',typ):
            raise FieldEncodingError('unsupported numeric descriptor')
        return 'numeric'
    value = ALIASES.get(typ,typ)
    if value not in OIDS:
        raise FieldEncodingError('unsupported PostgreSQL field type')
    return value


def numeric_text(raw: bytes) -> str:
    if len(raw)<8:
        raise FieldEncodingError('truncated numeric')
    count,weight,sign,scale = struct.unpack('>hhHH',raw[:8])
    if count<0 or len(raw)!=8+2*count or scale>16383:
        raise FieldEncodingError('invalid numeric framing')
    special = {0xC000:'NaN',0xD000:'Infinity',0xF000:'-Infinity'}
    if sign in special:
        if count or scale or weight:
            raise FieldEncodingError('noncanonical special numeric')
        return special[sign]
    if sign not in (0,0x4000):
        raise FieldEncodingError('invalid numeric sign')
    groups = struct.unpack(f'>{count}H',raw[8:]) if count else ()
    if any(group>9999 for group in groups):
        raise FieldEncodingError('invalid numeric digit')
    positions = {weight-index:group for index,group in enumerate(groups)}
    integer = ''.join(f'{positions.get(position,0):04d}' for position in range(max(weight,0),-1,-1)).lstrip('0') or '0'
    fraction = ''.join(f'{positions.get(-position,0):04d}' for position in range(1,(scale+3)//4+1))[:scale]
    return ('-' if sign==0x4000 else '')+integer+('.'+fraction if scale else '')


def numeric_binary(value: str | Decimal) -> bytes:
    text = str(value)
    special = {'NaN':0xC000,'Infinity':0xD000,'-Infinity':0xF000}
    if text in special:
        return struct.pack('>hhHH',0,0,special[text],0)
    decimal = Decimal(text)
    if not decimal.is_finite():
        raise FieldEncodingError('invalid numeric value')
    sign,_,exponent = decimal.as_tuple()
    scale = max(0,-int(exponent))
    if scale>16383:
        raise FieldEncodingError('numeric scale unsupported')
    fixed = format(decimal.copy_abs(),'f')
    integer,_,fraction = fixed.partition('.')
    integer_groups = (len(integer)+3)//4
    padded = integer.rjust(integer_groups*4,'0')+fraction.ljust(((len(fraction)+3)//4)*4,'0')
    groups = [int(padded[index:index+4]) for index in range(0,len(padded),4)]
    weight = integer_groups-1
    while groups and groups[0]==0:
        groups.pop(0)
        weight -= 1
    while groups and groups[-1]==0:
        groups.pop()
    if not groups:
        weight = 0
        sign = 0
    if len(groups)>32767 or not -32768<=weight<=32767:
        raise FieldEncodingError('numeric precision unsupported')
    return struct.pack('>hhHH',len(groups),weight,0x4000 if sign else 0,scale)+b''.join(struct.pack('>H',group) for group in groups)


@dataclass(frozen=True,slots=True)
class ArrayField:
    values: tuple[bytes | None,...]
    dimensions: tuple[int,...]
    lower_bounds: tuple[int,...]
    has_null: int
    element_oid: int


def array_field(raw: bytes, typ: str) -> ArrayField:
    if len(raw)<12:
        raise FieldEncodingError('truncated array')
    ndims,has_null,oid = struct.unpack('>iiI',raw[:12])
    if not 0<=ndims<=6 or has_null not in (0,1) or oid!=OIDS[kind(typ[:-2])]:
        raise FieldEncodingError('invalid array header')
    offset = 12
    dims,bounds = [],[]
    count = 0 if ndims==0 else 1
    for _ in range(ndims):
        if offset+8>len(raw):
            raise FieldEncodingError('truncated array bounds')
        length,lower = struct.unpack('>ii',raw[offset:offset+8])
        if length<1:
            raise FieldEncodingError('invalid array dimension')
        dims.append(length)
        bounds.append(lower)
        count *= length
        offset += 8
    if count>len(raw)//4:
        raise FieldEncodingError('invalid array element count')
    values = []
    for _ in range(count):
        if offset+4>len(raw):
            raise FieldEncodingError('truncated array element')
        size = struct.unpack('>i',raw[offset:offset+4])[0]
        offset += 4
        if size==-1:
            if not has_null:
                raise FieldEncodingError('array NULL flag inconsistent')
            values.append(None)
        elif size<0 or offset+size>len(raw):
            raise FieldEncodingError('invalid array element size')
        else:
            values.append(raw[offset:offset+size])
            offset += size
    if offset!=len(raw):
        raise FieldEncodingError('trailing array bytes')
    return ArrayField(tuple(values),tuple(dims),tuple(bounds),has_null,oid)


def array_binary(values, dimensions, lower_bounds, has_null, element_oid) -> bytes:
    dimensions,lower_bounds = tuple(dimensions),tuple(lower_bounds)
    if len(dimensions)!=len(lower_bounds) or len(dimensions)>6:
        raise FieldEncodingError('array shape mismatch')
    expected = 0 if not dimensions else 1
    for length in dimensions:
        if length<1:
            raise FieldEncodingError('invalid array dimension')
        expected *= length
    if len(values)!=expected or has_null not in (0,1) or (any(value is None for value in values) and not has_null):
        raise FieldEncodingError('array multiplicity mismatch')
    return struct.pack('>iiI',len(dimensions),has_null,element_oid)+b''.join(
        struct.pack('>ii',length,lower) for length,lower in zip(dimensions,lower_bounds))+b''.join(
        struct.pack('>i',-1 if value is None else len(value))+(value or b'') for value in values)


def decode(raw: bytes | None, typ: str):
    if raw is None:
        return None
    value_kind = kind(typ)
    if value_kind.endswith('[]'):
        return array_field(raw,typ)
    widths = {'smallint':2,'integer':4,'bigint':8,'real':4,'double precision':8,
              'boolean':1,'date':4,'timestamp with time zone':8,'timestamp without time zone':8,'uuid':16}
    if value_kind in widths and len(raw)!=widths[value_kind]:
        raise FieldEncodingError('invalid field width')
    if value_kind in ('smallint','integer','bigint'):
        return int.from_bytes(raw,'big',signed=True)
    if value_kind in ('real','double precision'):
        return struct.unpack('>f' if value_kind=='real' else '>d',raw)[0]
    if value_kind=='boolean':
        if raw not in (b'\x00',b'\x01'):
            raise FieldEncodingError('invalid boolean')
        return raw==b'\x01'
    if value_kind=='date':
        return date(2000,1,1)+timedelta(days=int.from_bytes(raw,'big',signed=True))
    if value_kind in ('timestamp with time zone','timestamp without time zone'):
        epoch = PG_EPOCH if value_kind=='timestamp with time zone' else PG_EPOCH.replace(tzinfo=None)
        return epoch+timedelta(microseconds=int.from_bytes(raw,'big',signed=True))
    if value_kind in ('uuid','bytea'):
        return raw
    if value_kind=='jsonb':
        if not raw or raw[0]!=1:
            raise FieldEncodingError('unsupported JSONB version')
        text = raw[1:].decode('utf-8')
        json.loads(text,parse_float=Decimal)
        return text
    if value_kind=='json':
        text = raw.decode('utf-8')
        json.loads(text,parse_float=Decimal)
        return text
    if value_kind=='text':
        return raw.decode('utf-8')
    if value_kind=='numeric':
        text = numeric_text(raw)
        return Decimal(text) if typ.startswith('numeric(') else text
    raise FieldEncodingError('unsupported field value')


def encode(value, typ: str) -> bytes | None:
    if value is None:
        return None
    value_kind = kind(typ)
    if value_kind in ('smallint','integer','bigint'):
        return int(value).to_bytes({'smallint':2,'integer':4,'bigint':8}[value_kind],'big',signed=True)
    if value_kind in ('real','double precision'):
        return struct.pack('>f' if value_kind=='real' else '>d',value)
    if value_kind=='boolean':
        return b'\x01' if value else b'\x00'
    if value_kind=='date':
        return ((value-date(2000,1,1)).days).to_bytes(4,'big',signed=True)
    if value_kind in ('timestamp with time zone','timestamp without time zone'):
        epoch = PG_EPOCH if value_kind=='timestamp with time zone' else PG_EPOCH.replace(tzinfo=None)
        delta = value-epoch
        micros = (delta.days*86400+delta.seconds)*1000000+delta.microseconds
        return micros.to_bytes(8,'big',signed=True)
    if value_kind=='uuid':
        return value.bytes if isinstance(value, UUID) else bytes(value)
    if value_kind=='bytea':
        return bytes(value)
    if value_kind=='jsonb':
        return b'\x01'+str(value).encode('utf-8')
    if value_kind in ('text','json'):
        return str(value).encode('utf-8')
    if value_kind=='numeric':
        return numeric_binary(value)
    raise FieldEncodingError('unsupported scalar inverse')
