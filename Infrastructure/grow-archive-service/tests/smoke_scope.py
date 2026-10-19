"""Real HTTP proof for dated attribution and closed-grow bypass protection."""
from datetime import timedelta


async def exercise_scope_corrections(client, conn):
    source = await conn.fetchrow(
        'SELECT m.sensor_id,min(m.time) AS start,max(m.time) AS finish,r.name AS room '
        'FROM measurement m JOIN sensor s USING(sensor_id) JOIN device d USING(device_id) '
        'JOIN rack k USING(rack_id) JOIN room r USING(room_id) '
        'GROUP BY m.sensor_id,r.name ORDER BY m.sensor_id LIMIT 1')
    assert source is not None
    start, end = source['start']-timedelta(seconds=1),source['finish']+timedelta(seconds=1)
    before = await conn.fetch('SELECT record_send(m) AS payload FROM measurement m WHERE sensor_id=$1 ORDER BY time',source['sensor_id'])
    body = {'dataset_id':None,'expected_revision':None,'source_table':'measurement',
            'source_identity':{'sensor_id':source['sensor_id']},'start':start.isoformat(),
            'end':end.isoformat(),'room':source['room']}
    first = await client.post('/api/archives/scope-corrections',json=body)
    assert first.status_code == 200,first.text
    repeated = await client.post('/api/archives/scope-corrections',json=body)
    assert repeated.status_code == 200 and repeated.json()['override_id']==first.json()['override_id']
    overlap = await client.post('/api/archives/scope-corrections',json={**body,'end':(end+timedelta(seconds=1)).isoformat()})
    assert overlap.status_code == 409 and overlap.json()['detail']['code']=='archive_scope_overlap'
    reversed_range = await client.post('/api/archives/scope-corrections',json={**body,'start':end.isoformat(),'end':start.isoformat()})
    assert reversed_range.status_code == 400
    bad_identity = await client.post('/api/archives/scope-corrections',json={**body,'source_identity':{'sensor_id':True}})
    assert bad_identity.status_code == 422
    after = await conn.fetch('SELECT record_send(m) AS payload FROM measurement m WHERE sensor_id=$1 ORDER BY time',source['sensor_id'])
    assert before == after
    print('Dated assignment HTTP idempotence/partial-overlap/strict identity/raw preservation passed')


async def assert_scope_closed_guard(client, *, sensor_id, start, end):
    response = await client.post('/api/archives/scope-corrections',json={
        'dataset_id':None,'expected_revision':None,'source_table':'measurement',
        'source_identity':{'sensor_id':sensor_id},'start':start.isoformat(),
        'end':end.isoformat(),'room':'Flower Room'})
    assert response.status_code == 409 and response.json()['detail']['code']=='grow_closed',response.text
    print('Null dataset scope correction cannot bypass closed-grow fence')
