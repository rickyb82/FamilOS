import pytest
from sqlalchemy import select
from app.db import SessionLocal
from app.models import Conversation, Project, FileAsset, User, Role, Family, AgeGroup
from app.services.access import access_level

@pytest.mark.asyncio
@pytest.mark.parametrize('rtype,model,fields',[
 ('conversation',Conversation,{'title':'Chat del bambino'}),
 ('project',Project,{'name':'Compiti'}),
 ('file',FileAsset,{'filename':'compiti.txt','mime':'text/plain','path':'test-only'})])
async def test_child_shares_own_resource_with_adult_and_revokes(kid,anna,rtype,model,fields):
 async with SessionLocal() as db:
  child=(await db.execute(select(User).where(User.username=='kid'))).scalar_one()
  adult=(await db.execute(select(User).where(User.username=='anna'))).scalar_one()
  resource=model(family_id=child.family_id,owner_id=child.id,**fields);db.add(resource);await db.commit()
  rid,aid=resource.id,adult.id
  assert await access_level(db,rtype,resource,adult) is None
 response=await kid.post('/api/shares',json={'resource_type':rtype,'resource_id':rid,'target_user_id':aid,'permission':'read'})
 assert response.status_code==200,response.text
 async with SessionLocal() as db:
  assert await access_level(db,rtype,await db.get(model,rid),await db.get(User,aid))=='read'
 assert (await kid.delete('/api/shares/'+response.json()['id'])).status_code==200
 async with SessionLocal() as db:
  assert await access_level(db,rtype,await db.get(model,rid),await db.get(User,aid)) is None

@pytest.mark.asyncio
async def test_child_cannot_share_to_children_inactive_or_other_family(kid):
 async with SessionLocal() as db:
  child=(await db.execute(select(User).where(User.username=='kid'))).scalar_one()
  other=Family(name='Other');db.add(other);await db.flush()
  targets=[User(family_id=child.family_id,username='share-child',display_name='Child',role=Role.child,age_group=AgeGroup.kid),User(family_id=child.family_id,username='share-inactive',display_name='Inactive',role=Role.adult,age_group=AgeGroup.adult,is_active=False),User(family_id=other.id,username='share-outside',display_name='Outside',role=Role.adult,age_group=AgeGroup.adult)]
  db.add_all(targets);resource=Conversation(family_id=child.family_id,owner_id=child.id);db.add(resource);await db.commit()
  rid=resource.id;ids=[u.id for u in targets]
 for target in [None,*ids]:
  response=await kid.post('/api/shares',json={'resource_type':'conversation','resource_id':rid,'target_user_id':target,'permission':'read'})
  assert response.status_code in (403,404),response.text
 members=(await kid.get('/api/shares/members')).json()
 assert all(m['role'] in ('admin','adult') for m in members)
 assert not set(ids)&{m['id'] for m in members}

@pytest.mark.asyncio
async def test_explicit_parent_share_restriction_is_preserved(kid):
 async with SessionLocal() as db:
  child=(await db.execute(select(User).where(User.username=='kid'))).scalar_one()
  cid,previous=child.id,child.policy;child.policy={**(previous or {}),'can_share':False};await db.commit()
 try:
  response=await kid.post('/api/shares',json={'resource_type':'conversation','resource_id':'any','target_user_id':'any'})
  assert response.status_code==403
 finally:
  async with SessionLocal() as db:
   child=await db.get(User,cid);child.policy=previous;await db.commit()
