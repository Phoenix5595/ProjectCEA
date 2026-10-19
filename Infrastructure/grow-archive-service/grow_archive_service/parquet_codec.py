"""Bounded typed Parquet/ZIP history codec with exact PG binary-row verification."""
from __future__ import annotations

from collections.abc import Iterator, Mapping
from datetime import datetime
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
from uuid import UUID
import zipfile
from zoneinfo import ZoneInfo

from .arrow_fields import arrow_schema, binary_rows_from_batch, table_from_rows
from .binary_codec import BinaryCopyError, canonical, rows
from .manifest import ArchiveManifest
from .pg_fields import FieldEncodingError, decode, kind

FORMAT='sj-cea-history/1'
MAX_MANIFEST=16*1024*1024
MAX_MEMBER=8*1024*1024*1024
MAX_TOTAL=64*1024*1024*1024
MAX_ROWS=4096
MAX_BATCH_BYTES=8*1024*1024
TORONTO=ZoneInfo('America/Toronto')
_CREDENTIAL=re.compile(r'(?:^|[_-])(?:password|passwd|credentials?|secret|api[_-]?key|app[_-]?password|authorization|encryption[_-]?key|access[_-]?token|refresh[_-]?token)(?:$|[_-])',re.I)


class ArchiveIntegrityError(ValueError):
    code='archive_integrity_failed'


def _arrow():
    import pyarrow as pa
    import pyarrow.parquet as pq
    if pa.__version__!='19.0.0':
        raise ArchiveIntegrityError('unsupported Arrow codec version')
    return pa,pq


def _json_bytes(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()


def _json_safe(value):
    if isinstance(value,Mapping):
        for key,child in value.items():
            if _CREDENTIAL.search(str(key)):
                raise ArchiveIntegrityError('credential-bearing context is not exportable')
            _json_safe(child)
    elif isinstance(value,(list,tuple)):
        for child in value:
            _json_safe(child)


def _safe_member(name):
    if not isinstance(name,str) or not name or '\\' in name or '\x00' in name or name.startswith('/'):
        return False
    if any(part in ('','.','..') for part in name.split('/')) or re.match(r'^[A-Za-z]:',name):
        return False
    return True


def _hash_file(path):
    digest=hashlib.sha256()
    with Path(path).open('rb') as stream:
        while block:=stream.read(1024*1024):
            digest.update(block)
    return digest.hexdigest()


def _columns(descriptor,partition_field=False):
    original=descriptor.get('columns')
    if not isinstance(original,list) or any(not isinstance(column,dict) for column in original):
        raise ArchiveIntegrityError('ordered source descriptor missing')
    for column in original:
        if not re.fullmatch(r'[a-z_][a-z0-9_]*',column.get('name','')):
            raise ArchiveIntegrityError('invalid source column name')
        if _CREDENTIAL.search(column['name']):
            raise ArchiveIntegrityError('credential-bearing column is not exportable')
        kind(column['type'])
    metadata=[{'name':'dataset_id','type':'uuid','not_null':True},
              {'name':'storage_revision','type':'bigint','not_null':True},
              {'name':'source_occurrence','type':'integer','not_null':True},
              {'name':'record_role','type':'text','not_null':True}]
    if partition_field:
        metadata.append({'name':'_archive_partition_time','type':'timestamp with time zone','not_null':True})
    return original+metadata


def _part_member(part,index):
    table=part['source_table']; role=part['record_role']
    if not re.fullmatch(r'[a-z_][a-z0-9_]*',table):
        raise ArchiveIntegrityError('invalid stream name')
    if role=='observation':
        day=part.get('toronto_date')
        if not isinstance(day,str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}',day):
            raise ArchiveIntegrityError('observation partition date missing')
        datetime.strptime(day,'%Y-%m-%d')
        member=f'raw/{table}/{day}/part-{index:05d}.parquet'
    elif role=='context':
        member=f'context/{table}-part-{index:05d}.parquet'
    elif role=='graph' and part.get('graph_kind') in ('sensor','light','photoperiod','coverage'):
        member=f"graph/{part['graph_kind']}/part-{index:05d}.parquet"
    else:
        raise ArchiveIntegrityError('invalid record role')
    if part.get('member') is not None and part['member']!=member:
        raise ArchiveIntegrityError('arbitrary archive member path forbidden')
    return member


def _validate_row(binary,columns,descriptor,dataset_id,revision,role,day):
    original_count=len(descriptor['columns'])
    if len(binary)!=len(columns):
        raise ArchiveIntegrityError('row column count changed')
    if binary[original_count]!=dataset_id.bytes or decode(binary[original_count+1],'bigint')!=revision:
        raise ArchiveIntegrityError('row dataset/revision mismatch')
    ordinal=decode(binary[original_count+2],'integer')
    if ordinal is None or ordinal<0 or decode(binary[original_count+3],'text')!=role:
        raise ArchiveIntegrityError('row role/occurrence mismatch')
    for index,column in enumerate(columns):
        raw=binary[index]
        if raw is None and column.get('not_null',False):
            raise ArchiveIntegrityError('source nullability changed')
        if raw is not None and kind(column['type']) in ('json','jsonb'):
            _json_safe(json.loads(decode(raw,column['type']),parse_float=Decimal))
    time_column=descriptor.get('time_column')
    if role=='observation' and time_column:
        position=next((index for index,column in enumerate(columns) if column['name']==time_column),None)
        if position is None or binary[position] is None:
            raise ArchiveIntegrityError('observation time missing')
        observed=decode(binary[position],columns[position]['type'])
        if observed.tzinfo is None or observed.astimezone(TORONTO).date().isoformat()!=day:
            raise ArchiveIntegrityError('row lies outside declared Toronto day')


def iter_write_archive(dataset_id: UUID,revision: int,destination: Path,*,checkpoint=None)->Iterator[dict]:
    pa,pq=_arrow()
    destination=Path(destination)
    capture=destination.parent/'capture'
    draft=json.loads((capture/'metadata.json').read_text(encoding='utf-8'))
    if draft.get('dataset_id')!=str(dataset_id) or draft.get('revision')!=revision or revision<1:
        raise ArchiveIntegrityError('capture identity mismatch')
    _json_safe({key:value for key,value in draft.items() if key!='parts'})
    inventory=[]; observations={}; contexts={}; per_day={}; total_decoded=0
    destination.parent.mkdir(parents=True,exist_ok=True)
    temporary=destination.with_suffix(destination.suffix+'.tmp')
    try:
        with zipfile.ZipFile(temporary,'w',compression=zipfile.ZIP_STORED,allowZip64=True) as archive:
            for index,part in enumerate(draft.get('parts',[])):
                if checkpoint:
                    checkpoint()
                relative=part['path']
                unresolved=capture/relative
                if not _safe_member(relative) or not relative.endswith('.copy') or unresolved.is_symlink():
                    raise ArchiveIntegrityError('unsafe capture path')
                source=unresolved.resolve()
                if capture.resolve() not in source.parents:
                    raise ArchiveIntegrityError('capture path escapes staging')
                role=part['record_role']; table=part['source_table']; day=part.get('toronto_date')
                descriptor=(draft.get('graph_schema',{}) if role=='graph' else draft.get('source_schema',{})).get(table)
                if not isinstance(descriptor,dict):
                    raise ArchiveIntegrityError('source descriptor unavailable')
                columns=_columns(descriptor,bool(part.get('partition_field')))
                schema=arrow_schema(pa,columns,descriptor,dataset_id=dataset_id,revision=revision,source_table=table,record_role=role)
                member=_part_member(part,index)
                digest=hashlib.sha256(); row_count=0; decoded_bytes=0; batch=[]; batch_bytes=0
                with tempfile.NamedTemporaryFile(prefix='parquet-part-',suffix='.parquet',dir=destination.parent,delete=False) as handle:
                    parquet_path=Path(handle.name)
                try:
                    text_names=[column['name'] for column in columns if kind(column['type']) in ('text','json','jsonb')]
                    float_names=[column['name'] for column in columns if kind(column['type']) in ('real','double precision')]
                    with pq.ParquetWriter(parquet_path,schema,version='2.6',compression='zstd',compression_level=9,
                                          use_dictionary=text_names,use_byte_stream_split=float_names) as writer:
                        with source.open('rb') as stream:
                            for binary in rows(stream):
                                _validate_row(binary,columns,descriptor,dataset_id,revision,role,day)
                                encoded=canonical(binary)
                                if len(encoded)>MAX_BATCH_BYTES:
                                    raise ArchiveIntegrityError('source row exceeds bounded codec batch')
                                if batch and (len(batch)>=MAX_ROWS or batch_bytes+len(encoded)>MAX_BATCH_BYTES):
                                    arrow_table=table_from_rows(pa,batch,columns,schema)
                                    writer.write_table(arrow_table,row_group_size=MAX_ROWS)
                                    yield {'phase':'encode','member':member,'rows':row_count}
                                    batch=[]; batch_bytes=0
                                    if checkpoint:
                                        checkpoint()
                                digest.update(encoded); row_count+=1; decoded_bytes+=len(encoded)
                                batch.append(binary); batch_bytes+=len(encoded)
                            if batch:
                                writer.write_table(table_from_rows(pa,batch,columns,schema),row_group_size=MAX_ROWS)
                    if row_count!=part['row_count'] or digest.hexdigest()!=part['canonical_sha256']:
                        raise ArchiveIntegrityError('capture counts/binary digest mismatch')
                    # Validate the actual on-disk typed decode before publication.
                    inverse=hashlib.sha256(); inverse_count=0
                    for batch in pq.ParquetFile(parquet_path).iter_batches(batch_size=MAX_ROWS):
                        for binary in binary_rows_from_batch(batch,columns):
                            inverse.update(canonical(binary)); inverse_count+=1
                        yield {'phase':'decode','member':member,'rows':inverse_count}
                        if checkpoint:
                            checkpoint()
                    if inverse_count!=row_count or inverse.hexdigest()!=digest.hexdigest():
                        raise ArchiveIntegrityError('typed Parquet altered original field bytes/multiplicity')
                    file_bytes=parquet_path.stat().st_size
                    with parquet_path.open('rb') as input_file,archive.open(member,'w',force_zip64=True) as output_file:
                        while block:=input_file.read(1024*1024):
                            output_file.write(block)
                            if checkpoint:
                                checkpoint()
                            yield {'phase':'zip','member':member}
                    inventory.append({'path':member,'sha256':_hash_file(parquet_path),'byte_size':file_bytes,
                        'decoded_bytes':decoded_bytes,'row_count':row_count,'source_table':table,'record_role':role,
                        'toronto_date':day,'partition_field':bool(part.get('partition_field')),
                        'window_start':part.get('window_start'),'window_end':part.get('window_end'),
                        'schema_sha256':hashlib.sha256(schema.serialize().to_pybytes()).hexdigest(),'canonical_sha256':digest.hexdigest()})
                    total_decoded+=decoded_bytes
                    if role=='observation':
                        observations[table]=observations.get(table,0)+row_count
                        days=per_day.setdefault(table,{})
                        days[day]=days.get(day,0)+row_count
                    elif role=='context':
                        contexts[table]=contexts.get(table,0)+row_count
                finally:
                    parquet_path.unlink(missing_ok=True)
            final={key:value for key,value in draft.items() if key!='parts'}
            schema_document={'source_schema':draft.get('source_schema',{}),'graph_schema':draft.get('graph_schema',{})}
            schema_bytes=_json_bytes(schema_document)
            archive.writestr('schema.json',schema_bytes)
            inventory.append({'path':'schema.json','sha256':hashlib.sha256(schema_bytes).hexdigest(),
                              'byte_size':len(schema_bytes),'decoded_bytes':len(schema_bytes),'row_count':0,
                              'source_table':None,'record_role':'schema','schema_sha256':hashlib.sha256(schema_bytes).hexdigest(),
                              'canonical_sha256':hashlib.sha256(schema_bytes).hexdigest()})
            final.update(format=FORMAT,members=inventory,observation_counts=observations,context_counts=contexts,per_day_counts=per_day,
                         decoded_bytes=total_decoded+len(schema_bytes))
            _json_safe(final)
            manifest_bytes=_json_bytes(final)
            if len(manifest_bytes)>MAX_MANIFEST or final['decoded_bytes']>MAX_TOTAL:
                raise ArchiveIntegrityError('archive exceeds decoded metadata budget')
            archive.writestr('manifest.json',manifest_bytes)
        os.replace(temporary,destination)
        yield {'complete':True,'path':str(destination)}
    except (BinaryCopyError,FieldEncodingError,OverflowError,UnicodeError,ValueError) as error:
        temporary.unlink(missing_ok=True)
        if isinstance(error,ArchiveIntegrityError):
            raise
        raise ArchiveIntegrityError('unsupported or inconsistent binary input') from error


def _member_allowed(entry,manifest):
    path=entry['path']; role=entry['record_role']; table=entry.get('source_table')
    if path=='schema.json':
        return role=='schema'
    if not _safe_member(path) or not isinstance(table,str) or not re.fullmatch(r'[a-z_][a-z0-9_]*',table):
        return False
    if role=='observation':
        day=entry.get('toronto_date')
        return table in manifest.get('source_schema',{}) and isinstance(day,str) and re.fullmatch(
            rf'raw/{re.escape(table)}/{re.escape(day)}/part-\d+\.parquet',path) is not None
    if role=='context':
        return table in manifest.get('source_schema',{}) and re.fullmatch(rf'context/{re.escape(table)}-part-\d+\.parquet',path) is not None
    if role=='graph':
        return table in manifest.get('graph_schema',{}) and re.fullmatch(r'graph/(?:sensor|light|photoperiod|coverage)/part-\d+\.parquet',path) is not None
    return False


def iter_verify_archive(path: Path,expected_sha256: str|None=None,*,checkpoint=None)->Iterator[dict]:
    try:
        with zipfile.ZipFile(path,'r') as archive:
            entries=archive.infolist(); names=[entry.filename for entry in entries]
            if len(entries)>100000 or len(names)!=len(set(names)):
                raise ArchiveIntegrityError('duplicate/excessive ZIP inventory')
            for entry in entries:
                mode=stat.S_IFMT(entry.external_attr>>16)
                if not _safe_member(entry.filename) or mode==stat.S_IFLNK or entry.file_size>MAX_MEMBER:
                    raise ArchiveIntegrityError('unsafe ZIP member')
                if entry.compress_type!=zipfile.ZIP_STORED:
                    raise ArchiveIntegrityError('unsupported ZIP compression')
            if sum(entry.file_size for entry in entries)>MAX_TOTAL or 'manifest.json' not in names or 'schema.json' not in names:
                raise ArchiveIntegrityError('ZIP budget/required member mismatch')
            if archive.getinfo('manifest.json').file_size>MAX_MANIFEST or archive.getinfo('schema.json').file_size>MAX_MANIFEST:
                raise ArchiveIntegrityError('manifest/schema size limit exceeded')
            manifest=json.loads(archive.read('manifest.json'))
            _json_safe(manifest)
            from .annual_codec import is_annual_archive_manifest, iter_verify_annual_archive
            if is_annual_archive_manifest(manifest):
                yield from iter_verify_annual_archive(path, expected_sha256, checkpoint)
                return
            digest = hashlib.sha256()
            byte_size = 0
            with Path(path).open('rb') as source:
                while block := source.read(1024 * 1024):
                    digest.update(block)
                    byte_size += len(block)
                    if byte_size > MAX_TOTAL:
                        raise ArchiveIntegrityError('archive file exceeds limit')
                    if checkpoint:
                        checkpoint()
                    yield {'phase': 'archive_hash', 'bytes': byte_size}
            if expected_sha256 is not None and digest.hexdigest() != expected_sha256:
                raise ArchiveIntegrityError('whole ZIP hash mismatch')
            dataset_id=UUID(manifest['dataset_id']); revision=manifest['revision']
            if manifest.get('format')!=FORMAT or type(revision) is not int or revision<1 or manifest.get('timezone')!='America/Toronto':
                raise ArchiveIntegrityError('invalid archive identity/format')
            inventory=manifest.get('members')
            if not isinstance(inventory,list) or len({entry['path'] for entry in inventory})!=len(inventory):
                raise ArchiveIntegrityError('invalid member inventory')
            if set(names)!={'manifest.json',*(entry['path'] for entry in inventory)}:
                raise ArchiveIntegrityError('unlisted ZIP member')
            schema_document=json.loads(archive.read('schema.json'))
            if schema_document!={'source_schema':manifest.get('source_schema',{}),'graph_schema':manifest.get('graph_schema',{})}:
                raise ArchiveIntegrityError('schema metadata mismatch')
            pa,pq=_arrow(); observations={}; contexts={}; per_day={}; decoded_total=0
            for entry in inventory:
                if not _member_allowed(entry,manifest):
                    raise ArchiveIntegrityError('member allowlist mismatch')
                name=entry['path']; member_digest=hashlib.sha256(); member_size=0
                with archive.open(name) as member:
                    while block:=member.read(1024*1024):
                        member_digest.update(block); member_size+=len(block)
                        if checkpoint:
                            checkpoint()
                        yield {'phase':'member_hash','member':name,'bytes':member_size}
                if member_size!=entry['byte_size'] or member_digest.hexdigest()!=entry['sha256']:
                    raise ArchiveIntegrityError('member hash/count mismatch')
                if name=='schema.json':
                    if entry['row_count']!=0 or entry['decoded_bytes']!=member_size:
                        raise ArchiveIntegrityError('schema inventory mismatch')
                    decoded_total+=member_size
                    continue
                role=entry['record_role']; table=entry['source_table']; day=entry.get('toronto_date')
                descriptor=(manifest.get('graph_schema',{}) if role=='graph' else manifest.get('source_schema',{}))[table]
                columns=_columns(descriptor,entry.get('partition_field',False))
                expected_schema=arrow_schema(pa,columns,descriptor,dataset_id=dataset_id,revision=revision,source_table=table,record_role=role)
                inverse=hashlib.sha256(); count=0; decoded_size=0
                with archive.open(name) as member:
                    parquet=pq.ParquetFile(member)
                    if not parquet.schema_arrow.equals(expected_schema,check_metadata=True) or hashlib.sha256(parquet.schema_arrow.serialize().to_pybytes()).hexdigest()!=entry['schema_sha256']:
                        raise ArchiveIntegrityError('typed ordered schema mismatch')
                    if parquet.metadata.num_rows!=entry['row_count']:
                        raise ArchiveIntegrityError('Parquet metadata count mismatch')
                    for group in range(parquet.num_row_groups):
                        metadata=parquet.metadata.row_group(group)
                        if metadata.num_rows>65536 or metadata.total_byte_size>64*1024*1024:
                            raise ArchiveIntegrityError('Parquet row group budget exceeded')
                    for batch in parquet.iter_batches(batch_size=MAX_ROWS):
                        for binary in binary_rows_from_batch(batch,columns):
                            _validate_row(binary,columns,descriptor,dataset_id,revision,role,day)
                            encoded=canonical(binary); inverse.update(encoded); decoded_size+=len(encoded); count+=1
                        if checkpoint:
                            checkpoint()
                        yield {'phase':'binary_roundtrip','member':name,'rows':count}
                if count!=entry['row_count'] or decoded_size!=entry['decoded_bytes'] or inverse.hexdigest()!=entry['canonical_sha256']:
                    raise ArchiveIntegrityError('original field bytes or multiplicity changed')
                decoded_total+=decoded_size
                if decoded_total>MAX_TOTAL:
                    raise ArchiveIntegrityError('decoded row budget exceeded')
                if role=='observation':
                    observations[table]=observations.get(table,0)+count
                    days=per_day.setdefault(table,{})
                    days[day]=days.get(day,0)+count
                elif role=='context':
                    contexts[table]=contexts.get(table,0)+count
            if observations!=manifest['observation_counts'] or contexts!=manifest['context_counts'] or per_day!=manifest['per_day_counts'] or decoded_total!=manifest['decoded_bytes']:
                raise ArchiveIntegrityError('table/day observation totals changed')
            yield {'complete':True,'sha256':digest.hexdigest(),'byte_size':byte_size,'manifest':manifest}
    except (KeyError,TypeError,ValueError,OverflowError,UnicodeError,zipfile.BadZipFile,RuntimeError) as error:
        if isinstance(error,ArchiveIntegrityError):
            raise
        raise ArchiveIntegrityError('invalid archive structure or typed data') from error


def write_archive(dataset_id: UUID,revision: int,destination: Path)->ArchiveManifest:
    for _ in iter_write_archive(dataset_id,revision,destination):
        pass
    return verify_archive(destination)


def verify_archive(path: Path,expected_sha256: str|None=None,*,checkpoint=None)->ArchiveManifest:
    result=None
    for step in iter_verify_archive(path,expected_sha256,checkpoint=checkpoint):
        if step.get('complete'):
            result=step
    if result is None:
        raise ArchiveIntegrityError('verification incomplete')
    return ArchiveManifest(result['manifest'],result['sha256'],result['byte_size'])


def iter_archive_binary_rows(
    path: Path, verified: ArchiveManifest, source_table: str, *,
    component_dataset_id: UUID | None = None, checkpoint=None,
) -> Iterator[list[tuple[bytes | None, ...]]]:
    """Stream original typed row fields from a verified, pinned ZIP.

    Recheck schemas and canonical member digests while reading. Callers must
    finish the iterator before publishing results; a changed/corrupt member
    fails rather than supplying a partial replacement.
    """
    from .annual_codec import is_annual_archive_manifest, iter_annual_binary_rows
    manifest = verified.data
    if is_annual_archive_manifest(manifest):
        if component_dataset_id is None:
            raise ArchiveIntegrityError('annual reads require a component dataset')
        yield from iter_annual_binary_rows(
            path, verified, source_table,
            component_dataset_id=component_dataset_id, checkpoint=checkpoint,
        )
        return
    if component_dataset_id is not None and component_dataset_id != UUID(verified.dataset_id):
        raise ArchiveIntegrityError('single-dataset archive identity mismatch')
    pa, pq = _arrow()
    if Path(path).stat().st_size != verified.byte_size:
        raise ArchiveIntegrityError('verified ZIP file size changed')
    dataset_id = UUID(verified.dataset_id)
    with zipfile.ZipFile(path, 'r') as archive:
        for entry in manifest['members']:
            if entry.get('source_table') != source_table or entry['record_role'] not in ('observation', 'context'):
                continue
            if not _member_allowed(entry, manifest):
                raise ArchiveIntegrityError('member allowlist changed')
            descriptor = manifest['source_schema'][source_table]
            columns = _columns(descriptor, entry.get('partition_field', False))
            schema = arrow_schema(
                pa, columns, descriptor, dataset_id=dataset_id,
                revision=verified.revision, source_table=source_table,
                record_role=entry['record_role'],
            )
            digest = hashlib.sha256()
            count = 0
            size = 0
            buffered = []
            buffered_size = 0
            with archive.open(entry['path']) as member:
                parquet = pq.ParquetFile(member)
                if not parquet.schema_arrow.equals(schema, check_metadata=True):
                    raise ArchiveIntegrityError('verified member schema changed')
                for batch in parquet.iter_batches(batch_size=MAX_ROWS):
                    for binary in binary_rows_from_batch(batch, columns):
                        _validate_row(binary, columns, descriptor, dataset_id, verified.revision,
                                      entry['record_role'], entry.get('toronto_date'))
                        encoded = canonical(binary)
                        if len(encoded) > MAX_BATCH_BYTES:
                            raise ArchiveIntegrityError('row exceeds streaming budget')
                        if buffered and (len(buffered) >= MAX_ROWS or buffered_size + len(encoded) > MAX_BATCH_BYTES):
                            yield buffered
                            buffered = []
                            buffered_size = 0
                        buffered.append(binary)
                        buffered_size += len(encoded)
                        digest.update(encoded)
                        size += len(encoded)
                        count += 1
                    if checkpoint:
                        checkpoint()
            if count != entry['row_count'] or size != entry['decoded_bytes'] or digest.hexdigest() != entry['canonical_sha256']:
                raise ArchiveIntegrityError('verified member bytes or multiplicity changed')
            if buffered:
                yield buffered
