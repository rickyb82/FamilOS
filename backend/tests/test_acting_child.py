import pytest
from sqlalchemy import select
from app.db import SessionLocal
from app.models import User,Role,Family

@pytest.mark.asyncio
async def test_admin_enters_child_and_returns_without_elevating_child_session(admin,kid,anna):
    original=(await admin.get('/api/auth/me')).json()['user']['id']
    child=(await kid.get('/api/auth/me')).json()['user']['id']
    assert (await anna.post('/api/auth/acting-child/start',json={'user_id':child})).status_code==403
    assert (await kid.post('/api/auth/acting-child/stop')).status_code==403
    assert (await admin.post('/api/auth/acting-child/start',json={'user_id':child})).status_code==200
    me=(await admin.get('/api/auth/me')).json()
    assert me['user']['id']==child and me['user']['role']=='child' and me['acting_as_child']
    assert (await admin.get('/api/admin/members')).status_code==403
    assert (await admin.post('/api/auth/acting-child/start',json={'user_id':child})).status_code==403
    assert (await kid.get('/api/auth/me')).json()['acting_as_child'] is False
    assert (await admin.post('/api/auth/acting-child/stop')).status_code==200
    me=(await admin.get('/api/auth/me')).json()
    assert me['user']['id']==original and not me['acting_as_child']

@pytest.mark.asyncio
async def test_rejects_adults_other_families_and_csrf(admin,anna,kid):
    adult=(await anna.get('/api/auth/me')).json()['user']['id']
    child=(await kid.get('/api/auth/me')).json()['user']['id']
    assert (await admin.post('/api/auth/acting-child/start',json={'user_id':adult})).status_code==404
    async with SessionLocal() as db:
        fam=Family(name='Other impersonation');db.add(fam);await db.flush()
        other=User(family_id=fam.id,username='other-acting-child',display_name='Other',role=Role.child)
        db.add(other);await db.commit();oid=other.id
    assert (await admin.post('/api/auth/acting-child/start',json={'user_id':oid})).status_code==404
    assert (await admin.post('/api/auth/acting-child/start',json={'user_id':child},headers={'X-Requested-With':'invalid'})).status_code==403

@pytest.mark.asyncio
async def test_can_return_when_child_is_disabled(admin,kid):
    child=(await kid.get('/api/auth/me')).json()['user']['id']
    assert (await admin.post('/api/auth/acting-child/start',json={'user_id':child})).status_code==200
    async with SessionLocal() as db:
        user=await db.get(User,child);user.is_active=False;await db.commit()
    try:
        assert (await admin.get('/api/auth/me')).status_code==403
        assert (await admin.post('/api/auth/acting-child/stop')).status_code==200
        assert (await admin.get('/api/auth/me')).json()['user']['role']=='admin'
    finally:
        async with SessionLocal() as db:
            user=await db.get(User,child);user.is_active=True;await db.commit()
