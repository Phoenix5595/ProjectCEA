"""Real ZIP graph parity and clipped-edge regression for the guarded fixture."""
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

from grow_archive_service.api_models import ArchiveGraphResponse
from grow_archive_service.graphs import ArchiveGraphReader
from grow_archive_service.membership import CaptureWindow, selection_sql, window_arrays
from grow_archive_service.models import ArchiveError
from grow_archive_service.schema import load_source_catalog


async def assert_graph_reader_parity(pool, heavy, root, dataset_id, start, end, path, manifest):
    reader = ArchiveGraphReader(pool, heavy.run, root)
    pin = SimpleNamespace(
        load_id=uuid4(), dataset_id=dataset_id, revision=manifest.revision,
        source_kind="zip", member_prefix=None, local_path=path, manifest=manifest,
    )
    stream = load_source_catalog().by_name()["measurement"]
    predicate, role = selection_sql(stream)
    windows = (CaptureWindow("Flower Room", start, end),)
    window_args = window_arrays(windows)
    ranges = [
        (start, end, 200),
        (start + timedelta(hours=12, microseconds=500),
         start + timedelta(hours=12, seconds=1, microseconds=3500), 10),
        (start + timedelta(hours=11, minutes=59, seconds=37),
         start + timedelta(hours=12, minutes=6, seconds=13), 10),
    ]
    for lower, upper, budget in ranges:
        archived = ArchiveGraphResponse.model_validate(
            await reader.read(pin, start=lower, end=upper, max_points=budget))
        assert archived.tier == 'detailed'
        async with pool.acquire() as conn:
            expected = await conn.fetch(
                "SELECT s.name,count(m.value)::bigint AS sample_count,avg(m.value::numeric) AS average,"
                "min(m.value::numeric)::double precision AS minimum,max(m.value::numeric)::double precision AS maximum "
                "FROM public.measurement m JOIN sensor s USING(sensor_id) "
                f"WHERE {predicate} AND {role}='observation' AND m.time>=$4 AND m.time<$5 "
                "AND (s.name LIKE '%_b' OR s.name LIKE '%_f') "
                "GROUP BY s.name HAVING count(m.value)>0",
                *window_args, lower, upper,
            )
        statistics = {item.sensor: item for item in archived.sensors.statistics}
        assert set(statistics) == {item['name'] for item in expected}
        for expected_stat in expected:
            actual = statistics[expected_stat['name']]
            assert actual.sample_count == expected_stat['sample_count'], 'lost/duplicated clipped samples'
            assert abs(actual.average - float(expected_stat['average'])) < 1e-12
            assert actual.minimum == expected_stat['minimum']
            assert actual.maximum == expected_stat['maximum']
        for series in archived.sensors.series:
            assert len(series.points) <= budget, 'graph exceeded requested point budget'
            assert sum(point.sample_count for point in series.points) == statistics[series.sensor].sample_count
    try:
        await reader.read(pin, start=start, end=start + timedelta(microseconds=999999), max_points=10)
    except ArchiveError as error:
        assert error.code == 'archive_graph_range_invalid' and error.status_code == 400
    else:
        raise AssertionError('archive graph accepted a sub-second range')

    final_zip = ArchiveGraphResponse.model_validate(
        await reader.read(pin, start=start, end=end, max_points=200))
    async with pool.acquire() as conn:
        expected_whole_count = await conn.fetchval(
            "SELECT count(m.value) FROM public.measurement m JOIN sensor s USING(sensor_id) "
            f"WHERE {predicate} AND {role}='observation' AND m.time>=$4 AND m.time<$5 "
            "AND (s.name LIKE '%_b' OR s.name LIKE '%_f')",
            *window_args, start, end,
        )
    assert sum(item.sample_count for item in final_zip.sensors.statistics) == expected_whole_count
    print('Actual archive graph proof: raw-source/ZIP parity, raw and clipped edges, point budget')
