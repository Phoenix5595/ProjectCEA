"""Exercise annual typed payloads and rejection boundaries without DB/network IO."""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from datetime import datetime
import hashlib
import json
from pathlib import Path
import tempfile
from uuid import uuid4
import zipfile
from zoneinfo import ZoneInfo

from grow_archive_service.annual import AnnualComponentInput, build_annual_zip
from grow_archive_service.annual_codec import annual_component
from grow_archive_service.binary_codec import canonical, write_header, write_row, write_trailer
from grow_archive_service.parquet_codec import (
    ArchiveIntegrityError, iter_archive_binary_rows, verify_archive, write_archive,
)
from grow_archive_service.pg_fields import encode

TORONTO = ZoneInfo('America/Toronto')


def expect_integrity_error(action):
    try:
        action()
    except ArchiveIntegrityError:
        return
    raise AssertionError('malformed archive was accepted')


def main():
    with tempfile.TemporaryDirectory(prefix='sj-annual-codec-') as temporary:
        root = Path(temporary)
        components = []
        expected = {}
        descriptor = {'time_column': 'time', 'columns': [
            {'name': 'time', 'type': 'timestamp with time zone', 'not_null': False},
            {'name': 'sensor_id', 'type': 'integer', 'not_null': True},
            {'name': 'value', 'type': 'double precision', 'not_null': False},
        ]}
        for quarter in range(1, 5):
            dataset_id = uuid4()
            start = datetime(2024, 1 + 3 * (quarter - 1), 1, tzinfo=TORONTO)
            end = datetime(2025, 1, 1, tzinfo=TORONTO) if quarter == 4 else datetime(
                2024, 1 + 3 * quarter, 1, tzinfo=TORONTO)
            directory = root / f'q{quarter}'
            capture = directory / 'capture'
            capture.mkdir(parents=True)
            observation = (encode(start, 'timestamptz'), encode(quarter, 'integer'),
                           bytes.fromhex('8000000000000000'))
            context = (None, encode(quarter, 'integer'), None)
            parts = []
            all_rows = []
            for role, original_rows in [('observation', [observation, observation]),
                                        ('context', [context])]:
                path = capture / f'{role}.copy'
                digest = hashlib.sha256()
                with path.open('wb') as output:
                    write_header(output)
                    for occurrence, original in enumerate(original_rows):
                        fields = original + (dataset_id.bytes, encode(3, 'bigint'),
                                             encode(occurrence, 'integer'), encode(role, 'text'))
                        write_row(output, fields)
                        digest.update(canonical(fields))
                        all_rows.append(fields)
                    write_trailer(output)
                part = {'source_table': 'measurement', 'record_role': role, 'path': path.name,
                        'row_count': len(original_rows), 'canonical_sha256': digest.hexdigest()}
                if role == 'observation':
                    part['toronto_date'] = start.date().isoformat()
                parts.append(part)
            draft = {'dataset_id': str(dataset_id), 'revision': 3, 'calendar_revision': 2,
                     'calendar_year': 2024, 'quarter': quarter, 'kind': 'veg_quarter',
                     'room': 'Veg Room', 'timezone': 'America/Toronto',
                     'available_from': start.isoformat(), 'available_to': end.isoformat(),
                     'coverage': 'complete', 'gaps': [], 'absent_streams': [],
                     'source_schema': {'measurement': descriptor}, 'graph_schema': {}, 'parts': parts}
            (capture / 'metadata.json').write_text(json.dumps(draft))
            source = directory / 'quarter.zip'
            verified = write_archive(dataset_id, 3, source)
            components.append(AnnualComponentInput(dataset_id, 3, f'Q{quarter}/', source, '',
                                                   verified.sha256, verified.byte_size, verified.data))
            expected[dataset_id] = Counter(map(canonical, all_rows))
        destination = root / 'annual.zip'
        build_annual_zip(uuid4(), 1, calendar_revision=2, calendar_year=2024,
                         room='Veg Room', components=components, destination=destination)
        verified = verify_archive(destination)
        for index, component in enumerate(components, 1):
            child, prefix = annual_component(verified, component.dataset_id)
            assert prefix == f'Q{index}/' and child['revision'] == 3
            actual = Counter(canonical(row) for batch in iter_archive_binary_rows(
                destination, verified, 'measurement', component_dataset_id=component.dataset_id)
                             for row in batch)
            assert actual == expected[component.dataset_id], 'field bytes/multiplicity changed'
        expect_integrity_error(lambda: list(iter_archive_binary_rows(destination, verified, 'measurement')))
        expect_integrity_error(lambda: list(iter_archive_binary_rows(
            destination, verified, 'measurement', component_dataset_id=uuid4())))
        with zipfile.ZipFile(destination) as original:
            entries = [(entry.filename, original.read(entry)) for entry in original.infolist()]
        for case in ('wrong_child_revision', 'path_traversal', 'changed_payload', 'duplicate_prefix'):
            malformed = root / f'{case}.zip'
            manifest = deepcopy(verified.data)
            if case == 'wrong_child_revision':
                manifest['components'][0]['revision'] += 1
            elif case == 'duplicate_prefix':
                manifest['components'][1]['member_prefix'] = 'Q1/'
            with zipfile.ZipFile(malformed, 'w', compression=zipfile.ZIP_STORED) as output:
                for name, payload in entries:
                    if name == 'manifest.json':
                        payload = json.dumps(manifest, sort_keys=True, separators=(',', ':'),
                                             ensure_ascii=False, allow_nan=False).encode()
                    elif case == 'changed_payload' and name.endswith('.parquet') and name.startswith('Q2/'):
                        payload = payload[:-1] + bytes([payload[-1] ^ 1])
                    output.writestr(name, payload)
                if case == 'path_traversal':
                    output.writestr('../escape', b'bad')
            expect_integrity_error(lambda: verify_archive(malformed))
        expect_integrity_error(lambda: verify_archive(destination, '0' * 64))
        print(json.dumps({'result': 'PASS', 'quarters': 4, 'rows': 12,
                          'archive_bytes': verified.byte_size, 'sha256': verified.sha256,
                          'cases': ['typed_bytes', 'duplicate_multiplicity', 'negative_zero',
                                    'null_time_context', 'Toronto_DST_quarters', 'missing_child',
                                    'unknown_child', 'wrong_child_revision', 'duplicate_prefix',
                                    'path_traversal', 'changed_payload', 'wrong_whole_hash']}))


if __name__ == '__main__':
    main()
