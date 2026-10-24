import pytest
from sqlalchemy import select
from app.db import SessionLocal
from app.models import User, Family, Role, AgeGroup
from app.services import messaging as ms

@pytest.mark.asyncio
async def test_child_messages_and_video_call_with_family(kid,anna,bob,monkeypatch):
 # API clients simulate browsers with their event streams connected.
 monkeypatch.setattr(ms.events,"online",lambda uid:True)
 me=(await kid.get('/api/auth/me')).json()
 assert me['features']['messaggi'] is True
 assert 'messaggi' in me['features']['apps']
 contacts=(await kid.get('/api/msg/contacts')).json()
 adult=next(c for c in contacts if c['name']=='Anna')
 response=await kid.post('/api/msg/rooms',json={'members':[adult['id']]})
 assert response.status_code==200,response.text
 room=response.json()['id']
 try:
  response=await kid.post(f'/api/msg/rooms/{room}/messages',json={'text':'Ciao, possiamo parlare?'})
  assert response.status_code==200,response.text
  response=await anna.get(f'/api/msg/rooms/{room}/messages')
  assert response.status_code==200
  assert any(m.get('text')=='Ciao, possiamo parlare?' for m in response.json()['messages'])
  assert (await kid.get('/api/msg/ice')).status_code==200
  response=await kid.post(f'/api/msg/rooms/{room}/call/join',json={'video':True})
  assert response.status_code==200,response.text
  assert response.json()['video'] is True
  assert (await anna.post(f'/api/msg/rooms/{room}/call/join',json={'video':True})).status_code==200
  assert (await kid.post(f'/api/msg/rooms/{room}/call/signal',json={'to':adult['id'],'data':{'type':'offer','sdp':'test-only'}})).status_code==200
  assert (await bob.post(f'/api/msg/rooms/{room}/call/join',json={'video':True})).status_code==403
  assert (await kid.post(f'/api/msg/rooms/{room}/call/leave')).status_code==200
 finally:
  ms.calls.pop(room,None)

@pytest.mark.asyncio
async def test_child_cannot_contact_other_family_and_parent_can_revoke(kid):
 async with SessionLocal() as db:
  child=(await db.execute(select(User).where(User.username=='kid'))).scalar_one()
  cid,previous=child.id,child.apps
  family=Family(name='Other messaging family');db.add(family);await db.flush()
  outsider=User(family_id=family.id,username='message-outsider',display_name='Outside',role=Role.adult,age_group=AgeGroup.adult)
  db.add(outsider);await db.commit();oid=outsider.id
 assert oid not in {c['id'] for c in (await kid.get('/api/msg/contacts')).json()}
 assert (await kid.post('/api/msg/rooms',json={'members':[oid]})).status_code==400
 try:
  async with SessionLocal() as db:
   child=await db.get(User,cid);child.apps=[];await db.commit()
  for path in ('/api/msg/contacts','/api/msg/rooms','/api/msg/ice'):
   assert (await kid.get(path)).status_code==403
  assert (await kid.post('/api/msg/rooms/any/call/join',json={'video':True})).status_code==403
 finally:
  async with SessionLocal() as db:
   child=await db.get(User,cid);child.apps=previous;await db.commit()
