import io, json, zipfile
from types import SimpleNamespace
import pytest
from sqlalchemy import select
from app.db import SessionLocal
from app.models import User, Conversation, Artifact
from app.services.game_projects import validate_project
from app.tools.artifacts import create_artifact, update_artifact

PROJECT={'runtime':'javascript','entry':'main.ts','files':{'main.ts':'console.log(1)','lib.ts':'export const n=2'}}
@pytest.mark.parametrize('path',['../escape','/absolute','a/../../b','a\\b','a//b'])
def test_reject_unsafe_paths(path):
    with pytest.raises(ValueError): validate_project({**PROJECT,'files':{**PROJECT['files'],path:'oops'}})
@pytest.mark.parametrize('url',['http://esm.sh/three@0.174.0','https://evil.test/a@1.0.0','https://esm.sh/three@latest','https://esm.sh@evil.test/x@1.0.0'])
def test_reject_untrusted_or_unpinned_dependencies(url):
    with pytest.raises(ValueError): validate_project({**PROJECT,'dependencies':{'three':url}})
@pytest.mark.asyncio
async def test_game_create_patch_versions_export_and_permissions(anna,bob):
    async with SessionLocal() as db:
        u=(await db.execute(select(User).where(User.username=='anna'))).scalar_one()
        c=Conversation(owner_id=u.id,family_id=u.family_id);db.add(c);await db.flush()
        ctx=SimpleNamespace(db=db,user=u,conversation_id=c.id,project_id=None)
        created=await create_artifact({'title':'Gioco','kind':'game','project':PROJECT},ctx)
        assert not created.is_error
        aid=created.artifacts[0]['id']
        result=await update_artifact({'artifact_id':aid,'files':{'main.ts':'console.log(3)'}},ctx)
        assert not result.is_error
        a=await db.get(Artifact,aid)
        assert a.version==1
        assert json.loads(a.content)['files']['lib.ts']=='export const n=2'
        invalid=await update_artifact({'artifact_id':aid,'delete_files':['main.ts']},ctx)
        assert invalid.is_error and 'main.ts' in json.loads(a.content)['files']
    download=await anna.get('/api/artifacts/'+aid+'/project.zip')
    assert download.status_code==200
    with zipfile.ZipFile(io.BytesIO(download.content)) as z:
        assert z.read('src/main.ts')==b'console.log(3)'
        assert 'familos-game.json' in z.namelist()
    assert (await bob.get('/api/artifacts/'+aid+'/project.zip')).status_code in (403,404)
    invalid=await anna.patch('/api/artifacts/'+aid,json={'content':'{}'})
    assert invalid.status_code==400
