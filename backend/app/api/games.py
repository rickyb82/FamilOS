"""Personal Giochi folder: saved projects independent from conversations."""
import json
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select, func, or_, update
from sqlalchemy.ext.asyncio import AsyncSession
from ..db import get_db
from ..models import Artifact, SavedGame, User, Role
from ..security.sessions import AuthContext, require_auth, require_adult
from ..services.access import require_access
from ..services.game_projects import encode_project

router=APIRouter(prefix='/games',tags=['games'])
class SaveGame(BaseModel):
    artifact_id: str | None = None
    content: str | None = None
    title: str = Field(default='Il mio gioco',min_length=1,max_length=200)

def summary(g):
    return {'id':g.id,'title':g.title,'runtime':g.runtime,'sharing':g.sharing,'owner_id':g.owner_id,'revision':g.revision,'updated_at':g.updated_at.isoformat()}

@router.get('')
async def list_games(ctx:AuthContext=Depends(require_auth), db:AsyncSession=Depends(get_db)):
    visibility = or_(SavedGame.owner_id==ctx.user.id,SavedGame.sharing=='approved')
    if ctx.user.role != Role.child: visibility=or_(visibility,SavedGame.sharing=='pending')
    rows=(await db.execute(select(SavedGame.id,SavedGame.title,SavedGame.runtime,SavedGame.updated_at,SavedGame.sharing,SavedGame.owner_id,SavedGame.revision).where(SavedGame.family_id==ctx.user.family_id,visibility).order_by(SavedGame.updated_at.desc()))).all()
    return [summary(g) for g in rows]

@router.post('')
async def save_game(body:SaveGame,ctx:AuthContext=Depends(require_auth),db:AsyncSession=Depends(get_db)):
    source=None
    if body.artifact_id:
        source=await db.get(Artifact,body.artifact_id)
        if not source: raise HTTPException(404)
        await require_access(db,'conversation',source.conversation_id,ctx.user)
        if source.kind!='game': raise HTTPException(400,'Questo artifact non è un gioco')
    try: content=encode_project(source.content if source else body.content or '')
    except (ValueError,TypeError) as exc: raise HTTPException(400,str(exc))
    saved=None
    if source:
        saved=(await db.execute(select(SavedGame).where(SavedGame.owner_id==ctx.user.id,SavedGame.source_artifact_id==source.id))).scalars().first()
    if not saved:
        count=(await db.execute(select(func.count()).select_from(SavedGame).where(SavedGame.owner_id==ctx.user.id))).scalar_one()
        if count>=100: raise HTTPException(400,'La cartella contiene già 100 giochi')
        saved=SavedGame(owner_id=ctx.user.id,family_id=ctx.user.family_id,source_artifact_id=source.id if source else None)
        db.add(saved)
    if saved.content is not None and (saved.content != content or saved.title != (source.title if source else body.title)):
        saved.revision += 1
        saved.sharing = 'private'
    saved.title=source.title if source else body.title
    saved.content=content;saved.runtime=json.loads(content)['runtime']
    await db.commit()
    return summary(saved)

@router.get('/{gid}')
async def get_game(gid:str,ctx:AuthContext=Depends(require_auth),db:AsyncSession=Depends(get_db)):
    game=await db.get(SavedGame,gid)
    if not game or game.family_id!=ctx.user.family_id or not (game.owner_id==ctx.user.id or game.sharing=='approved' or (game.sharing=='pending' and ctx.user.role!=Role.child)): raise HTTPException(404)
    return {**summary(game),'content':game.content}


@router.post('/{gid}/share')
async def share_game(gid:str,ctx:AuthContext=Depends(require_auth),db:AsyncSession=Depends(get_db)):
    game=await db.get(SavedGame,gid)
    if not game or game.owner_id!=ctx.user.id or game.family_id!=ctx.user.family_id: raise HTTPException(404)
    if game.sharing != 'private': return summary(game)
    game.sharing='pending' if ctx.user.role==Role.child else 'approved'
    await db.commit()
    if game.sharing=='pending':
        from ..services.events import notify
        adults=(await db.execute(select(User).where(User.family_id==ctx.user.family_id,User.role.in_([Role.admin,Role.adult]),User.is_active==True))).scalars().all()
        for adult in adults:
            await notify(db,adult.id,'game_review',f'{ctx.user.display_name} chiede di condividere il gioco {game.title}.','/games',ctx.user.display_name)
    return summary(game)

class ReviewGame(BaseModel):
    approve: bool
    revision: int

@router.post('/{gid}/review')
async def review_game(gid:str,body:ReviewGame,ctx:AuthContext=Depends(require_adult),db:AsyncSession=Depends(get_db)):
    game=await db.get(SavedGame,gid)
    if not game or game.family_id!=ctx.user.family_id: raise HTTPException(404)
    if game.sharing!='pending' or game.revision!=body.revision: raise HTTPException(409,'Il progetto è cambiato: rileggilo prima di approvare')
    result=await db.execute(update(SavedGame).where(SavedGame.id==gid,SavedGame.revision==body.revision,SavedGame.sharing=='pending').values(sharing='approved' if body.approve else 'private'))
    if result.rowcount!=1: raise HTTPException(409,'Il progetto è cambiato: ripeti la verifica')
    await db.commit()
    await db.refresh(game)
    from ..services.events import notify
    await notify(db,game.owner_id,'game_review','Condivisione '+('approvata' if body.approve else 'non approvata')+': '+game.title,'/games',ctx.user.display_name)
    return summary(game)

@router.post('/{gid}/unshare')
async def unshare_game(gid:str,ctx:AuthContext=Depends(require_auth),db:AsyncSession=Depends(get_db)):
    game=await db.get(SavedGame,gid)
    if not game or game.family_id!=ctx.user.family_id or (game.owner_id!=ctx.user.id and ctx.user.role==Role.child): raise HTTPException(404)
    game.sharing='private'
    await db.commit()
    return summary(game)


@router.post('/templates/{template}')
async def add_template(template:str,ctx:AuthContext=Depends(require_auth),db:AsyncSession=Depends(get_db)):
    from pathlib import Path
    if template!='ragnoman': raise HTTPException(404)
    folder=Path(__file__).resolve().parents[1]/'game_templates'
    if not folder.exists(): folder=Path(__file__).resolve().parents[3]/'docs/games'
    content=(folder/'ragnoman-rooftops.json').read_text()
    return await save_game(SaveGame(content=content,title='Ragnoman Rooftops'),ctx,db)
