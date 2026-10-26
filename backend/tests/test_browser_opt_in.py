from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from sqlalchemy import select
from app.db import SessionLocal
from app.models import Role, User
from app.services.safe_browser import ai_review_enabled
from app.api import remote_browser


def test_only_explicitly_enabled_child_is_reviewed():
 for role in (Role.adult,Role.admin):
  assert not ai_review_enabled(SimpleNamespace(role=role,policy={'browser_ai_review':True}))
 for policy in (None,{}, {'browser_ai_review':False}):
  assert not ai_review_enabled(SimpleNamespace(role=Role.child,policy=policy))
 assert ai_review_enabled(SimpleNamespace(role=Role.child,policy={'browser_ai_review':True}))

@pytest.mark.asyncio
async def test_parent_switch_and_no_ai_by_default(kid,anna,monkeypatch):
 children=(await anna.get('/api/parental/children')).json()
 child=next(c for c in children if c['display_name']=='Kid');cid=child['id']
 assert child['browser_ai_review'] is False
 async with SessionLocal() as db:
  adult=(await db.execute(select(User).where(User.username=='anna'))).scalar_one();aid=adult.id
 assert (await anna.put('/api/parental/children/'+aid+'/browser-review',json={'enabled':True})).status_code==404
 assert (await kid.put('/api/parental/children/'+cid+'/browser-review',json={'enabled':True})).status_code==403
 page={'url':'https://optin.example.com/one','document_ok':True,'title':'Example','image':'test','width':1280,'height':800,'notice':''}
 fake=AsyncMock(return_value=page);check=AsyncMock()
 monkeypatch.setattr(remote_browser,'worker',fake);monkeypatch.setattr(remote_browser,'review_frame',check)
 monkeypatch.setattr(remote_browser,'child_time',lambda ctx:None)   # il test non dipende dall'ora della fascia consentita
 session=(await kid.post('/api/browser/sessions')).json()['id']
 try:
  assert (await kid.get('/api/browser/sessions/'+session+'/frame')).status_code==200
  assert (await kid.post('/api/browser/sessions/'+session+'/action',json={'action':'click','x':1,'y':1})).status_code==200
  check.assert_not_awaited()
  assert (await anna.put('/api/parental/children/'+cid+'/browser-review',json={'enabled':True})).status_code==200
  assert (await kid.get('/api/browser/sessions/'+session+'/frame')).status_code==200
  page['url']='https://optin.example.com/two'
  assert (await kid.get('/api/browser/sessions/'+session+'/frame')).status_code==200
  assert check.await_count==1
  page['url']='https://another-optin.example.com/'
  assert (await kid.get('/api/browser/sessions/'+session+'/frame')).status_code==200
  assert check.await_count==2
 finally:
  await anna.put('/api/parental/children/'+cid+'/browser-review',json={'enabled':False})
  remote_browser.leases.pop(session,None)

@pytest.mark.asyncio
async def test_weekly_review_off_by_default(admin):
 result=await admin.get('/api/admin/browser/approvals/schedule')
 assert result.status_code==200
 assert result.json()['enabled'] is False

@pytest.mark.asyncio
@pytest.mark.parametrize('username',['anna','admin'])
async def test_adults_never_wait_for_ai_or_child_approvals(seeded,monkeypatch,username):
 from conftest import make_client,session_cookie
 from app.services import browser_approvals
 async with SessionLocal() as db:
  user=(await db.execute(select(User).where(User.username==username))).scalar_one()
  uid,previous=user.id,user.policy
  user.policy={**(previous or {}),'browser_ai_review':True};await db.commit()
 page={'url':'https://adult.example.com/','document_ok':False,'title':'Example','image':'test','width':1280,'height':800,'notice':''}
 fake=AsyncMock(return_value=page)
 check=AsyncMock(side_effect=AssertionError('Adults must never invoke AI'))
 blocked=AsyncMock(side_effect=AssertionError('Child approvals must not apply to adults'))
 monkeypatch.setattr(remote_browser,'worker',fake)
 monkeypatch.setattr(remote_browser,'review_frame',check)
 monkeypatch.setattr(browser_approvals,'check_blocked',blocked)
 session=None
 try:
  async with make_client(await session_cookie(username)) as client:
   session=(await client.post('/api/browser/sessions')).json()['id']
   assert (await client.get('/api/browser/sessions/'+session+'/frame')).status_code==200
   for action in ('click','scroll','key','text'):
    assert (await client.post('/api/browser/sessions/'+session+'/action',json={'action':action})).status_code==200
   check.assert_not_awaited();blocked.assert_not_awaited()
 finally:
  if session:remote_browser.leases.pop(session,None)
  async with SessionLocal() as db:
   user=await db.get(User,uid);user.policy=previous;await db.commit()
