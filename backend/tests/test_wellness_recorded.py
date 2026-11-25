"""Synthetic immutable events, real SQLite projections; no model/user archive."""
import copy
import uuid
import pytest
from app.services.homepulse.store import HomePulseStore

START=1_600_000_000

@pytest.fixture
def archive(tmp_path):return HomePulseStore(tmp_path/'recorded')

def payload(revision=1,*,final=False,bad=False):
    return {'token':'synthetic-session','revision':revision,'inputDigest':'a'*64,
        'source':'oura-recorded-offline','model':'WHR-U2.5.1',
        'time_source':'ring-timesync-100ms','interpolated':True,'experimental':True,
        'workoutID':'workout','startedAt':START,'availableUntil':START+60,
        'endedAt':START+60 if final else None,'final':final,'signalCheck':-2 if bad else 0,
        'points':[{'offset':2,'bpm':100+revision,'quality':.9},
                  {'offset':3,'bpm':102+revision,'quality':.8},
                  {'offset':65,'bpm':199,'quality':.9}]}

def send(archive,p,owner='a',device='phone'):
    event={'event_id':uuid.uuid4().hex,'sequence':1,'captured_at':START+120,'kind':'recorded_cardio','payload':p}
    return archive.append(owner,device,uuid.uuid4().hex,[event])

def values(archive,owner='a'):
    return archive.analysis(owner,start=START,end=START+100)['heart_rate']

def test_acquisition_time_tail_clipping_and_provenance(archive):
    send(archive,payload())
    points=values(archive)
    assert [p['t'] for p in points]==[START+2,START+3]
    assert points[0]['source']=='oura-recorded-offline'
    assert points[0]['interpolated'] and points[0]['provisional']
    assert archive.analysis('a',start=START,end=START+100)['interpolated']
    assert values(archive,'other')==[]
    assert archive.state('a')['latest']['heart_rate']['fresh'] is False

def test_new_revision_replaces_instead_of_duplicating_and_finalizes(archive):
    send(archive,payload());send(archive,payload(2,final=True))
    points=values(archive)
    assert len(points)==2 and [p['v'] for p in points]==[102,104]
    assert all(p['revision']==2 and not p['provisional'] for p in points)
    assert archive.state('a')['archive']['events']==2
    assert archive.state('a')['recorded_cardio']['final']==1

def test_out_of_order_upload_cannot_rewind_final_revision(archive):
    send(archive,payload(3,final=True));send(archive,payload(1))
    assert [p['v'] for p in values(archive)]==[103,105]
    send(archive,payload(4))
    assert archive.state('a')['archive']['decode_errors']==1
    assert archive.state('a')['recorded_cardio']['revision']==3

def test_bad_quality_revision_removes_prior_valid_projection_keeps_events(archive):
    send(archive,payload());send(archive,payload(2,bad=True))
    assert values(archive)==[]
    assert archive.state('a')['archive']['events']==2
    assert archive.state('a')['recorded_cardio']['point_count']==0

def test_same_revision_conflict_preserves_previous_projection(archive):
    p=payload();send(archive,p);send(archive,p)
    assert len(values(archive))==2
    changed=copy.deepcopy(p);changed['points'][0]['bpm']=200
    send(archive,changed)
    assert values(archive)[0]['v']==101
    assert archive.state('a')['archive']['decode_errors']==1

@pytest.mark.parametrize('change',[
    {'time_source':'received_at'}, {'model':'unknown'}, {'signalCheck':1},
    {'final':True}, {'interpolated':False}, {'token':'%invalid'},
    {'availableUntil':START+99_999},
    {'points':[{'offset':3,'bpm':100,'quality':2}]},
    {'points':[{'offset':3,'bpm':100,'quality':.8},{'offset':3,'bpm':101,'quality':.8}]},
])
def test_invalid_model_results_are_archived_without_publishing(archive,change):
    send(archive,{**payload(),**change})
    assert values(archive)==[]
    assert archive.state('a')['archive']['events']==1
    assert archive.state('a')['archive']['decode_errors']==1

def test_token_is_scoped_to_owner_and_device(archive):
    send(archive,payload());send(archive,payload(2),device='second-phone')
    send(archive,payload(3),owner='b')
    assert len(values(archive))==4 and len(values(archive,'b'))==2

def test_upgrade_recovers_result_previously_acknowledged_by_old_server(archive,monkeypatch):
    with archive.connection(True) as db:db.execute('DELETE FROM homepulse_projection_features')
    monkeypatch.setattr(archive,'_project',lambda *args:None)
    send(archive,payload());send(archive,payload(3,final=True));send(archive,payload(2))
    assert values(archive)==[]
    upgraded=HomePulseStore(archive.base)
    assert [p['v'] for p in values(upgraded)]==[103,105]
    assert upgraded.state('a')['archive']['events']==3
    reopened=HomePulseStore(archive.base)
    assert len(values(reopened))==2
