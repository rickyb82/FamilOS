"""Bounded, transient cooperative rooms for a single-node deployment."""
import math
import time
import secrets
from fastapi import HTTPException

rooms={}

def cleanup():
    now=time.monotonic()
    for rid,room in list(rooms.items()):
        room['players']={uid:p for uid,p in room['players'].items() if now-p['seen']<12}
        if now-room['seen']>120: rooms.pop(rid,None)

def create(family_id,game_id,revision,owner):
    cleanup()
    if sum(r['family_id']==family_id for r in rooms.values())>=8: raise HTTPException(429,'Massimo 8 stanze per famiglia')
    if sum(r['owner']==owner for r in rooms.values())>=2: raise HTTPException(429,'Hai già due stanze aperte; attendi che quelle inattive scadano')
    rid=secrets.token_hex(16)
    rooms[rid]={'id':rid,'family_id':family_id,'game_id':game_id,'revision':revision,'owner':owner,'players':{},'seen':time.monotonic()}
    return rooms[rid]

def pose(value):
    result={}
    for key in ('x','y','z','rotation'):
        number=value.get(key,0)
        if isinstance(number,bool) or not isinstance(number,(int,float)) or not math.isfinite(number) or abs(number)>100000: raise HTTPException(400,'Posizione non valida')
        result[key]=float(number)
    action=value.get('action','idle')
    if action not in ('idle','run','jump','swing'): raise HTTPException(400,'Azione non valida')
    result['action']=action
    return result

def sync(room,uid,name,value):
    now=time.monotonic()
    previous=room['players'].get(uid)
    if previous and now-previous['seen']<.08: raise HTTPException(429,'Invii troppo frequenti')
    if not previous and len(room['players'])>=8: raise HTTPException(409,'Stanza piena (8 giocatori)')
    room['players'][uid]={'id':uid,'name':name,'state':pose(value),'seen':now}
    room['seen']=now
    return {'self':uid,'players':[{k:p[k] for k in ('id','name','state')} for key,p in room['players'].items() if key!=uid]}
