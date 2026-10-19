"""Marked-DB + actual loopback HTTP DAV proof, no mocked repositories or success."""
import argparse
import asyncio
from datetime import date
import os
import time
from urllib.parse import unquote, urlsplit

from cryptography.fernet import Fernet
from icalendar import Calendar

from smoke_lifecycle import run
from app.calendar.sync_worker import CalendarSyncWorker, serialize_calendar_event
from dav_fixture import loopback_dav, fixture_http_client


def calendar_identity(payload):
    return [(str(e.get('UID','')),str(e.get('X-CEA-EVENT-ID',''))) for e in Calendar.from_ical(payload).walk() if e.name=='VEVENT']


async def exercise_calendar_sync(api, archives, pool, sink, database):
    os.environ['CALDAV_ALLOW_HTTP_TEST']='true'
    os.environ['CALENDAR_SYNC_ENCRYPTION_KEY']=Fernet.generate_key().decode()
    key = os.environ['CALENDAR_SYNC_ENCRYPTION_KEY']
    worker = CalendarSyncWorker(database)
    with loopback_dav() as (origin,state):
        target = origin+'/dav/calendars/fixture/growing/'
        connection_body = {'caldav_base_url':origin+'/dav/','username':'fixture',
                           'app_password':'fixture-only-password','target_calendar_url':target}
        # Deleted local events cannot become new resources during mirror-all.
        removed = await api.post('/api/calendar/events',json={'location':'Flower Room','title':'Deleted before rebuilt instance',
                                  'start_date':'2026-07-01'})
        assert removed.status_code==200,removed.text
        removed_id = removed.json()['numericId']
        deleted_uid = await pool.fetchval('SELECT ical_uid FROM calendar_event WHERE id=$1',removed_id)
        assert (await api.delete(f'/api/calendar/events/{removed_id}')).status_code==200
        identities_before = await pool.fetch('SELECT id,ical_uid FROM calendar_event WHERE deleted_at IS NULL ORDER BY id')
        foreign = serialize_calendar_event({'id':900000,'ical_uid':'unrelated-nextcloud-event','location':'Lab','title':'Not an SJCEA local event',
                                            'start_date':date(2026,7,1),'end_date':None,'notes':None})
        async with fixture_http_client() as dav:
            assert (await dav.put(target+'unrelated.ics',content=foreign,headers={'If-None-Match':'*'})).status_code==201
            tested = await api.post('/api/calendar/sync/connections/test',json={k:v for k,v in connection_body.items() if k!='target_calendar_url'})
            assert tested.status_code==200 and tested.json()[0]['name']=='Growing',tested.text
            saved = await api.post('/api/calendar/sync/connections',json=connection_body)
            assert saved.status_code==200 and not saved.json()['enabled'],saved.text
            connection = saved.json()
            assert 'credentials_encrypted' not in connection
            preview = await api.get(f"/api/calendar/sync/connections/{connection['id']}/mirror-preview")
            assert preview.status_code==200 and preview.json()['event_count']==len(identities_before)
            denied = await api.post(f"/api/calendar/sync/connections/{connection['id']}/reconnect",json={
                'expected_generation':connection['generation'],'backup_confirmed':False})
            assert denied.status_code==400 and denied.json()['detail']['code']=='calendar_backup_required'
            mirrored = await api.post(f"/api/calendar/sync/connections/{connection['id']}/reconnect",json={
                'expected_generation':connection['generation'],'backup_confirmed':True})
            assert mirrored.status_code==200 and mirrored.json()['sync']['ok'],mirrored.text
            assert await pool.fetch('SELECT id,ical_uid FROM calendar_event WHERE deleted_at IS NULL ORDER BY id')==identities_before
            remote_ids = [entry for payload in state.calendars.values() for entry in calendar_identity(payload)]
            assert all(remote_ids.count((row['ical_uid'],str(row['id'])))==1 for row in identities_before)
            assert not any(uid==deleted_uid for uid,_ in remote_ids)
            assert state.calendars['/dav/calendars/fixture/growing/unrelated.ics']==foreign
            print('Explicit rebuilt mirror preserves IDs/UIDs, excludes deleted rows and unrelated remote events')
            started = time.monotonic()
            normal = await api.post('/api/calendar/events',json={'location':'Flower Room','title':'Normal & confirmed <event>',
                                    'notes':'Unicode 🌿 and a note & <angle>','start_date':'2026-07-02'})
            elapsed = time.monotonic()-started
            assert normal.status_code==200 and normal.json()['sync_status']=='synced' and elapsed<=5,normal.text
            event_id = normal.json()['numericId']
            assert not {'sync_lease_owner','sync_lease_until','sync_mirror_all'}.intersection(normal.json())
            print(f'Normal save remotely confirmed and persisted in {elapsed:.3f}s')
            state.accept_then_timeout=True
            uncertain = await api.post('/api/calendar/events',json={'location':'Veg Room','title':'Accepted but acknowledgement lost',
                                       'start_date':'2026-07-03'})
            assert uncertain.status_code==200,uncertain.text
            uncertain_id = uncertain.json()['numericId']
            await pool.execute("UPDATE calendar_event SET sync_next_attempt_at=clock_timestamp()-interval '1 second' WHERE id=$1",uncertain_id)
            recovered = await worker.sync_events([uncertain_id])
            assert recovered.ok,recovered.as_dict()
            uid = await pool.fetchval('SELECT ical_uid FROM calendar_event WHERE id=$1',uncertain_id)
            assert sum(identity[0]==uid for data in state.calendars.values() for identity in calendar_identity(data))==1
            print('Accept-then-lost-ack UID reconciliation creates exactly one resource')
            # A remote edit invalidates the known ETag; do not silently overwrite.
            row = await database.calendar_repo.get_event(event_id)
            remote = dict(row,title='Remote user content')
            changed = await dav.put(row['external_event_id'],content=serialize_calendar_event(remote),headers={'If-Match':row['external_etag']})
            assert changed.status_code==201
            conflict = await api.patch(f'/api/calendar/events/{event_id}',json={'title':'Newest durable local content'})
            assert conflict.status_code==200 and conflict.json()['sync_status']=='conflict',conflict.text
            assert conflict.json()['sync_error_code']=='calendar_remote_conflict'
            assert 'Remote user content' in state.calendars[unquote(urlsplit(row['external_event_id']).path)].decode()
            assert (await pool.fetchval('SELECT title FROM calendar_event WHERE id=$1',event_id))=='Newest durable local content'
            print('Different remote ETag/content blocks blind overwrite while retaining the local save')
            # Missing remote deletion must confirm absence, never create.
            row = await database.calendar_repo.get_event(uncertain_id)
            path = unquote(urlsplit(row['external_event_id']).path)
            state.calendars.pop(path)
            puts = state.counters.get('PUT',0)
            absence = await api.delete(f'/api/calendar/events/{uncertain_id}')
            assert absence.status_code==200 and absence.json()['event']['sync_status']=='synced',absence.text
            assert state.counters.get('PUT',0)==puts
            state.drop_delete_ack=True
            lost_delete = await api.delete(f'/api/calendar/events/{event_id}')
            assert lost_delete.status_code==200 and lost_delete.json()['event']['sync_status']=='synced',lost_delete.text
            print('Absent and lost-ack deletes reconcile without any create')
            # Missing/wrong encryption key is visibly unavailable, not plaintext.
            os.environ.pop('CALENDAR_SYNC_ENCRYPTION_KEY')
            unavailable = await api.post('/api/calendar/events',json={'location':'Veg Room','title':'Saved while key unavailable','start_date':'2026-07-04'})
            assert unavailable.status_code==200 and unavailable.json()['sync_status']=='error',unavailable.text
            assert unavailable.json()['sync_error_code']=='calendar_credentials_unavailable'
            os.environ['CALENDAR_SYNC_ENCRYPTION_KEY']=key
            # Atomic lease + older response CAS keep a newer local revision.
            leased = await database.calendar_repo.create_event({'location':'Veg Room','event_type':'planned_task','title':'Lease prior revision',
                'start_date':date(2026,7,5),'end_date':None})
            from uuid import uuid4
            owner = uuid4()
            claim = await database.calendar_repo.claim_sync_event(leased['id'],owner)
            assert claim is not None
            assert await database.calendar_repo.claim_sync_event(leased['id'],uuid4()) is None
            newest = await database.calendar_repo.update_event(leased['id'],{'title':'Lease newest revision'})
            assert newest['local_revision']>claim['local_revision']
            assert not await database.calendar_repo.complete_sync_event(claim,owner,target+'obsolete.ics','"obsolete"')
            assert (await database.calendar_repo.get_event(leased['id']))['title']=='Lease newest revision'
            assert (await worker.sync_events([leased['id']])).ok
            print('Foreground/background lease exclusivity and older-response revision CAS passed')
            # Real slow HTTP exceeds the common deadline after the server has
            # accepted the create; retry reconciles that resource without PUT.
            state.request_delay = 1.8
            started = time.monotonic()
            slow = await api.post('/api/calendar/events',json={'location':'Veg Room','title':'Durable slow confirmation','start_date':'2026-07-07'})
            elapsed = time.monotonic()-started
            state.request_delay = 0.0
            assert slow.status_code == 200 and elapsed <= 5.0, (elapsed,slow.text)
            assert slow.json()['sync_status'] == 'pending_push' and slow.json()['sync_error_code'] == 'calendar_sync_timeout',slow.text
            slow_id = slow.json()['numericId']
            await pool.execute("UPDATE calendar_event SET sync_next_attempt_at=clock_timestamp()-interval '1 second' WHERE id=$1",slow_id)
            puts_before = state.counters.get('PUT',0)
            assert (await worker.sync_events([slow_id])).ok
            assert state.counters.get('PUT',0) == puts_before
            print(f'Timeout save remains locally durable and recovers without another create ({elapsed:.3f}s)')
            async with asyncio.timeout(3):
                while state.active:
                    await asyncio.sleep(.05)
            # Multi-event work actually exercises the shared four-request bound.
            batch = [await database.calendar_repo.create_event({
                'location':'Veg Room','event_type':'planned_task','title':f'Bounded mirror {i}',
                'start_date':date(2026,7,8),'end_date':None}) for i in range(8)]
            assert (await worker.sync_events([row['id'] for row in batch])).ok
            assert 2 <= state.max_inflight <= 4,state.max_inflight
            # Run the production one-second due poller without control/hardware.
            from app.background_tasks.calendar import CalendarMixin
            class Poller(CalendarMixin):
                def __init__(self):
                    self.database = database
                    self._running = True
            background = Poller()
            queued = await database.calendar_repo.create_event({
                'location':'Veg Room','event_type':'planned_task','title':'One-second background due queue',
                'start_date':date(2026,7,9),'end_date':None})
            loop_task = asyncio.create_task(background._calendar_sync_loop())
            try:
                async with asyncio.timeout(3):
                    while (await database.calendar_repo.get_event(queued['id']))['sync_status'] != 'synced':
                        await asyncio.sleep(.05)
            finally:
                background._running = False
                await loop_task
            print('Production due poller confirms queued work with four-request concurrency')
            # Auth failures become blocked, not a one-second retry flood.
            state.auth_failure=True
            auth = await api.post('/api/calendar/events',json={'location':'Veg Room','title':'Locally durable auth failure','start_date':'2026-07-06'})
            assert auth.status_code==200 and auth.json()['sync_error_code']=='calendar_auth_failed',auth.text
            assert auth.json()['numericId'] not in [r['id'] for r in await database.calendar_repo.events_pending_sync()]
            state.auth_failure=False
            assert state.max_inflight<=4,state.max_inflight
            print('Auth blocking, bounded four-request concurrency and durable unavailable states passed')
            retry = await database.calendar_repo.create_event({
                'location':'Veg Room','event_type':'planned_task','title':'Bounded retry clock',
                'start_date':date(2026,7,10),'end_date':None})
            for attempt, delay in enumerate((1,5,30,60,300,300),1):
                await pool.execute(
                    "UPDATE calendar_event SET sync_status='pending_push',sync_attempts=$2,sync_next_attempt_at=clock_timestamp()-interval '1 second' WHERE id=$1",
                    retry['id'],attempt-1)
                retry_owner = uuid4()
                retry_claim = await database.calendar_repo.claim_sync_event(retry['id'],retry_owner)
                await database.calendar_repo.fail_sync_event(retry_claim,retry_owner,'calendar_transport_failed')
                seconds = await pool.fetchval(
                    'SELECT EXTRACT(epoch FROM sync_next_attempt_at-clock_timestamp()) FROM calendar_event WHERE id=$1',retry['id'])
                assert delay-.2 <= float(seconds) <= delay,(attempt,seconds)
                assert retry['id'] not in [r['id'] for r in await database.calendar_repo.events_pending_sync()]
            pending_generation = await database.calendar_repo.create_event({
                'location':'Veg Room','event_type':'planned_task','title':'Old generation response',
                'start_date':date(2026,7,11),'end_date':None})
            generation_owner = uuid4()
            generation_claim = await database.calendar_repo.claim_sync_event(pending_generation['id'],generation_owner)
            current_generation = await pool.fetchval('SELECT generation FROM calendar_sync_connection WHERE id=$1',connection['id'])
            rebound = await database.calendar_repo.reconnect_and_mirror(connection['id'],current_generation)
            assert not await database.calendar_repo.complete_sync_event(
                generation_claim,generation_owner,target+'old-generation.ics','\"stale\"')
            assert (await database.calendar_repo.get_event(pending_generation['id']))['sync_generation']==rebound['generation']
            assert (await worker.sync_events(rebound['event_ids'])).ok
            print('Specified 1/5/30/60/300 retry timing and old-generation CAS fencing passed')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--database-url',required=True)
    args = parser.parse_args()
    asyncio.run(run(args.database_url,scenario=exercise_calendar_sync))


if __name__=='__main__':
    main()
