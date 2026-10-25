import json
import pytest
from sqlalchemy import select
from app.db import SessionLocal
from app.models import User, Conversation, Artifact, SavedGame, Family
from app.services import game_rooms
PROJECT={'runtime':'javascript','entry':'main.js','files':{'main.js':'console.log(1)'}}

@pytest.mark.asyncio
async def test_private_child_games_review_multiplayer_and_revision(kid,anna,bob):
    game_rooms.rooms.clear()
    async with SessionLocal() as db:
        child=(await db.execute(select(User).where(User.username=='kid'))).scalar_one()
        conv=Conversation(owner_id=child.id,family_id=child.family_id);db.add(conv);await db.flush()
        artifact=Artifact(owner_id=child.id,family_id=child.family_id,conversation_id=conv.id,title='Mondo',kind='game',content=json.dumps(PROJECT));db.add(artifact);await db.commit();aid,cid=artifact.id,conv.id
    response=await kid.post('/api/games',json={'artifact_id':aid});assert response.status_code==200,response.text
    saved=response.json();gid=saved['id']
    assert saved['sharing']=='private'
    assert (await bob.get('/api/games/'+gid)).status_code==404
    assert (await kid.post('/api/game-rooms',json={'game_id':gid})).status_code==403
    assert (await kid.post('/api/games/'+gid+'/share')).json()['sharing']=='pending'
    assert (await kid.post('/api/games/'+gid+'/review',json={'approve':True,'revision':1})).status_code==403
    assert (await anna.get('/api/games/'+gid)).status_code==200
    assert (await anna.post('/api/games/'+gid+'/review',json={'approve':True,'revision':1})).status_code==200
    room=(await kid.post('/api/game-rooms',json={'game_id':gid})).json()['id']
    first=await kid.post('/api/game-rooms/'+room+'/state',json={'x':1,'action':'run'});assert first.status_code==200
    second=await anna.post('/api/game-rooms/'+room+'/state',json={'x':2,'action':'jump'});assert second.status_code==200
    assert second.json()['players'][0]['state']['x']==1
    assert len(second.json()['players'])==1
    assert (await bob.post('/api/game-rooms/'+room+'/state',json={'x':999999})).status_code==400
    async with SessionLocal() as db:
        a=await db.get(Artifact,aid);a.content=json.dumps({**PROJECT,'files':{'main.js':'console.log(2)'}});await db.commit()
    changed=(await kid.post('/api/games',json={'artifact_id':aid})).json()
    assert changed['id']==gid and changed['sharing']=='private' and changed['revision']==2
    assert (await anna.post('/api/game-rooms/'+room+'/state',json={})).status_code==403
    await kid.post('/api/games/'+gid+'/share')
    assert (await anna.post('/api/games/'+gid+'/review',json={'approve':True,'revision':1})).status_code==409
    # Independent snapshot survives removal of its source.
    async with SessionLocal() as db:
        await db.delete(await db.get(Artifact,aid));await db.delete(await db.get(Conversation,cid));await db.commit()
    assert (await kid.get('/api/games/'+gid)).status_code==200

@pytest.mark.asyncio
async def test_other_family_cannot_read_review_or_join(anna):
    async with SessionLocal() as db:
        fam=Family(name='Separate');db.add(fam);await db.flush()
        owner=User(family_id=fam.id,username='other-game-owner',display_name='Other');db.add(owner);await db.flush()
        game=SavedGame(owner_id=owner.id,family_id=fam.id,title='Private world',runtime='javascript',content=json.dumps(PROJECT),sharing='approved');db.add(game);await db.commit()
        gid=game.id;room=game_rooms.create(fam.id,gid,1,owner.id)['id']
    assert (await anna.get('/api/games/'+gid)).status_code==404
    assert (await anna.post('/api/games/'+gid+'/review',json={'approve':True,'revision':1})).status_code==404
    assert (await anna.post('/api/game-rooms/'+room+'/state',json={})).status_code==404
    assert gid not in [g['id'] for g in (await anna.get('/api/games')).json()]

@pytest.mark.parametrize('state',[{'x':float('nan')},{'z':float('inf')},{'action':'execute'},{'y':100001}])
def test_invalid_player_states(state):
    from fastapi import HTTPException
    with pytest.raises(HTTPException):game_rooms.pose(state)

@pytest.mark.asyncio
async def test_builtin_game_is_private_and_contains_multiplayer_sdk(kid):
    response=await kid.post('/api/games/templates/ragnoman')
    assert response.status_code==200,response.text
    game=response.json()
    assert game['sharing']=='private' and game['title']=='Ragnoman Rooftops'
    saved=(await kid.get('/api/games/'+game['id'])).json()
    project=json.loads(saved['content'])
    assert project['runtime']=='javascript'
    assert 'game.multiplayer' in project['files'][project['entry']]
    assert (await kid.post('/api/games/templates/unknown')).status_code==404

@pytest.mark.asyncio
async def test_child_can_create_game_without_web_or_server_execution(kid):
    from app.policy.permissions import effective_policy, tool_allowed
    from app.tools.artifacts import create_artifact
    from app.tools.registry import ToolContext
    async with SessionLocal() as db:
        child=(await db.execute(select(User).where(User.username=='kid'))).scalar_one()
        policy=effective_policy(child)
        policy['allowed_tools']=['artifacts']
        assert tool_allowed(policy,'create_artifact')
        assert not tool_allowed(policy,'web_search')
        assert not tool_allowed(policy,'run_code')
        conv=Conversation(owner_id=child.id,family_id=child.family_id)
        db.add(conv);await db.flush()
        result=await create_artifact({'kind':'game','title':'Gioco personale','project':PROJECT},ToolContext(user=child,policy=policy,db=db,conversation_id=conv.id))
        assert not result.is_error
        assert result.artifacts[0]['kind']=='game'
