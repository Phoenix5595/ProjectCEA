"""Arrow arrays constructed from PG field bytes, with byte-exact inverse access."""
from __future__ import annotations

import json
import re

from .pg_fields import (
    FieldEncodingError, UNIX_OFFSET_DAYS, UNIX_OFFSET_US, array_binary, array_field,
    decode, encode, kind,
)


def arrow_type(pa, typ):
    value_kind = kind(typ)
    mapping = {'smallint':pa.int16(),'integer':pa.int32(),'bigint':pa.int64(),
               'real':pa.float32(),'double precision':pa.float64(),'boolean':pa.bool_(),
               'date':pa.date32(),'timestamp with time zone':pa.timestamp('us',tz='UTC'),
               'timestamp without time zone':pa.timestamp('us'),'uuid':pa.binary(16),
               'bytea':pa.binary(),'text':pa.string(),'json':pa.string(),'jsonb':pa.string()}
    if value_kind.endswith('[]'):
        return pa.list_(pa.field('element', arrow_type(pa,typ[:-2])))
    if value_kind=='numeric':
        precision_scale = re.fullmatch(r'numeric\((\d+),(\d+)\)',typ)
        if precision_scale:
            precision,scale = map(int,precision_scale.groups())
            if precision<=38:
                return pa.decimal128(precision,scale)
            if precision<=76:
                return pa.decimal256(precision,scale)
        return pa.string()
    return mapping[value_kind]


def auxiliary_fields(pa, columns):
    fields = []
    for column in columns:
        if kind(column['type']).endswith('[]'):
            name=column['name']
            fields.extend((pa.field(name+'__dimensions',pa.list_(pa.field('element',pa.int32()))),
                           pa.field(name+'__lower_bounds',pa.list_(pa.field('element',pa.int32()))),
                           pa.field(name+'__has_null',pa.int32()),pa.field(name+'__element_oid',pa.uint32())))
    return fields


def arrow_schema(pa, columns, source_descriptor, *, dataset_id, revision, source_table, record_role):
    names=[column['name'] for column in columns]
    if len(names)!=len(set(names)):
        raise FieldEncodingError('duplicate source column')
    fields=[]
    for column in columns:
        typ=column['type']
        metadata={b'pg_type':typ.encode()}
        if kind(typ)=='uuid':
            metadata[b'logical_type']=b'uuid'
        if kind(typ) in ('json','jsonb'):
            metadata[b'logical_type']=typ.encode()
        if kind(typ)=='numeric' and arrow_type(pa,typ)==pa.string():
            metadata[b'pg_numeric_encoding']=b'exact-decimal-text'
        fields.append(pa.field(column['name'],arrow_type(pa,typ),nullable=not column.get('not_null',False),metadata=metadata))
    aux=auxiliary_fields(pa,columns)
    if set(names).intersection(field.name for field in aux):
        raise FieldEncodingError('source auxiliary name collision')
    return pa.schema(fields+aux,metadata={
        b'sjcea.source_schema':json.dumps(source_descriptor,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode(),
        b'sjcea.dataset_id':str(dataset_id).encode(),b'sjcea.revision':str(revision).encode(),
        b'sjcea.source_table':source_table.encode(),b'sjcea.record_role':record_role.encode(),
    })


def scalar_array(pa, raw_values, typ):
    value_kind=kind(typ)
    target_type=arrow_type(pa,typ)
    widths={'real':4,'double precision':8,'date':4,'timestamp with time zone':8,'timestamp without time zone':8}
    if value_kind not in widths:
        values=[decode(raw,typ) for raw in raw_values]
        # Unbounded numeric remains annotated text; bounded decimal is exact.
        if value_kind=='numeric' and target_type==pa.string():
            values=[None if value is None else str(value) for value in values]
        return pa.array(values,type=target_type)
    width=widths[value_kind]
    validity=bytearray((len(raw_values)+7)//8)
    data=bytearray(len(raw_values)*width)
    null_count=0
    for index,raw in enumerate(raw_values):
        if raw is None:
            null_count+=1
            continue
        if len(raw)!=width:
            raise FieldEncodingError('invalid scalar width')
        validity[index//8] |= 1 << (index%8)
        if value_kind in ('real','double precision'):
            encoded=raw[::-1]
        else:
            value=int.from_bytes(raw,'big',signed=True)
            sentinel=(2**(8*width-1)-1,-2**(8*width-1))
            if value in sentinel:
                raise FieldEncodingError('infinite temporal field unsupported')
            value+=UNIX_OFFSET_DAYS if value_kind=='date' else UNIX_OFFSET_US
            encoded=value.to_bytes(width,'little',signed=True)
        data[index*width:(index+1)*width]=encoded
    return pa.Array.from_buffers(target_type,len(raw_values),[pa.py_buffer(validity),pa.py_buffer(data)],null_count=null_count)


def table_from_rows(pa, binary_rows, columns, schema):
    arrays=[]
    aux={}
    for index,column in enumerate(columns):
        typ=column['type']; name=column['name']
        raw_values=[row[index] for row in binary_rows]
        if not kind(typ).endswith('[]'):
            arrays.append(scalar_array(pa,raw_values,typ))
            continue
        parsed=[None if raw is None else array_field(raw,typ) for raw in raw_values]
        offsets=[0]; flat=[]; mask=[]
        for value in parsed:
            mask.append(value is None)
            if value is not None:
                flat.extend(value.values)
            offsets.append(len(flat))
        values=scalar_array(pa,flat,typ[:-2])
        arrays.append(pa.ListArray.from_arrays(pa.array(offsets,type=pa.int32()),values,mask=pa.array(mask,type=pa.bool_())))
        aux[name+'__dimensions']=pa.array([None if value is None else list(value.dimensions) for value in parsed],type=pa.list_(pa.int32()))
        aux[name+'__lower_bounds']=pa.array([None if value is None else list(value.lower_bounds) for value in parsed],type=pa.list_(pa.int32()))
        aux[name+'__has_null']=pa.array([None if value is None else value.has_null for value in parsed],type=pa.int32())
        aux[name+'__element_oid']=pa.array([None if value is None else value.element_oid for value in parsed],type=pa.uint32())
    arrays.extend(aux[field.name] for field in list(schema)[len(columns):])
    return pa.Table.from_arrays(arrays,schema=schema)


def scalar_binary(array, index, typ):
    if not array[index].is_valid:
        return None
    value_kind=kind(typ)
    if value_kind in ('real','double precision','date','timestamp with time zone','timestamp without time zone'):
        width=4 if value_kind in ('real','date') else 8
        offset=(array.offset+index)*width
        raw=bytes(memoryview(array.buffers()[1])[offset:offset+width])
        if value_kind in ('real','double precision'):
            return raw[::-1]
        value=int.from_bytes(raw,'little',signed=True)-(UNIX_OFFSET_DAYS if value_kind=='date' else UNIX_OFFSET_US)
        return value.to_bytes(width,'big',signed=True)
    return encode(array[index].as_py(),typ)


def binary_rows_from_batch(batch, columns):
    arrays=[batch.column(index) for index in range(len(columns))]
    for row_index in range(batch.num_rows):
        fields=[]
        for index,column in enumerate(columns):
            typ=column['type']; name=column['name']; array=arrays[index]
            if not kind(typ).endswith('[]'):
                fields.append(scalar_binary(array,row_index,typ))
            elif not array[row_index].is_valid:
                fields.append(None)
            else:
                offsets=array.offsets
                start,end=offsets[row_index].as_py(),offsets[row_index+1].as_py()
                values=[scalar_binary(array.values,value_index,typ[:-2]) for value_index in range(start,end)]
                def aux(suffix):
                    return batch.column(batch.schema.get_field_index(name+suffix))[row_index].as_py()
                fields.append(array_binary(values,aux('__dimensions'),aux('__lower_bounds'),aux('__has_null'),aux('__element_oid')))
        yield tuple(fields)
