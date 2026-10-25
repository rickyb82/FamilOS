from types import SimpleNamespace
import pytest
from sqlalchemy import select
from app.db import SessionLocal
from app.models import User, Conversation, Artifact
from app.tools.artifacts import update_artifact, list_artifacts

@pytest.mark.asyncio
async def test_current_edits_and_explicit_versions(anna):
    async with SessionLocal() as db:
        user = (await db.execute(select(User).where(User.username == 'anna'))).scalar_one()
        conv = Conversation(family_id=user.family_id, owner_id=user.id, title='Artifact test')
        db.add(conv); await db.flush()
        a = Artifact(family_id=user.family_id, owner_id=user.id, conversation_id=conv.id, title='Page', kind='html', content='original')
        db.add(a); await db.commit()
        aid, cid = a.id, conv.id
        ctx = SimpleNamespace(db=db, user=user, conversation_id=cid)
        await update_artifact({'artifact_id': aid, 'replacements': [{'old':'original','new':'edited'}]}, ctx)
        assert a.version == 1 and a.content == 'edited' and not a.history
        await update_artifact({'artifact_id': aid, 'content':'version two', 'new_version':True}, ctx)
        assert a.version == 2 and a.history[0]['content'] == 'edited'
        result = await list_artifacts({'artifact_id': aid}, ctx)
        assert 'version two' in result.content
    response = await anna.patch('/api/artifacts/'+aid, json={'content':'manual edit'})
    assert response.status_code == 200, response.text
    assert response.json()['version'] == 2
    response = await anna.patch('/api/artifacts/'+aid, json={'content':'explicit new', 'new_version':True})
    assert response.json()['version'] == 3
    previous = await anna.get('/api/artifacts/'+aid+'/versions/2')
    assert previous.json()['content'] == 'manual edit'
