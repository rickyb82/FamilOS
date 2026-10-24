import base64
from io import BytesIO
import json
from pathlib import Path
import sys
import time
from types import SimpleNamespace

import httpx
from PIL import Image
import pytest
from sqlalchemy import select

from app.api import installed_apps as api
from app.db import SessionLocal
from app.models import Session, User
from app.security.sessions import SESSION_COOKIE

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'extras/updater'))
sys.path.insert(0,str(ROOT/'packages/app-sdk'))
from catalog import Catalog

@pytest.fixture
def item(monkeypatch):
    manifest=json.loads((ROOT/'packages/app-sdk/examples/app.json').read_text())
    doc={'manifest':manifest,'enabled':True,'users':[],'icon':''}
    monkeypatch.setattr(api,'registry',lambda:{'document-editor':doc})
    api.leases.clear()
    return doc

@pytest.mark.asyncio
async def test_access_defaults_and_disabled(admin,anna,kid,item):
    assert len((await admin.get('/api/installed-apps')).json())==1
    assert (await anna.get('/api/installed-apps')).json()==[]
    for client in (anna,kid):
        assert (await client.post('/api/installed-apps/document-editor/launch')).status_code==403
    item['enabled']=False
    assert (await admin.post('/api/installed-apps/document-editor/launch')).status_code==403

@pytest.mark.asyncio
async def test_admin_mutation_confirmation_and_png(admin,anna,item,monkeypatch):
    async def forbidden(*args):pytest.fail('Must not reach host')
    monkeypatch.setattr(api,'agent_request',forbidden)
    payload={'manifest':item['manifest'],'icon':'not png','confirm_maintenance':True}
    assert (await admin.post('/api/admin/app-catalog/install',json=payload)).status_code==400
    assert (await anna.post('/api/admin/app-catalog/install',json=payload)).status_code==403
    assert (await admin.post('/api/admin/app-catalog/document-editor/action',json={'action':'stop'})).status_code==400

@pytest.mark.asyncio
async def test_import_and_child_grant(admin,item,monkeypatch):
    calls=[]
    async def host(*args):calls.append(args);return {'status':'running'}
    monkeypatch.setattr(api,'agent_request',host)
    png=BytesIO();Image.new('RGB',(16,16)).save(png,format='PNG')
    payload={'manifest':item['manifest'],'icon':base64.b64encode(png.getvalue()).decode(),'confirm_maintenance':True}
    assert (await admin.post('/api/admin/app-catalog/install',json=payload)).status_code==200
    assert calls[0][2]['action']=='app-install'
    async with SessionLocal() as db:
        kid=(await db.execute(select(User).where(User.username=='kid'))).scalar_one()
        payload['users']=[kid.id]
    assert (await admin.post('/api/admin/app-catalog/install',json=payload)).status_code==400
    assert len(calls)==1

@pytest.mark.asyncio
async def test_gateway_scope_headers_and_session_revocation(admin,item,monkeypatch):
    response=await admin.post('/api/installed-apps/document-editor/launch')
    assert response.status_code==200
    url=response.json()['url']
    preflight=await admin.options(url+'note',headers={'Origin':'null','Access-Control-Request-Method':'PUT','Access-Control-Request-Headers':'content-type'})
    assert preflight.status_code==204
    assert preflight.headers['access-control-allow-origin']=='null'
    original=httpx.AsyncClient
    seen=[]
    def handler(request):
        seen.append(request)
        assert request.url.host=='app-document-editor-web'
        assert 'cookie' not in request.headers and 'authorization' not in request.headers
        assert request.headers['x-familos-user']
        return httpx.Response(200,text='<h1>App</h1>',headers={'Content-Type':'text/html','Set-Cookie':'evil=1','Access-Control-Allow-Origin':'*'})
    monkeypatch.setattr(api.httpx,'AsyncClient',lambda **kw:original(transport=httpx.MockTransport(handler),**kw))
    r=await admin.get(url,headers={'Cookie':'fake=x'})
    assert r.status_code==200
    assert 'allow-same-origin' not in r.headers['content-security-policy']
    assert 'set-cookie' not in r.headers
    assert r.headers['access-control-allow-origin']=='null'
    assert (await admin.get(url.replace('document-editor','other-app',1))).status_code==401
    async with SessionLocal() as db:
        session=await db.get(Session,admin.cookies.get(SESSION_COOKIE));session.screen_locked=True;await db.commit()
    assert (await admin.get(url)).status_code==423
    assert len(seen)==1

@pytest.mark.asyncio
async def test_gateway_stale_and_revoked_user(admin,anna,item):
    async with SessionLocal() as db:
        user=(await db.execute(select(User).where(User.username=='anna'))).scalar_one()
        item['users']=[user.id]
    url=(await anna.post('/api/installed-apps/document-editor/launch')).json()['url']
    item['users']=[]
    assert (await anna.get(url)).status_code==403
    for lease in api.leases.values():lease.expires=0
    assert (await admin.get(url)).status_code==401

@pytest.mark.asyncio
async def test_gateway_no_redirects(admin,item,monkeypatch):
    url=(await admin.post('/api/installed-apps/document-editor/launch')).json()['url']
    original=httpx.AsyncClient
    monkeypatch.setattr(api.httpx,'AsyncClient',lambda **kw:original(transport=httpx.MockTransport(lambda _:httpx.Response(302,headers={'Location':'http://169.254.169.254/'})),**kw))
    assert (await admin.get(url)).status_code==502
    assert (await admin.get(url,headers={'Origin':'https://evil.example'})).status_code==403


def test_host_manifest_rejects_permissions_and_escalation(tmp_path,monkeypatch):
    monkeypatch.setattr('catalog.platform.machine',lambda:'x86_64')
    worker=SimpleNamespace(root=tmp_path,platform=tmp_path)
    catalog=Catalog(worker)
    doc=json.loads((ROOT/'packages/app-sdk/examples/app.json').read_text())
    assert catalog.validate(doc)['id']=='document-editor'
    for field,value in [('network','internet'),('documents','user-selected-read'),('children',True)]:
        changed=json.loads(json.dumps(doc));changed['permissions'][field]=value
        with pytest.raises(ValueError):catalog.validate(changed)
    doc['services']['web']['privileged']=True
    with pytest.raises(ValueError):catalog.validate(doc)


def test_host_compose_isolated_and_persistent(tmp_path,monkeypatch):
    monkeypatch.setattr('catalog.os.chown',lambda *args:None)
    catalog=Catalog(SimpleNamespace(root=tmp_path,platform=tmp_path))
    manifest=json.loads((ROOT/'packages/app-sdk/examples/app.json').read_text())
    cfg=catalog.compose({'document-editor':{'manifest':manifest,'enabled':True}})
    service=cfg['services']['app-document-editor-web']
    assert 'ports' not in service and 'privileged' not in service
    assert service['read_only'] is True and service['cap_drop']==['ALL']
    assert cfg['networks']['app-document-editor']['internal'] is True
    assert all(str(tmp_path) in volume for volume in service['volumes'])


def test_log_filter_redacts_capability():
    import logging
    record=logging.LogRecord('uvicorn.access',20,'',0,'%s',('/api/app-gateway/example/secret-token/file?x=1',),None)
    api.GatewayLogFilter().filter(record)
    assert 'secret-token' not in record.getMessage()

@pytest.mark.parametrize('failure',[None,'health'])
def test_catalog_transaction_and_rollback(tmp_path,monkeypatch,failure):
    import shutil
    monkeypatch.setattr('catalog.os.chown',lambda *a:None)
    monkeypatch.setattr('catalog.platform.machine',lambda:'x86_64')
    monkeypatch.setattr('catalog.time.sleep',lambda *_:None)
    platform=tmp_path/'platform';root=platform/'apps/familos';root.mkdir(parents=True)
    (root/'data').mkdir();(platform/'.env').write_text('COMPOSE_FILE="compose.yaml:compose.familos.yaml"\n')
    monkeypatch.setattr('catalog.ORBITA_LOCK',tmp_path/'migration.lock')
    stages=[];commands=[]
    worker=SimpleNamespace(root=root,platform=platform,directory=tmp_path/'state',state={'job':{'id':'a'*32}})
    worker.directory.mkdir()
    worker.phase=lambda message,**kw:stages.append(message)
    def run(*args):
        commands.append(args)
        if args[:3]==('docker','image','inspect'):return '[{"Config": {}}]'
        if args[:2]==('docker','inspect'):return '[{"State":{"ExitCode":0,"OOMKilled":false}}]'
        if args[:2]==('cp','-a'):shutil.copytree(args[2],args[3]);return ''
        return ''
    def compose(*args):
        commands.append(args)
        if args[:2]==('ps','-aq'):return 'test-container'
        if args[:2]==('exec','-T') and failure=='health':raise RuntimeError('health failed')
        return ''
    def backup(*args):
        commands.append(('backup',))
        raise AssertionError('Automatic backup must not run')
    worker.run=run;worker.compose=compose;worker.execute=backup
    catalog=Catalog(worker)
    manifest=json.loads((ROOT/'packages/app-sdk/examples/app.json').read_text())
    old={'manifest':manifest,'enabled':True,'users':[],'icon':''}
    catalog.save({manifest['id']:old});catalog.publish({manifest['id']:old})
    catalog.compose_file.write_text(json.dumps(catalog.compose({manifest['id']:old})))
    old_compose=catalog.compose_file.read_bytes();old_env=(platform/'.env').read_bytes()
    data=catalog.directory/manifest['id']/'data/documents';data.mkdir(parents=True,exist_ok=True)
    (data/'notes.txt').write_text('important data')
    updated=json.loads(json.dumps(manifest));updated['version']='1.0.1';updated['services']['web']['image']=updated['services']['web']['image'].replace('0'*64,'1'*64)
    png=BytesIO();Image.new('RGB',(1,1)).save(png,format='PNG')
    body={'action':'app-install','target':manifest['id'],'manifest':updated,'icon':base64.b64encode(png.getvalue()).decode(),'confirm_maintenance':True}
    if failure:
        with pytest.raises(RuntimeError):catalog.execute(body)
        assert catalog.compose_file.read_bytes()==old_compose
        assert (platform/'.env').read_bytes()==old_env
        assert catalog.read()[manifest['id']]['manifest']['version']=='1.0.0'
    else:
        catalog.execute(body)
        assert catalog.read()[manifest['id']]['manifest']['version']=='1.0.1'
        assert ('backup',) not in commands
    assert (data/'notes.txt').read_text()=='important data'
