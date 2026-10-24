import uuid
import pytest
from sqlalchemy import select
from app.db import SessionLocal
from app.models import User, Role
from app.services.apps import user_apps, admin_apps, CHILD_APP_IDS


def test_child_catalog_respects_parent_selection_without_admin_privileges():
    child = User(role=Role.child, apps=None, admin_apps=['wordcraft'])
    assert user_apps(child) == CHILD_APP_IDS
    assert admin_apps(child) == set()
    child.apps = ['gridcraft', 'mailcraft', 'archivio']
    assert user_apps(child) == {'gridcraft'}
    child.apps = []
    assert user_apps(child) == set()


@pytest.mark.asyncio
async def test_child_can_create_save_and_reopen_own_document(kid, anna):
    me = (await kid.get('/api/auth/me')).json()
    assert set(me['features']['apps']) == CHILD_APP_IDS
    assert me['features']['admin_apps'] == []
    response = await kid.post('/api/files/blank-pdf', json={'filename': 'Compiti.pdf'})
    assert response.status_code == 200, response.text
    fid = uuid.uuid4().hex
    response = await kid.post('/api/files/wordcraft-save', params={'file_id': fid}, headers={'X-Filename': 'compiti.txt'}, content=b'I miei compiti')
    assert response.status_code == 200, response.text
    assert any(f['id'] == fid for f in (await kid.get('/api/files')).json())
    response = await anna.post('/api/files/wordcraft-save', params={'file_id': fid}, headers={'X-Filename': 'compiti.txt'}, content=b'Non autorizzato')
    assert response.status_code in (403, 404)
    assert (await kid.get('/api/auth/apps/mailcraft')).status_code == 403
    assert (await kid.get('/api/admin/updates')).status_code == 403
    assert (await kid.post('/api/auth/device/trust', json={'name': 'altro PC'})).status_code == 403


@pytest.mark.asyncio
async def test_parent_can_revoke_child_app_access(kid, admin):
    async with SessionLocal() as db:
        child = (await db.execute(select(User).where(User.username == 'kid'))).scalar_one()
        cid, previous = child.id, child.apps
    try:
        assert (await admin.patch(f'/api/admin/members/{cid}', json={'apps': []})).status_code == 200
        assert (await kid.get('/api/auth/apps/gridcraft')).status_code == 403
        assert (await kid.post('/api/files/blank-pdf', json={})).status_code == 403
        assert (await kid.post('/api/files/wordcraft-save', params={'file_id': uuid.uuid4().hex}, headers={'X-Filename': 'test.txt'}, content=b'test')).status_code == 403
    finally:
        async with SessionLocal() as db:
            child = await db.get(User, cid)
            child.apps = previous
            await db.commit()
