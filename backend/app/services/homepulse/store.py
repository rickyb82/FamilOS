"""Profile-private, durable workout and raw sensor storage.

Acknowledgement follows the transaction committing the original event and its
projections. Arrival time never replaces source acquisition time. No radio,
owner-key inference, destructive ring commands or family-wide health sharing.
"""
from __future__ import annotations

import base64
from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import secrets
import sqlite3
import stat
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .protocol import OuraProtocolError, parse_live
from .history import describe_raw_record, split_history_packet
from .activity import parse_exercise_latest
from .motion import parse_motion_record, parse_activity_record
from .breathing import decode_breathing_record, decode_sleep_breathing_record
from .workout import validate_profile, validate_plan, composition, schedule, energy_for_interval
from .sleep import GEN3_CONTRACT, normalize_bedtime, normalize_period, merge_period, coalesce_periods, period_groups, period_detail_rank, verified_run, MAX_PERIODS_PER_RUN, NATIVE_ABI
from . import recorded, spo2, processing as history_processing


class StoreError(ValueError):
    pass


class ConflictError(StoreError):
    pass


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _finite(value, low=0, high=None):
    if type(value) not in (int, float) or not math.isfinite(value) or value < low or high is not None and value > high:
        raise StoreError('Valore numerico non valido')
    return value


def _identity(value):
    if not isinstance(value, str) or not value or len(value) > 200 or any(ord(c) < 32 for c in value):
        raise StoreError('Identificatore non valido')
    return value


ALLOWED_ACTIVITIES = [
    {'id':65535,'name':'Altro'}, {'id':14,'name':'Camminata'},
    {'id':12,'name':'Corsa'}, {'id':5,'name':'Ciclismo'},
    {'id':8,'name':'Allenamento di forza'}, {'id':10,'name':'Pilates'},
    {'id':15,'name':'Yoga'},
]


def defaults():
    return {'profile':{**validate_profile({}), 'resting_hr':None},
            'config':{'work':40,'rest':20,'rounds':8,'hrmax':None,'plan':[], 'activity_name':None,'activity_id':None},
            'weekly_schedule':[],
            'auto_detection':{'enabled':False,'gym':{'id':'primary-gym','name':'La mia palestra',
                'address':'Imposta indirizzo palestra', 'latitude':None,'longitude':None,'radius_m':150},
                'heart_rate_delta_bpm':15,'sustain_seconds':30,'cooldown_seconds':3600}}


def validate_settings(value, previous=None):
    if not isinstance(value, dict):
        raise StoreError('Impostazioni non valide')
    merged={**(previous or defaults()), **value}
    if set(merged) - {'profile','config','weekly_schedule','auto_detection'}:
        raise StoreError('Impostazione sconosciuta')
    if any(not isinstance(merged.get(key),dict) for key in ('profile','config','auto_detection')):
        raise StoreError('Profilo, scheda o rilevamento non validi')
    profile=validate_profile(merged['profile'])
    resting=merged['profile'].get('resting_hr')
    if resting is not None:_finite(resting,20,200)
    profile['resting_hr']=resting
    cfg={**defaults()['config'], **merged['config']}
    cfg['plan']=validate_plan(cfg.get('plan',[]))
    for key,low,high in [('work',5,3600),('rest',0,3600),('rounds',1,100)]:
        if type(cfg[key]) is not int or not low <= cfg[key] <= high:raise StoreError('Parametro scheda non valido')
    if cfg['hrmax'] is not None:_finite(cfg['hrmax'],80,240)
    if cfg['activity_name'] is not None:
        if not isinstance(cfg['activity_name'],str) or len(cfg['activity_name'])>80:raise StoreError('Nome attività non valido')
        cfg['activity_name']=cfg['activity_name'].strip() or None
    if cfg['activity_id'] is not None and (type(cfg['activity_id']) is not int or not 0 <= cfg['activity_id'] <= 65535):
        raise StoreError('Identificatore attività non valido')
    schedule(0,**{k:cfg[k] for k in ('work','rest','rounds','plan')})
    weekly=merged['weekly_schedule']
    if not isinstance(weekly,list) or len(weekly)>100:raise StoreError('Calendario non valido')
    normalized=[]
    for entry in weekly:
        if not isinstance(entry,dict) or type(entry.get('weekday')) is not int or not 1 <= entry['weekday'] <= 7:
            raise StoreError('Giorno del calendario non valido')
        try:
            start=datetime.strptime(entry['start'],'%H:%M').time()
            end=datetime.strptime(entry['end'],'%H:%M').time()
            zone=ZoneInfo(entry.get('timezone','Europe/Rome'))
        except (KeyError,TypeError,ValueError):raise StoreError('Orario o fuso del calendario non valido') from None
        if end <= start:raise StoreError('Fine sessione precedente all’inizio')
        normalized.append({'id':_identity(entry.get('id') or uuid.uuid4().hex),'weekday':entry['weekday'],
            'start':start.strftime('%H:%M'),'end':end.strftime('%H:%M'),'timezone':str(zone),
            'label':str(entry.get('label','Palestra'))[:120]})
    auto={**defaults()['auto_detection'],**merged.get('auto_detection',{})}
    if type(auto['enabled']) is not bool:raise StoreError('Rilevamento automatico non valido')
    gym={**defaults()['auto_detection']['gym'],**auto.get('gym',{})}
    for key in ('id','name','address'):
        if not isinstance(gym[key],str) or not 1 <= len(gym[key]) <= 300:raise StoreError('Palestra non valida')
    if gym['latitude'] is not None:_finite(gym['latitude'],-90,90)
    if gym['longitude'] is not None:_finite(gym['longitude'],-180,180)
    _finite(gym['radius_m'],25,2000)
    for key,low,high in [('heart_rate_delta_bpm',1,100),('sustain_seconds',5,600),('cooldown_seconds',60,86400)]:
        _finite(auto[key],low,high)
    auto['gym']=gym
    return {'profile':profile,'config':cfg,'weekly_schedule':normalized,'auto_detection':auto}


def occurrences(entries, now=None, days=14):
    now=time.time() if now is None else now
    values=[]
    for item in entries:
        zone=ZoneInfo(item['timezone'])
        today=datetime.fromtimestamp(now,zone).date()
        for offset in range(days+1):
            date=today+timedelta(days=offset)
            if date.isoweekday()!=item['weekday']:continue
            start=datetime.combine(date,datetime.strptime(item['start'],'%H:%M').time(),zone)
            end=datetime.combine(date,datetime.strptime(item['end'],'%H:%M').time(),zone)
            if end.timestamp()<now:continue
            values.append({**item,'date':date.isoformat(),'starts_at':start.timestamp(),'ends_at':end.timestamp(),
                           'duration_minutes':(end.timestamp()-start.timestamp())/60})
    return sorted(values,key=lambda item:item['starts_at'])


class HomePulseStore:
    def __init__(self, base:Path, encrypt=lambda value:value, decrypt=lambda value:value):
        self.base=Path(base)
        if self.base.is_symlink():raise StoreError('Percorso archivio non valido')
        self.base.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.path=self.base/'wellness.sqlite3'
        if self.path.is_symlink():raise StoreError('Archivio non valido')
        self.encrypt,self.decrypt=encrypt,decrypt
        self.lock=threading.RLock();self._energy_cache={}
        fd=os.open(self.path,os.O_RDWR|os.O_CREAT|getattr(os,'O_NOFOLLOW',0),0o600);os.close(fd)
        os.chmod(self.path,0o600)
        with self.connection() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS settings(owner_id TEXT PRIMARY KEY,value TEXT NOT NULL,revision INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS pairings(code_hash TEXT PRIMARY KEY,owner_id TEXT NOT NULL,expires_at REAL NOT NULL,used INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS devices(id TEXT PRIMARY KEY,owner_id TEXT NOT NULL,name TEXT NOT NULL,token_hash TEXT NOT NULL UNIQUE,created_at REAL NOT NULL,last_seen_at REAL,revoked INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS ring_bindings(owner_id TEXT PRIMARY KEY,logical_ring_id TEXT NOT NULL,encrypted_key TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY,owner_id TEXT NOT NULL,device_id TEXT NOT NULL,event_id TEXT NOT NULL,sequence INTEGER NOT NULL,captured_at REAL NOT NULL,received_at REAL NOT NULL,kind TEXT NOT NULL,payload TEXT NOT NULL,digest TEXT NOT NULL,decode_error TEXT,UNIQUE(device_id,event_id));
            CREATE INDEX IF NOT EXISTS events_owner_time ON events(owner_id,captured_at,id);
            CREATE INDEX IF NOT EXISTS events_owner_decode ON events(owner_id,decode_error);
            CREATE INDEX IF NOT EXISTS events_recorded_upgrade ON events(id) WHERE kind='recorded_cardio';
            CREATE TABLE IF NOT EXISTS homepulse_projection_features(name TEXT PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS history(id INTEGER PRIMARY KEY,owner_id TEXT NOT NULL,ring_id TEXT NOT NULL,raw_hex TEXT NOT NULL,digest TEXT NOT NULL,tag INTEGER NOT NULL,cursor INTEGER,received_at REAL NOT NULL,UNIQUE(owner_id,ring_id,digest));
            CREATE INDEX IF NOT EXISTS history_owner_cursor ON history(owner_id,ring_id,cursor,id);
            CREATE TABLE IF NOT EXISTS points(id INTEGER PRIMARY KEY,owner_id TEXT NOT NULL,kind TEXT NOT NULL,t REAL NOT NULL,value REAL NOT NULL,source TEXT NOT NULL,quality REAL,metadata TEXT NOT NULL,identity TEXT NOT NULL,UNIQUE(owner_id,kind,identity));
            CREATE INDEX IF NOT EXISTS points_owner_kind_time ON points(owner_id,kind,t,id);
            CREATE INDEX IF NOT EXISTS points_recorded_source ON points(owner_id,source,identity);
            CREATE TABLE IF NOT EXISTS recorded_cardio_runs(owner_id TEXT NOT NULL,device_id TEXT NOT NULL,token TEXT NOT NULL,revision INTEGER NOT NULL,result_digest TEXT NOT NULL,final INTEGER NOT NULL,started_at REAL NOT NULL,available_until REAL NOT NULL,signal_check INTEGER NOT NULL,point_count INTEGER NOT NULL,computed_at REAL NOT NULL,PRIMARY KEY(owner_id,device_id,token));
            CREATE TABLE IF NOT EXISTS sessions(owner_id TEXT NOT NULL,id TEXT NOT NULL,activity_name TEXT,started_at REAL NOT NULL,ended_at REAL,active_seconds REAL NOT NULL,status TEXT NOT NULL,auto_detected INTEGER NOT NULL DEFAULT 0,provisional INTEGER NOT NULL DEFAULT 0,config TEXT NOT NULL,profile TEXT NOT NULL,last_event_at REAL NOT NULL,source TEXT NOT NULL,PRIMARY KEY(owner_id,id));
            CREATE TABLE IF NOT EXISTS diary_cleanups(owner_id TEXT NOT NULL,retain_start REAL NOT NULL,retain_end REAL NOT NULL,created_before REAL NOT NULL,PRIMARY KEY(owner_id,created_before));
            CREATE VIEW IF NOT EXISTS visible_sessions AS SELECT s.* FROM sessions s WHERE NOT EXISTS(
                SELECT 1 FROM diary_cleanups d WHERE d.owner_id=s.owner_id AND s.started_at<=d.created_before
                AND NOT(s.started_at<=d.retain_end AND coalesce(s.ended_at,s.started_at+s.active_seconds)>=d.retain_start));
            CREATE INDEX IF NOT EXISTS sessions_owner_start ON sessions(owner_id,started_at);
            CREATE TABLE IF NOT EXISTS processing(owner_id TEXT PRIMARY KEY,history_count INTEGER NOT NULL DEFAULT 0,status TEXT NOT NULL,last_processed_at REAL,error TEXT);
            CREATE TABLE IF NOT EXISTS processing_features(owner_id TEXT NOT NULL,feature TEXT NOT NULL,PRIMARY KEY(owner_id,feature));
            CREATE TABLE IF NOT EXISTS processing_checkpoints(owner_id TEXT PRIMARY KEY,last_history_id INTEGER NOT NULL,contract TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS processing_replay(owner_id TEXT NOT NULL,ring_id TEXT NOT NULL,from_cursor INTEGER NOT NULL,PRIMARY KEY(owner_id,ring_id));
            CREATE TABLE IF NOT EXISTS processing_jobs(owner_id TEXT NOT NULL,contract TEXT NOT NULL,input_sha TEXT NOT NULL,native_digest TEXT NOT NULL,PRIMARY KEY(owner_id,contract,input_sha));
            CREATE INDEX IF NOT EXISTS history_owner_tag_cursor ON history(owner_id,ring_id,tag,cursor,id);
            CREATE TABLE IF NOT EXISTS native_runs(id INTEGER PRIMARY KEY,owner_id TEXT NOT NULL,created_at REAL NOT NULL,source_input_sha256 TEXT,result TEXT NOT NULL,result_sha256 TEXT NOT NULL,UNIQUE(owner_id,result_sha256));
            CREATE TABLE IF NOT EXISTS spo2_catalog(owner_id TEXT NOT NULL,utc_ms INTEGER NOT NULL,value INTEGER NOT NULL,quality REAL NOT NULL,timezone_raw INTEGER,native_run_id INTEGER NOT NULL,PRIMARY KEY(owner_id,utc_ms));
            CREATE TABLE IF NOT EXISTS spo2_catalog_runs(run_id INTEGER PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS sleep_periods(owner_id TEXT NOT NULL,started_at REAL NOT NULL,ended_at REAL NOT NULL,id TEXT NOT NULL,period TEXT NOT NULL,native_result_sha256 TEXT NOT NULL,updated_at REAL NOT NULL,PRIMARY KEY(owner_id,started_at));
            CREATE INDEX IF NOT EXISTS sleep_owner_window ON sleep_periods(owner_id,ended_at,started_at);
            CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events BEGIN SELECT RAISE(ABORT,'events are append-only'); END;
            CREATE TRIGGER IF NOT EXISTS history_no_delete BEFORE DELETE ON history BEGIN SELECT RAISE(ABORT,'history is append-only'); END;
            ''')
        with self.connection(True) as db:recorded.replay_pending_upgrade(db)

    @contextmanager
    def connection(self, transaction=False):
        if self.path.is_symlink() or self.base.is_symlink():raise StoreError('Percorso archivio non valido')
        with self.lock:
            db=sqlite3.connect(self.path,timeout=10,isolation_level=None)
            db.row_factory=sqlite3.Row
            try:
                db.execute('PRAGMA journal_mode=DELETE');db.execute('PRAGMA synchronous=FULL')
                if transaction:db.execute('BEGIN IMMEDIATE')
                yield db
                if transaction:db.commit()
            except BaseException:
                if transaction:db.rollback()
                raise
            finally:db.close()

    def _settings(self,db,owner):
        row=db.execute('SELECT value FROM settings WHERE owner_id=?',(owner,)).fetchone()
        return json.loads(row['value']) if row else defaults()

    def settings(self,owner):
        with self.connection() as db:return self._settings(db,owner)

    def update_settings(self,owner,value):
        with self.connection(True) as db:
            normalized=validate_settings(value,self._settings(db,owner))
            db.execute('INSERT INTO settings(owner_id,value,revision) VALUES(?,?,1) ON CONFLICT(owner_id) DO UPDATE SET value=excluded.value,revision=revision+1',(owner,_json(normalized)))
        return normalized

    def set_ring(self,owner,logical_ring_id,key_hex):
        try:key=bytes.fromhex(key_hex)
        except (TypeError,ValueError):raise StoreError('Chiave anello non valida') from None
        if len(key)!=16:raise StoreError('Chiave anello non valida')
        with self.connection(True) as db:
            db.execute('INSERT INTO ring_bindings VALUES(?,?,?) ON CONFLICT(owner_id) DO UPDATE SET logical_ring_id=excluded.logical_ring_id,encrypted_key=excluded.encrypted_key',
                       (owner,_identity(logical_ring_id),self.encrypt(key.hex())))

    def ring(self,owner,secret=False):
        with self.connection() as db:
            row=db.execute('SELECT * FROM ring_bindings WHERE owner_id=?',(owner,)).fetchone()
            if not row:return None
            result={'logical_ring_id':row['logical_ring_id'],'configured':True}
            if secret:result['owner_key_hex']=self.decrypt(row['encrypted_key'])
            return result

    def create_pairing(self,owner):
        code=secrets.token_urlsafe(32);expires=time.time()+600
        with self.connection(True) as db:
            db.execute('INSERT INTO pairings VALUES(?,?,?,0)',(hashlib.sha256(code.encode()).hexdigest(),owner,expires))
        return {'pairing_code':code,'expires_at':expires}

    def enroll(self,code,name):
        digest=hashlib.sha256(str(code).encode()).hexdigest()
        token=secrets.token_urlsafe(48);device=uuid.uuid4().hex
        with self.connection(True) as db:
            row=db.execute('SELECT * FROM pairings WHERE code_hash=?',(digest,)).fetchone()
            if not row or row['used'] or row['expires_at']<time.time():raise StoreError('Codice scaduto o già utilizzato')
            owner=row['owner_id'];db.execute('UPDATE pairings SET used=1 WHERE code_hash=?',(digest,))
            db.execute('INSERT INTO devices VALUES(?,?,?,?,?,?,0)',(device,owner,str(name)[:120] or 'iPhone',hashlib.sha256(token.encode()).hexdigest(),time.time(),time.time()))
        return {'owner_id':owner,'device_id':device,'access_token':token}

    def authenticate(self,token):
        with self.connection() as db:
            row=db.execute('SELECT id,owner_id,name FROM devices WHERE token_hash=? AND revoked=0',(hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
            return dict(row) if row else None

    def revoke(self,owner,device):
        with self.connection(True) as db:
            row=db.execute('UPDATE devices SET revoked=1 WHERE owner_id=? AND id=?',(owner,device))
            if not row.rowcount:raise StoreError('Dispositivo non trovato')

    def _point(self,db,owner,kind,t,value,source,identity,quality=None,metadata=None):
        _finite(t);_finite(value, -1e12,1e12)
        db.execute('INSERT OR IGNORE INTO points(owner_id,kind,t,value,source,quality,metadata,identity) VALUES(?,?,?,?,?,?,?,?)',
                   (owner,kind,t,value,source,quality,_json(metadata or {}),identity))

    def _history(self,db,owner,device,raw,t):
        binding=db.execute('SELECT logical_ring_id FROM ring_bindings WHERE owner_id=?',(owner,)).fetchone()
        ring=binding['logical_ring_id'] if binding else device
        for frame in split_history_packet(raw):
            item=describe_raw_record(frame);digest=hashlib.sha256(frame).hexdigest()
            db.execute('INSERT OR IGNORE INTO history(owner_id,ring_id,raw_hex,digest,tag,cursor,received_at) VALUES(?,?,?,?,?,?,?)',
                       (owner,ring,frame.hex(),digest,item['tag_raw'],item.get('timestamp_raw'),t))

    def _project(self,db,owner,device,event):
        payload=event['payload'];kind=event['kind'];stamp=event['captured_at'];identity=device+':'+event['event_id']
        if kind in ('sensor_packet','ring_history'):
            raw=bytes.fromhex(payload.get('raw_hex',''))
            if not raw:return
            if raw[0]>=0x41:self._history(db,owner,device,raw,stamp)
            if len(raw)>=4 and raw[:1]==b'\x2f' and raw[2]==0x28 and raw[3]==2:
                sample=parse_live(raw,tuple(payload.get('api_version',[2,0,0])))
                if sample.bpm is not None:
                    supported=20<=sample.bpm<=250
                    self._point(db,owner,'heart_rate' if supported else 'heart_rate_outside_display',stamp,sample.bpm,'oura-ble',identity,
                                metadata={'warnings':list(sample.warnings),'flags_raw':raw[4],'ibi_validity_raw':raw[9]>>4,'ibi_ms':sample.ibi_ms,
                                          'within_display_range':supported,'display_range_is_not_physiological_validity':True})
            elif len(raw)>=4 and raw[0]==47 and raw[2]==37 and raw[3]==3:
                value=parse_exercise_latest(raw)
                self._point(db,owner,'exercise',stamp,value['heart_rate_value_raw'],'oura-exercise-raw',identity,metadata=value)
        elif kind=='recorded_cardio':
            recorded.project(db,owner,device,payload,stamp)
        elif kind=='workout':
            session_id=_identity(payload.get('session_id'))
            # Mobile background batches may finish out of order. Rebuild the
            # projection from immutable events instead of losing an early end.
            rows=db.execute("SELECT device_id,captured_at,payload FROM events WHERE owner_id=? AND kind='workout' AND json_extract(payload,'$.session_id')=? ORDER BY captured_at,sequence,id",(owner,session_id)).fetchall()
            db.execute('DELETE FROM sessions WHERE owner_id=? AND id=?',(owner,session_id))
            started=False
            for row in rows:
                value=json.loads(row['payload'])
                if value.get('action')=='started':started=True
                if started:self._workout(db,owner,row['device_id'],value,row['captured_at'])

    def _workout(self,db,owner,device,p,stamp):
        session_id=_identity(p.get('session_id'));action=p.get('action')
        old=db.execute('SELECT * FROM sessions WHERE owner_id=? AND id=?',(owner,session_id)).fetchone()
        if action=='started':
            if old:return
            settings=self._settings(db,owner)
            cfg=validate_settings({'config':p.get('config_snapshot',settings['config'])},settings)['config']
            profile=validate_settings({'profile':p.get('profile_snapshot',settings['profile'])},settings)['profile']
            db.execute('INSERT INTO sessions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',(owner,session_id,str(p.get('activity_name') or cfg.get('activity_name') or 'Allenamento')[:120],
                stamp,None,0,'active',int(p.get('auto_detected') is True),int(p.get('provisional') is True),_json(cfg),_json(profile),stamp,device))
            return
        if old is None:raise StoreError('Sessione non ancora ricevuta: inizio mancante')
        if stamp<old['last_event_at']:
            # The source event remains archived; stale controls cannot rewind a session.
            return
        active=_finite(p.get('active_seconds',old['active_seconds']))
        if action in ('paused','resumed','ended','false_detection'):
            status={'paused':'paused','resumed':'active','ended':'completed','false_detection':'rejected'}[action]
            db.execute('UPDATE sessions SET status=?,ended_at=?,active_seconds=?,last_event_at=? WHERE owner_id=? AND id=?',
                       (status,stamp if status in ('completed','rejected') else None,active,stamp,owner,session_id))
        elif action in ('activity_updated','confirmed'):
            name=str(p.get('activity_name') or old['activity_name'])[:120]
            if 'active_seconds' not in p and old['status']=='active':active+=max(0,stamp-old['last_event_at'])
            db.execute('UPDATE sessions SET activity_name=?,provisional=0,active_seconds=?,last_event_at=? WHERE owner_id=? AND id=?',(name,active,stamp,owner,session_id))

    def append(self,owner,device,batch_id,events):
        _identity(batch_id)
        if not isinstance(events,list) or not 1<=len(events)<=1000:raise StoreError('Batch eventi non valido')
        normalized=[]
        for event in events:
            if not isinstance(event,dict):raise StoreError('Evento non valido')
            event_id=_identity(event.get('event_id'));seq=event.get('sequence')
            if type(seq) is not int or not 0<=seq<2**63:raise StoreError('Sequenza non valida')
            stamp=_finite(event.get('captured_at'))
            kind=_identity(event.get('kind'));payload=event.get('payload')
            if not isinstance(payload,dict):raise StoreError('Payload evento non valido')
            if kind in ('sensor_packet','ring_history'):
                raw_hex=payload.get('raw_hex')
                if not isinstance(raw_hex,str) or len(raw_hex)>8192:raise StoreError('Pacchetto sensore non valido')
                try:raw=bytes.fromhex(raw_hex)
                except ValueError:raise StoreError('Pacchetto sensore non esadecimale') from None
                if raw and (raw[0] in (0x24,0x25) or len(raw)>=3 and raw[0]==47 and raw[2] in (43,44,45,46,47)):
                    raise StoreError('Un pacchetto di autenticazione non è un dato sensore')
            value={**event,'event_id':event_id,'sequence':seq,'captured_at':stamp,'kind':kind,'payload':payload}
            digest=hashlib.sha256(_json(value).encode()).hexdigest()
            normalized.append((value,digest))
        accepted=[];duplicates=0;received=time.time()
        with self.connection(True) as db:
            for event,digest in normalized:
                previous=db.execute('SELECT digest,owner_id FROM events WHERE device_id=? AND event_id=?',(device,event['event_id'])).fetchone()
                if previous:
                    if previous['owner_id']!=owner or previous['digest']!=digest:raise ConflictError('Evento già ricevuto con contenuto diverso')
                    duplicates+=1;accepted.append(event['event_id']);continue
                row=db.execute('INSERT INTO events(owner_id,device_id,event_id,sequence,captured_at,received_at,kind,payload,digest) VALUES(?,?,?,?,?,?,?,?,?)',
                               (owner,device,event['event_id'],event['sequence'],event['captured_at'],received,event['kind'],_json(event['payload']),digest))
                # Unknown/invalid sensor values are retained even if decoding fails.
                db.execute('SAVEPOINT projection')
                try:self._project(db,owner,device,event)
                except (OuraProtocolError,StoreError,ValueError,TypeError,KeyError,OverflowError) as exc:
                    db.execute('ROLLBACK TO projection')
                    db.execute('UPDATE events SET decode_error=? WHERE id=?',(type(exc).__name__,row.lastrowid))
                finally:db.execute('RELEASE projection')
                accepted.append(event['event_id'])
            db.execute('UPDATE devices SET last_seen_at=? WHERE owner_id=? AND id=?',(received,owner,device))
        return {'batch_id':batch_id,'accepted_event_ids':accepted,'duplicates':duplicates,'server_time':received}

    def _sessions(self,db,owner,now):
        output=[]
        for row in db.execute('SELECT * FROM visible_sessions WHERE owner_id=? ORDER BY started_at DESC LIMIT 300',(owner,)):
            item=dict(row);cfg=json.loads(item.pop('config'));profile=json.loads(item.pop('profile'))
            elapsed=item['active_seconds']
            if item['status']=='active':elapsed+=max(0,now-item['last_event_at'])
            item['active_seconds']=elapsed;item['config']=cfg;item['profile']=profile
            item['auto_detected']=bool(item['auto_detected']);item['provisional']=bool(item['provisional'])
            summary=db.execute("SELECT avg(value),max(value),count(*) FROM points WHERE owner_id=? AND kind='heart_rate' AND t>=? AND t<=?",(owner,item['started_at'],item['ended_at'] or now)).fetchone()
            energy=energy_for_interval(0,elapsed,cfg,profile)
            total=schedule(0,**{k:cfg[k] for k in ('work','rest','rounds','plan')})['total']
            energy['unestimated_seconds']+=max(0,elapsed-total)
            item['stats']={'average_bpm':summary[0],'max_bpm':summary[1],'samples':summary[2],**energy}
            item['phase']=schedule(elapsed,**{k:cfg[k] for k in ('work','rest','rounds','plan')})
            output.append(item)
        return output

    def state(self,owner):
        now=time.time()
        with self.connection() as db:
            settings=self._settings(db,owner);latest={}
            for kind in ('heart_rate','heart_rate_outside_display','spo2','temperature','motion','steps','respiratory_rate','exercise'):
                row=db.execute('SELECT * FROM points WHERE owner_id=? AND kind=? AND t<=? ORDER BY t DESC,id DESC LIMIT 1',(owner,kind,now+5)).fetchone()
                if row:
                    metadata=json.loads(row['metadata']);item={'value':row['value'],'captured_at':row['t'],'utc_ms':row['t']*1000,'source':row['source'],'quality':row['quality'],**metadata}
                    if kind=='heart_rate':item.update(bpm=row['value'],fresh=0<=now-row['t']<=(90 if row['source']==recorded.SOURCE and metadata.get('provisional') else 8))
                    latest[kind]=item
            devices=[dict(row) for row in db.execute('SELECT id,name,created_at,last_seen_at,revoked FROM devices WHERE owner_id=? ORDER BY created_at',(owner,))]
            events=db.execute('SELECT count(*),sum(decode_error IS NOT NULL) FROM events WHERE owner_id=?',(owner,)).fetchone()
            history=db.execute('SELECT count(*) FROM history WHERE owner_id=?',(owner,)).fetchone()[0]
            processing=db.execute('SELECT * FROM processing WHERE owner_id=?',(owner,)).fetchone()
            sessions=self._sessions(db,owner,now)
            recorded_status=recorded.latest(db,owner)
        return {'server_time':now,'settings':settings,'latest':latest,'devices':devices,'sessions':sessions,'recorded_cardio':recorded_status,
                'allowed_activities':ALLOWED_ACTIVITIES,
                'active_session':next((s for s in sessions if s['status'] in ('active','paused')),None),
                'weekly_occurrences':occurrences(settings['weekly_schedule'],now),
                'composition':composition(settings['profile']),
                'archive':{'events':events[0],'history_records':history,'decode_errors':events[1] or 0,
                           'processing_status':processing['status'] if processing else 'In attesa di record storici',
                           'processing_error':processing['error'] if processing else None},
                'daily_energy':self.daily_energy(owner),
                'spo2_catalog':self.spo2_summary(owner),
                'diary_cleanups':self.diary_cleanups(owner),
                'ring_binding':self.ring(owner,secret=False)}

    def diary_cleanups(self,owner):
        with self.connection() as db:
            return [dict(row) for row in db.execute('SELECT retain_start,retain_end,created_before FROM diary_cleanups WHERE owner_id=? ORDER BY created_before',(owner,))]

    def cleanup_diary(self,owner,rule):
        if not isinstance(rule,dict) or set(rule)!={'retain_start','retain_end','created_before'}:raise StoreError('Pulizia diario non valida')
        start,end,cutoff=(_finite(rule[k],1,time.time()+300) for k in ('retain_start','retain_end','created_before'))
        if not start<end<=cutoff:raise StoreError('Intervallo da conservare non valido')
        with self.connection(True) as db:
            before=db.execute('SELECT count(*) FROM visible_sessions WHERE owner_id=?',(owner,)).fetchone()[0]
            db.execute('INSERT OR IGNORE INTO diary_cleanups VALUES(?,?,?,?)',(owner,start,end,cutoff))
            after=db.execute('SELECT count(*) FROM visible_sessions WHERE owner_id=?',(owner,)).fetchone()[0]
        self._energy_cache.clear()
        return {'removed_from_diary':before-after,'remaining_sessions':after,'sensor_data_preserved':True,'rule':dict(retain_start=start,retain_end=end,created_before=cutoff)}

    def diary_analysis(self,owner,hours=2):
        if type(hours) is not int or hours not in (2,4,8,24,48,168):raise StoreError('Intervallo diario non valido')
        end=time.time();start=end-hours*3600;bucket=(end-start)/140
        # Query only real extrema/edge points per time bucket, after computing
        # signal gaps on the complete series. Original samples stay in points.
        query="WITH base AS (\n            SELECT id,kind,t,value,source,lag(t) OVER(PARTITION BY kind ORDER BY t,id) previous\n            FROM points WHERE owner_id=? AND t>=? AND t<=? AND kind IN ('heart_rate','spo2','temperature','motion','steps','respiratory_rate')),\n            segmented AS (SELECT *,sum(CASE WHEN kind='heart_rate' AND t-previous>8 THEN 1 ELSE 0 END) OVER(PARTITION BY kind ORDER BY t,id) segment FROM base),\n            ranked AS (SELECT *,row_number() OVER(PARTITION BY kind,cast((t-?)/? AS INTEGER) ORDER BY value,t,id) low,\n            row_number() OVER(PARTITION BY kind,cast((t-?)/? AS INTEGER) ORDER BY value DESC,t,id) high,\n            row_number() OVER(PARTITION BY kind ORDER BY t,id) first,\n            row_number() OVER(PARTITION BY kind ORDER BY t DESC,id DESC) last FROM segmented)\n            SELECT kind,t,value,source,segment FROM ranked WHERE low=1 OR high=1 OR first=1 OR last=1 ORDER BY t,id"
        with self.connection() as db:
            rows=db.execute(query,(owner,start,end,start,bucket,start,bucket)).fetchall()
            counts={row[0]:row[1] for row in db.execute('SELECT kind,count(*) FROM points WHERE owner_id=? AND t>=? AND t<=? GROUP BY kind',(owner,start,end))}
        result={k:[] for k in ('heart_rate','spo2','temperature','motion','step_increments','respiratory_rate')}
        for row in rows:
            kind='step_increments' if row['kind']=='steps' else row['kind']
            result[kind].append({'t':row['t'],'v':row['value'],'source':row['source'],'segment':row['segment']})
        return {**result,'window_start':start,'window_end':end,'hours':hours,'source_counts':counts,'chart_reduced':True,'full_samples_preserved':True}

    def daily_energy(self,owner,date=None):
        from . import daily
        now=time.time()
        try:day,start,end=daily.window(now,date)
        except (ValueError,TypeError):raise StoreError('Data energia non valida') from None
        with self.connection() as db:
            settings=self._settings(db,owner);profile=settings['profile']
            key=(owner,day,_json(profile))
            cached=self._energy_cache.get(key)
            if cached and 0<=now-cached[0]<30:return json.loads(_json(cached[1]))
            rows=db.execute("SELECT t,metadata FROM points WHERE owner_id=? AND kind='steps' AND t>? AND t<=? ORDER BY t,id",(owner,start,end+13*60)).fetchall()
            sessions=db.execute("SELECT * FROM visible_sessions WHERE owner_id=? AND started_at<? AND (ended_at IS NULL OR ended_at>?) AND status!='rejected'",(owner,end,start)).fetchall()
            intervals=[];steps=0
            for row in rows:
                meta=json.loads(row['metadata']);mets=meta.get('met_levels',[])
                if not isinstance(mets,list) or len(mets)>13:continue
                for i,met in enumerate(mets):
                    stop=row['t']-(len(mets)-i-1)*60
                    intervals.append({'start':stop-60,'end':stop,'met':met,'source':'oura-minute-met','priority':2})
                if start<=row['t']<=end:steps+=meta.get('step_count',0)
            from .cardio import phase_windows
            for session in sessions:
                events=[{'captured_at':r['captured_at'],'sequence':r['sequence'],'payload':json.loads(r['payload'])} for r in db.execute("SELECT captured_at,sequence,payload FROM events WHERE owner_id=? AND kind='workout' AND json_extract(payload,'$.session_id')=? ORDER BY captured_at,sequence,id",(owner,session['id']))]
                windows,_=phase_windows(session['started_at'],min(end,session['ended_at'] or end),json.loads(session['config']),events)
                for a,b,_,phase,_,_ in windows:
                    if phase.get('met') is not None:intervals.append({'start':a,'end':b,'met':phase['met'],'source':'workout-plan-met','priority':1})
            value=daily.estimate(profile,intervals,start,end)
            value.update(date=day,time_zone='Europe/Rome',updated_at=now,steps_in_records=steps)
            # Bounded per-profile cache; no health data leaves this owner's store.
            if len(self._energy_cache)>128:self._energy_cache.clear()
            self._energy_cache[key]=(now,value)
            return json.loads(_json(value))

    def sleep(self,owner,days=14):
        if type(days) is not int or not 1<=days<=90:raise StoreError('Intervallo sonno non valido')
        now=time.time();start=now-days*86400
        with self.connection() as db:
            periods=coalesce_periods([json.loads(row['period']) for row in db.execute(
                'SELECT period FROM sleep_periods WHERE owner_id=? AND ended_at>=? AND started_at<=? ORDER BY started_at DESC',
                (owner,start,now))])
            for period in periods:
                period['spo2']=spo2.aggregate(db,owner,period['started_at'],period['ended_at'])
                rate=db.execute("SELECT count(*),avg(value),min(value),max(value) FROM points WHERE owner_id=? AND kind='respiratory_rate' AND source='oura-firmware-sleep-period-history' AND t>=? AND t<=?",(owner,period['started_at'],period['ended_at'])).fetchone()
                period['respiratory_rate']={'sample_count':rate[0],'average':rate[1],'minimum':rate[2],'maximum':rate[3],'source':'oura-firmware-sleep-period-history','aggregation':'arithmetic-mean-of-record-means','is_live':False}
            processing=db.execute('SELECT status,last_processed_at,error FROM processing WHERE owner_id=?',(owner,)).fetchone()
        return {'server_time':now,'window_start':start,'window_end':now,'periods':periods,
                'processing_status':processing['status'] if processing else 'In attesa di record storici',
                'last_processed_at':processing['last_processed_at'] if processing else None,
                'processing_error':processing['error'] if processing else None}

    def backfill_spo2_catalog(self,limit=2):
        """Bounded background migration, using saved outputs without RF/redecode."""
        with self.connection() as db:
            rows=db.execute('SELECT id,owner_id,result FROM native_runs WHERE id NOT IN (SELECT run_id FROM spo2_catalog_runs) ORDER BY id DESC LIMIT ?', (limit,)).fetchall()
        for row in rows:
            result=json.loads(row['result'])
            with self.connection(True) as db:
                spo2.capture(db,row['owner_id'],row['id'],result)
                db.execute('INSERT OR IGNORE INTO spo2_catalog_runs VALUES(?)',(row['id'],))
        return len(rows)

    def spo2_summary(self,owner):
        with self.connection() as db:
            count=db.execute('SELECT count(*) FROM spo2_catalog WHERE owner_id=?',(owner,)).fetchone()[0]
            recent=[dict(row) for row in db.execute('SELECT utc_ms,value,quality,timezone_raw FROM spo2_catalog WHERE owner_id=? ORDER BY utc_ms DESC LIMIT 10',(owner,))]
            # Per-period aggregates are explicit until native sleep selection is
            # available; do not manufacture a night from arbitrary daytime data.
            periods=db.execute('SELECT period FROM sleep_periods WHERE owner_id=? ORDER BY ended_at DESC LIMIT 1',(owner,)).fetchall()
            nightly=[]
            for row in periods:
                period=json.loads(row['period'])
                aggregate=spo2.aggregate(db,owner,period['started_at'],period['ended_at'])
                if aggregate['sample_count']:
                    aggregate['sleep_id']=period['id'];nightly.append(aggregate)
            return {'sample_count':count,'recent_native_outputs':recent,
                    'latest_sleep':nightly[0] if nightly else None,
                    'selection_policy':'latest native sleep period; not Oura DbSleep.day selection',
                    'native_quality_preserved':True,'live':False}

    def analysis(self,owner,session_id=None,start=None,end=None):
        now=time.time();activity=None;workout_end=None;cardio=None;workout_events=[];session=None
        with self.connection() as db:
            if session_id and session_id!='last24h':
                session=db.execute('SELECT * FROM visible_sessions WHERE owner_id=? AND id=?',(owner,session_id)).fetchone()
                if not session:raise StoreError('Sessione non trovata')
                start=session['started_at'];workout_end=session['ended_at'];end=min(now,(workout_end or now)+600);activity=session['activity_name']
                workout_events=[{'captured_at':r['captured_at'],'sequence':r['sequence'],'payload':json.loads(r['payload'])} for r in db.execute("SELECT captured_at,sequence,payload FROM events WHERE owner_id=? AND kind='workout' AND json_extract(payload,'$.session_id')=? ORDER BY captured_at,sequence,id",(owner,session_id))]
            else:
                start=now-86400 if start is None else _finite(start)
                end=now if end is None else _finite(end)
            if end<start:raise StoreError('Intervallo grafico non valido')
            rows=db.execute('SELECT kind,t,value,metadata,source FROM points WHERE owner_id=? AND t>=? AND t<=? ORDER BY t,id',(owner,start,end)).fetchall()
        result={kind:[] for kind in ('heart_rate','spo2','temperature','motion','step_increments','respiratory_rate','heart_rate_outside_display')};quality=[]
        for row in rows:
            kind='step_increments' if row['kind']=='steps' else row['kind']
            if kind not in result:continue
            meta=json.loads(row['metadata'])
            result[kind].append({'t':row['t'],'v':row['value'],**({'warnings':meta.get('warnings',[]),'flags_raw':meta.get('flags_raw'),'source':row['source'],**{k:meta[k] for k in ('model','interpolated','experimental','provisional','revision','signal_quality','signal_check') if k in meta}} if kind=='heart_rate' else {})})
            if kind=='heart_rate' and meta.get('warnings'):quality.append({'t':row['t'],**meta})
        if session is not None:
            from .cardio import analyze
            cardio=analyze(start,workout_end or now,json.loads(session['config']),workout_events,result['heart_rate'])
        heart=[p['v'] for p in result['heart_rate'] if workout_end is None or p['t']<=workout_end]
        provenance={'sources':sorted({p.get('source','unknown') for p in result['heart_rate']}),
                    'models':sorted({p['model'] for p in result['heart_rate'] if p.get('model')}),
                    'provisional':any(p.get('provisional') for p in result['heart_rate']),
                    'interpolated':any(p.get('interpolated') for p in result['heart_rate']),
                    'experimental':any(p.get('experimental') for p in result['heart_rate'])}
        return {**result,'cardio':cardio,'cardio_provenance':provenance,'window_start':start,'window_end':end,'workout_end':workout_end,'activity_name':activity,
                'steps_in_records':sum(p['v'] for p in result['step_increments']),'quality_events':quality,
                'stats':{'average_bpm':sum(heart)/len(heart) if heart else None,'max_bpm':max(heart) if heart else None},
                'acquisition_time_alignment':True,'interpolated':any(p.get('interpolated') for p in result['heart_rate'])}

    def samples(self,owner,session_id,metric='heart_rate',start_offset_seconds=0,end_offset_seconds=None,cursor=None,limit=200,include_recovery=False):
        """Bounded, lossless pages of derived measurements in an owner's session.

        The cursor fixes the time window, not a database snapshot: provisional
        model revisions can replace points while paging. Callers are told this
        explicitly and must re-read after final processing when needed.
        """
        units={'heart_rate':'BPM','spo2_native':'%','spo2':'%','temperature':'°C','motion':'s','step_increments':'steps','respiratory_rate':'breaths/min'}
        if metric not in units or type(limit) is not int or not 1<=limit<=1000 or type(include_recovery) is not bool:
            raise StoreError('Parametri campioni non validi')
        _identity(session_id)
        offset=float(_finite(start_offset_seconds))
        stop=None if end_offset_seconds is None else float(_finite(end_offset_seconds))
        if stop is not None and stop<offset:raise StoreError('Intervallo campioni non valido')
        scope=hashlib.sha256(_json([owner,session_id,metric,offset,stop,include_recovery]).encode()).hexdigest()
        after=None;cutoff=None
        if cursor is not None:
            try:
                if not isinstance(cursor,str) or len(cursor)>1024:raise ValueError()
                page=json.loads(base64.urlsafe_b64decode(cursor.encode()))
                if set(page)!={'scope','t','id','end'} or page['scope']!=scope:raise ValueError()
                after=(_finite(page['t']),page['id']);cutoff=_finite(page['end'])
                if type(after[1]) is not int or not 1<=after[1]<2**63:raise ValueError()
            except (ValueError,TypeError,KeyError,UnicodeError):raise StoreError('Cursore campioni non valido') from None
        now=time.time()
        with self.connection() as db:
            session=db.execute('SELECT started_at,ended_at,activity_name FROM visible_sessions WHERE owner_id=? AND id=?',(owner,session_id)).fetchone()
            if session is None:raise StoreError('Sessione non trovata')
            start=session['started_at']+offset
            end=min(now,(session['ended_at'] or now)+(600 if include_recovery and session['ended_at'] is not None else 0))
            if stop is not None:end=min(end,session['started_at']+stop)
            if cutoff is not None:end=min(end,cutoff)
            kind='steps' if metric=='step_increments' else metric
            args=[owner,kind,start,end]
            where='owner_id=? AND kind=? AND t>=? AND t<=?'
            table='points'
            if metric=='spo2_native':
                # Trusted native type6 outputs, including quality0/out-of-range.
                # UTC milliseconds uniquely identify this owner's catalog sample.
                table="(SELECT utc_ms AS id,owner_id,'spo2_native' AS kind,utc_ms/1000.0 AS t,value,'oura-ecore-native-catalog' AS source,quality,'{\"native_catalog\":true,\"quality_filtered\":false}' AS metadata FROM spo2_catalog)"
            summary=db.execute('SELECT count(*),min(value),max(value),avg(value) FROM '+table+' WHERE '+where,args).fetchone()
            if after is not None:where+=' AND (t>? OR (t=? AND id>?))';args += [after[0],after[0],after[1]]
            rows=db.execute('SELECT id,t,value,source,quality,metadata FROM '+table+' WHERE '+where+' ORDER BY t,id LIMIT ?',args+[limit+1]).fetchall()
        more=len(rows)>limit;rows=rows[:limit];points=[]
        for row in rows:
            meta=json.loads(row['metadata'])
            points.append({'t':row['t'],'captured_at':datetime.fromtimestamp(row['t'],timezone.utc).isoformat(),
                'elapsed_seconds':row['t']-session['started_at'],'v':row['value'],'source':row['source'],'quality':row['quality'],
                **{k:meta[k] for k in ('warnings','flags_raw','model','interpolated','experimental','provisional','revision','signal_quality','signal_check','native_catalog','quality_filtered') if k in meta}})
        next_cursor=None
        if more:
            last=rows[-1];next_cursor=base64.urlsafe_b64encode(_json({'scope':scope,'t':last['t'],'id':last['id'],'end':end}).encode()).decode()
        return {'session_id':session_id,'activity_name':session['activity_name'],'metric':metric,'unit':units[metric],
            'window_start':start,'window_end':end,'workout_start':session['started_at'],'workout_end':session['ended_at'],
            'include_recovery':include_recovery,'points':points,'returned':len(points),'total_points':summary[0],
            'range_stats':{'min':summary[1],'max':summary[2],'mean':summary[3]},'has_more':more,'next_cursor':next_cursor,
            'downsampled':False,'pagination_snapshot':False,'acquisition_time_alignment':True,
            'interpretation':'Tempi di acquisizione; valori mancanti non ricostruiti. I punti interpolati del modello non sono misure indipendenti. Le revisioni provvisorie possono cambiare durante la lettura: rileggere a elaborazione finale.'}

    def manual_session(self,owner,action,session_id=None,activity_name=None,**extra):
        now=time.time();session_id=session_id or uuid.uuid4().hex
        with self.connection() as db:
            row=db.execute('SELECT * FROM visible_sessions WHERE owner_id=? AND id=?',(owner,session_id)).fetchone()
            if action!='start' and row is None:raise StoreError('Sessione non trovata')
            if action=='start':
                active=db.execute("SELECT id FROM visible_sessions WHERE owner_id=? AND status IN ('active','paused')",(owner,)).fetchone()
                if active:raise ConflictError('Una sessione è già attiva')
            elapsed=row['active_seconds'] if row else 0
            if row and row['status']=='active':elapsed+=max(0,now-row['last_event_at'])
            settings=self._settings(db,owner)
        mapped={'start':'started','pause':'paused','resume':'resumed','end':'ended','activity':'activity_updated'}
        if action not in mapped:raise StoreError('Azione sessione non valida')
        payload={'action':mapped[action],'session_id':session_id,'active_seconds':elapsed,'activity_name':activity_name,**extra}
        if action=='start':payload.update(config_snapshot=settings['config'],profile_snapshot=settings['profile'])
        self.append(owner,'server:'+owner,uuid.uuid4().hex,[{'event_id':uuid.uuid4().hex,'sequence':time.time_ns(),'captured_at':now,'kind':'workout','payload':payload}])
        return self.state(owner)

    def pending_owners(self):
        with self.connection() as db:
            # Installing the sleep decoder must also replay an archive whose
            # record count has not changed since the previous worker version.
            return [row[0] for row in db.execute('''SELECT h.owner_id FROM history h
                LEFT JOIN processing p ON p.owner_id=h.owner_id
                LEFT JOIN processing_features f ON f.owner_id=h.owner_id AND f.feature=?
                GROUP BY h.owner_id HAVING count(*)>coalesce(max(p.history_count),0) OR max(f.feature) IS NULL OR h.owner_id NOT IN (SELECT owner_id FROM processing_features WHERE feature=?)''', (NATIVE_ABI,history_processing.CONTRACT))]

    def historical_records(self,owner):
        with self.connection() as db:
            return [dict(row) for row in db.execute('SELECT id,ring_id,raw_hex,digest,tag,cursor FROM history WHERE owner_id=? ORDER BY cursor,id',(owner,))]

    def processing_snapshot(self,owner):
        with self.connection() as db:
            total,last=db.execute('SELECT count(*),coalesce(max(id),0) FROM history WHERE owner_id=?',(owner,)).fetchone()
            checkpoint=db.execute('SELECT last_history_id FROM processing_checkpoints WHERE owner_id=? AND contract=?',(owner,history_processing.CONTRACT)).fetchone()
            if checkpoint:
                rows=db.execute('SELECT h.id,h.ring_id,h.raw_hex,h.digest,h.tag,h.cursor FROM history h LEFT JOIN processing_replay r ON r.owner_id=h.owner_id AND r.ring_id=h.ring_id WHERE h.owner_id=? AND (r.from_cursor IS NULL OR h.cursor>=r.from_cursor OR h.id>?) ORDER BY h.cursor,h.id',(owner,checkpoint[0])).fetchall()
            else:rows=db.execute('SELECT id,ring_id,raw_hex,digest,tag,cursor FROM history WHERE owner_id=? ORDER BY cursor,id',(owner,)).fetchall()
            records={row['id']:dict(row) for row in rows}
            for ring in {row['ring_id'] for row in rows}:
                positions=[row['cursor'] for row in rows if row['ring_id']==ring and row['cursor'] is not None]
                if not positions:continue
                first=min(positions)
                for tag in (0x41,0x42):
                    row=db.execute('SELECT id,ring_id,raw_hex,digest,tag,cursor FROM history WHERE owner_id=? AND ring_id=? AND tag=? AND cursor<? ORDER BY cursor DESC,id DESC LIMIT 1',(owner,ring,tag,first)).fetchone()
                    if row:records[row['id']]=dict(row)
        return {'records':sorted(records.values(),key=lambda row:(row['cursor'] if row['cursor'] is not None else -1,row['id'])),'total_count':total,'last_history_id':last}

    def cached_processing_job(self,owner,digest):
        with self.connection() as db:
            row=db.execute('SELECT n.result FROM processing_jobs j JOIN native_runs n ON n.owner_id=j.owner_id AND n.result_sha256=j.native_digest WHERE j.owner_id=? AND j.contract=? AND j.input_sha=?',(owner,history_processing.CONTRACT,digest)).fetchone()
            return json.loads(row[0]) if row else None

    def finish_processing(self,owner,snapshot,replay):
        with self.connection(True) as db:
            db.execute('INSERT INTO processing VALUES(?,?,?,?,NULL) ON CONFLICT(owner_id) DO UPDATE SET history_count=excluded.history_count,status=excluded.status,last_processed_at=excluded.last_processed_at,error=NULL',(owner,snapshot['total_count'],'Storico elaborato',time.time()))
            db.execute('INSERT INTO processing_checkpoints VALUES(?,?,?) ON CONFLICT(owner_id) DO UPDATE SET last_history_id=excluded.last_history_id,contract=excluded.contract',(owner,snapshot['last_history_id'],history_processing.CONTRACT))
            for ring,cursor in replay.items():db.execute('INSERT INTO processing_replay VALUES(?,?,?) ON CONFLICT(owner_id,ring_id) DO UPDATE SET from_cursor=excluded.from_cursor',(owner,ring,cursor))
            for feature in (NATIVE_ABI,history_processing.CONTRACT):db.execute('INSERT OR IGNORE INTO processing_features VALUES(?,?)',(owner,feature))

    def publish_processed(self,owner,records,result,checkpoint=True):
        success=result.get('status')=='ok' and result.get('process_true') is True and result.get('deinit_returned') is True
        count=len(records);by_hash={row['digest']:row for row in records}
        with self.connection(True) as db:
            # Original native outputs and the checkpoint commit together. A full
            # disk cannot acknowledge processing while losing rejected averages.
            original=_json(result)
            result_digest=hashlib.sha256(original.encode()).hexdigest()
            db.execute('INSERT OR IGNORE INTO native_runs(owner_id,created_at,source_input_sha256,result,result_sha256) VALUES(?,?,?,?,?)',
                       (owner,time.time(),result.get('source_input_sha256'),original,result_digest))
            if checkpoint or not success:
                db.execute('INSERT INTO processing VALUES(?,?,?,?,?) ON CONFLICT(owner_id) DO UPDATE SET history_count=CASE WHEN excluded.history_count>0 THEN excluded.history_count ELSE processing.history_count END,status=excluded.status,last_processed_at=excluded.last_processed_at,error=excluded.error',
                           (owner,count if success else 0,'Storico elaborato' if success else 'Elaborazione da completare',time.time(),None if success else result.get('error',result.get('status','core non disponibile'))))
            if not success:return
            if checkpoint:
                db.execute('INSERT OR IGNORE INTO processing_features VALUES(?,?)',(owner,history_processing.CONTRACT))
            if verified_run(result) and isinstance(result.get('gen3_sleep'),dict) and result['gen3_sleep'].get('contract')==GEN3_CONTRACT and result.get('sensor_mapping_abi')==history_processing.SENSOR_MAPPING_ABI and not checkpoint:
                db.execute('INSERT OR REPLACE INTO processing_jobs VALUES(?,?,?,?)',(owner,history_processing.CONTRACT,result['source_input_sha256'],result_digest))
            if checkpoint and result.get('sleep_native_abi')==NATIVE_ABI:
                # This is a decoder-attempt checkpoint. Failed/empty callbacks
                # never erase valid periods and do not cause perpetual replay.
                db.execute('INSERT OR IGNORE INTO processing_features VALUES(?,?)',(owner,NATIVE_ABI))
            sleep_rows=result.get('sleep_outputs',[])
            gen3=result.get('gen3_sleep',{})
            bedtime_rows=gen3.get('periods',[]) if isinstance(gen3,dict) and gen3.get('contract')==GEN3_CONTRACT else []
            candidates=[(row,normalize_period) for row in sleep_rows] if isinstance(sleep_rows,list) and len(sleep_rows)<=MAX_PERIODS_PER_RUN else []
            if isinstance(bedtime_rows,list) and len(bedtime_rows)<=MAX_PERIODS_PER_RUN:candidates+=[(row,normalize_bedtime) for row in bedtime_rows]
            if verified_run(result):
                for row,normalize in candidates:
                    try:period=normalize(owner,row,time.time())
                    except (ValueError,TypeError,OverflowError):continue
                    previous=db.execute('SELECT period FROM sleep_periods WHERE owner_id=? AND started_at=?',
                                        (owner,period['started_at'])).fetchone()
                    if previous:period=merge_period(json.loads(previous['period']),period)
                    db.execute('INSERT INTO sleep_periods VALUES(?,?,?,?,?,?,?) ON CONFLICT(owner_id,started_at) DO UPDATE SET ended_at=excluded.ended_at,period=excluded.period,native_result_sha256=excluded.native_result_sha256,updated_at=excluded.updated_at',
                               (owner,period['started_at'],period['ended_at'],period['id'],_json(period),result_digest,time.time()))
                    # Repair duplicate projections transactionally as new data
                    # arrives, retaining the row with analysis in either order.
                    # Immutable native runs and raw history remain untouched.
                    nearby=[json.loads(item['period']) for item in db.execute(
                        'SELECT period FROM sleep_periods WHERE owner_id=? AND started_at BETWEEN ? AND ? AND ended_at BETWEEN ? AND ?',
                        (owner,period['started_at']-30,period['started_at']+30,period['ended_at']-30,period['ended_at']+30))]
                    for group in period_groups(nearby):
                        preferred=max(group,key=period_detail_rank)
                        for duplicate in group:
                            if duplicate is not preferred:
                                db.execute('DELETE FROM sleep_periods WHERE owner_id=? AND started_at=?',
                                           (owner,duplicate['started_at']))
            run_id=db.execute('SELECT id FROM native_runs WHERE owner_id=? AND result_sha256=?',(owner,result_digest)).fetchone()[0]
            spo2.capture(db,owner,run_id,result)
            db.execute('INSERT OR IGNORE INTO spo2_catalog_runs VALUES(?)',(run_id,))
            for row in result.get('spo2_outputs',[]):
                value,quality,stamp=row.get('average_raw'),row.get('quality_raw'),row.get('timestamp_utc_ms_native')
                # Original averages are retained, including those failing our display gate.
                if type(value) not in (int,float) or type(quality) not in (int,float) or type(stamp) not in (int,float):continue
                if row.get('data_type')==6 and math.isfinite(value) and math.isfinite(quality) and math.isfinite(stamp) and 85<=value<=100 and quality==1 and stamp>0:
                    self._point(db,owner,'spo2',stamp/1000,value,'oura-ecore','ecore:'+str(stamp)+':'+str(value),quality,{'native':row})
            for row in result.get('mapped_temperatures',[]):
                value,stamp=row.get('field_1_celsius'),row.get('utc_ms')
                if type(value) in (int,float) and math.isfinite(value) and value>0 and type(stamp) in (int,float) and stamp>0:
                    self._point(db,owner,'temperature',stamp/1000,value,'oura-temperature-history',row['record_sha256'],metadata={'sensor_index':1,'core_body_temperature':False})
            for row in result.get('mapped_sensor_records',[]):
                record=by_hash.get(row['record_sha256'])
                if not record:continue
                try:
                    raw=bytes.fromhex(record['raw_hex']);stamp=row['utc_ms']/1000
                    if record['tag']==0x47:
                        decoded=parse_motion_record(raw,utc_ms=row['utc_ms'])
                        self._point(db,owner,'motion',stamp,decoded['motion_seconds'],'oura-motion-history',record['digest'],metadata=decoded)
                    elif record['tag']==0x50:
                        decoded=parse_activity_record(raw,utc_ms=row['utc_ms'])
                        self._point(db,owner,'steps',stamp,decoded['step_count'],'oura-activity-history',record['digest'],metadata=decoded)
                    elif record['tag'] in (0x62,0x6a):
                        if record['tag']==0x6a and not (result.get('version')==[3,6,2] and isinstance(result.get('mapping'),dict) and result['mapping'].get('complete') is True and result.get('sensor_mapping_abi')==history_processing.SENSOR_MAPPING_ABI):continue
                        decoded=(decode_sleep_breathing_record if record['tag']==0x6a else decode_breathing_record)(raw)
                        if decoded.get('available'):
                            self._point(db,owner,'respiratory_rate',stamp,decoded['average_breathing_rate_per_minute'],decoded['source'] if record['tag']==0x6a else 'oura-firmware-history',record['digest'],metadata=decoded)
                except (OuraProtocolError,ValueError,TypeError,KeyError):continue
