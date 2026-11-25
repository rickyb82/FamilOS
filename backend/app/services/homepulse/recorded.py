"""Projection of locally computed cardio revisions; never calls Oura services.

Immutable events retain all model outputs. A newer revision atomically replaces
only this device/session projection, including removal of previously valid points
when the revised signal is bad. Receipt time never becomes acquisition time.
"""
import hashlib
import json
import math
import re

SOURCE = 'oura-recorded-offline'
MODEL = 'WHR-U2.5.1'


def number(value, low, high):
    if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError('Invalid recorded cardio number')
    return value


def project(db, owner, device, payload, stamp):
    token=payload.get('token');revision=payload.get('revision')
    if not isinstance(token,str) or re.fullmatch(r'[A-Za-z0-9-]{1,80}',token) is None:
        raise ValueError('Invalid recorded cardio token')
    if type(revision) is not int or not 1<=revision<=1_000_000_000:
        raise ValueError('Invalid recorded cardio revision')
    if (payload.get('source')!=SOURCE or payload.get('model')!=MODEL
            or payload.get('time_source')!='ring-timesync-100ms'
            or payload.get('interpolated') is not True or payload.get('experimental') is not True):
        raise ValueError('Unsupported recorded cardio provenance')
    digest=payload.get('inputDigest')
    if not isinstance(digest,str) or re.fullmatch(r'[0-9a-f]{64}',digest) is None:
        raise ValueError('Invalid recorded cardio input identity')
    start=number(payload.get('startedAt'),1_262_304_000,4_102_444_800)
    end=number(payload.get('availableUntil'),start,start+21_600)
    if end>stamp+120:raise ValueError('Recorded cardio time exceeds computation time')
    final=payload.get('final');ended=payload.get('endedAt')
    if type(final) is not bool or final != (ended is not None):
        raise ValueError('Invalid recorded cardio final state')
    if ended is not None and number(ended,start,end)!=end:
        raise ValueError('Final recorded cardio bound mismatch')
    check=payload.get('signalCheck')
    if type(check) is not int or check not in (-2,-1,0):
        raise ValueError('Invalid recorded cardio quality')
    points=payload.get('points')
    if not isinstance(points,list) or len(points)>21_601:
        raise ValueError('Invalid recorded cardio points')
    previous=-1;normalized=[]
    for point in points:
        if not isinstance(point,dict):raise ValueError('Invalid recorded cardio point')
        offset=number(point.get('offset'),0,24_000)
        if offset<=previous:raise ValueError('Recorded cardio times must increase')
        previous=offset
        bpm=point.get('bpm');quality=point.get('quality')
        if bpm is not None:number(bpm,-1_000_000,1_000_000)
        if quality is not None:number(quality,0,1)
        if check != -2 and start+offset<=end and bpm is not None and 20<=bpm<=250 and quality is not None:
            normalized.append((start+offset,bpm,quality,offset))
    body=json.dumps(payload,sort_keys=True,separators=(',',':'),allow_nan=False)
    result_digest=hashlib.sha256(body.encode()).hexdigest()
    old=db.execute('SELECT revision,result_digest,final,started_at FROM recorded_cardio_runs WHERE owner_id=? AND device_id=? AND token=?',(owner,device,token)).fetchone()
    if old:
        if revision<old['revision']:return
        if revision==old['revision']:
            if result_digest!=old['result_digest']:raise ValueError('Recorded cardio revision conflict')
            return
        if abs(start-old['started_at'])>.001 or old['final'] and not final:
            raise ValueError('Recorded cardio session identity/final state changed')
    prefix='recorded:'+device+':'+token+':'
    db.execute('DELETE FROM points WHERE owner_id=? AND source=? AND identity>=? AND identity<?',(owner,SOURCE,prefix,prefix+'\uffff'))
    metadata={'model':MODEL,'experimental':True,'interpolated':True,'provisional':not final,
              'recorded_token':token,'recorded_device_id':device,'revision':revision,
              'signal_check':check,'input_digest':digest,'time_source':'ring-timesync-100ms',
              'workout_id':payload.get('workoutID')}
    for timestamp,bpm,quality,offset in normalized:
        point_metadata=json.dumps({**metadata,'signal_quality':quality},separators=(',',':'))
        db.execute('INSERT INTO points(owner_id,kind,t,value,source,quality,metadata,identity) VALUES(?,?,?,?,?,?,?,?)',
                   (owner,'heart_rate',timestamp,bpm,SOURCE,abs(check),point_metadata,prefix+str(offset)))
    db.execute('INSERT INTO recorded_cardio_runs VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(owner_id,device_id,token) DO UPDATE SET revision=excluded.revision,result_digest=excluded.result_digest,final=excluded.final,available_until=excluded.available_until,signal_check=excluded.signal_check,point_count=excluded.point_count,computed_at=excluded.computed_at',
        (owner,device,token,revision,result_digest,int(final),start,end,check,len(normalized),stamp))


def latest(db,owner):
    row=db.execute('SELECT token,revision,final,started_at,available_until,signal_check,point_count,computed_at FROM recorded_cardio_runs WHERE owner_id=? ORDER BY started_at DESC,computed_at DESC LIMIT 1',(owner,)).fetchone()
    return {**dict(row),'model':MODEL,'source':SOURCE,'experimental':True,'interpolated':True} if row else None


def replay_pending_upgrade(db):
    """Recover new phone events ACKed by an older server that ignored the kind."""
    if db.execute("SELECT 1 FROM homepulse_projection_features WHERE name='recorded-cardio-v1'").fetchone():return
    rows=db.execute("SELECT id,owner_id,device_id,captured_at,payload FROM events WHERE kind='recorded_cardio' ORDER BY owner_id,device_id,json_extract(payload,'$.token'),json_extract(payload,'$.revision') DESC,id DESC")
    done=set()
    for row in rows:
        payload=json.loads(row['payload'])
        if not isinstance(payload.get('token'),str):
            db.execute("UPDATE events SET decode_error='RecordedUpgradeError' WHERE id=?",(row['id'],));continue
        identity=(row['owner_id'],row['device_id'],payload['token'])
        if identity in done:continue
        try:
            project(db,row['owner_id'],row['device_id'],payload,row['captured_at'])
            done.add(identity)
        except (ValueError,TypeError,KeyError,OverflowError):
            db.execute("UPDATE events SET decode_error='RecordedUpgradeError' WHERE id=?",(row['id'],))
    db.execute("INSERT OR IGNORE INTO homepulse_projection_features VALUES('recorded-cardio-v1')")
