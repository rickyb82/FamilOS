from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from ..db import get_db
from ..models import SavedGame
from ..security.sessions import AuthContext, require_auth
from ..services import game_rooms as service
router=APIRouter(prefix='/game-rooms',tags=['game-rooms'])

async def allowed(db,gid,ctx):
    game=await db.get(SavedGame,gid)
    if not game or game.family_id!=ctx.user.family_id or game.sharing!='approved': raise HTTPException(403,'Il gioco deve essere approvato per la famiglia')
    return game

@router.get('')
async def list_rooms(ctx:AuthContext=Depends(require_auth),db:AsyncSession=Depends(get_db)):
    service.cleanup();result=[]
    for room in list(service.rooms.values()):
        if room['family_id']!=ctx.user.family_id: continue
        game=await db.get(SavedGame,room['game_id'])
        if game and game.sharing=='approved' and game.revision==room['revision']:
            result.append({'id':room['id'],'game_id':game.id,'title':game.title,'players':len(room['players'])})
    return result

class CreateRoom(BaseModel):
    game_id:str
@router.post('')
async def create_room(body:CreateRoom,ctx:AuthContext=Depends(require_auth),db:AsyncSession=Depends(get_db)):
    game=await allowed(db,body.game_id,ctx)
    room=service.create(ctx.user.family_id,game.id,game.revision,ctx.user.id)
    return {'id':room['id'],'game_id':game.id}

class State(BaseModel):
    x:float=0
    y:float=0
    z:float=0
    rotation:float=0
    action:str='idle'
@router.post('/{rid}/state')
async def sync_room(rid:str,body:State,ctx:AuthContext=Depends(require_auth),db:AsyncSession=Depends(get_db)):
    service.cleanup();room=service.rooms.get(rid)
    if not room or room['family_id']!=ctx.user.family_id: raise HTTPException(404)
    game=await allowed(db,room['game_id'],ctx)
    if game.revision!=room['revision']: raise HTTPException(409,'Il gioco è stato aggiornato: apri una nuova stanza')
    return service.sync(room,ctx.user.id,ctx.user.display_name,body.model_dump())

@router.delete('/{rid}/membership')
async def leave_room(rid:str,ctx:AuthContext=Depends(require_auth)):
    room=service.rooms.get(rid)
    if room and room['family_id']==ctx.user.family_id: room['players'].pop(ctx.user.id,None)
    return {'ok':True}
