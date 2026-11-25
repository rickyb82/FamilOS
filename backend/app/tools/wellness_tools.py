"""Personal workout tools: fixed current owner; no credentials or raw journals."""
import asyncio
import json
from sqlalchemy import select,or_
from ..models import Role,Conversation,Share
from ..services.apps import app_enabled
from ..services.homepulse import module
from ..services.homepulse.store import StoreError
from .registry import ToolContext,ToolResult,register


async def guard(ctx):
    if ctx.user.role==Role.child or not getattr(ctx.user,'is_active',True) or not module.enabled() or not app_enabled(ctx.user,'wellness'):
        return 'Modulo Allenamento non disponibile per questo profilo.'
    if not ctx.conversation_id:return 'Usa una chat personale per i dati di allenamento.'
    conversation=await ctx.db.get(Conversation,ctx.conversation_id)
    if not conversation or conversation.owner_id!=ctx.user.id:return 'Usa una tua chat personale.'
    scopes=[(Share.resource_type=='conversation') & (Share.resource_id==conversation.id)]
    if conversation.project_id:scopes.append((Share.resource_type=='project') & (Share.resource_id==conversation.project_id))
    if (await ctx.db.execute(select(Share.id).where(or_(*scopes)).limit(1))).scalar_one_or_none():
        return 'I dati di allenamento si consultano in una chat personale non condivisa.'
    return None


def result(value):return ToolResult(json.dumps(value,ensure_ascii=False,allow_nan=False))
def archive():
    from ..api.wellness import store
    return store()


@register('allenamento_stato','Legge soltanto lo stato, la scheda e le ultime sessioni di allenamento dell’adulto che scrive, in chat personale. Non restituisce credenziali o dati di altre persone.',{'type':'object','properties':{},'additionalProperties':False})
async def state(args:dict,ctx:ToolContext):
    error=await guard(ctx)
    if error:return ToolResult(error,is_error=True)
    value=await asyncio.to_thread(archive().state,ctx.user.id)
    return result({key:value[key] for key in ('server_time','settings','latest','active_session','sessions','weekly_occurrences','recorded_cardio')})


@register('allenamento_scheda','Su richiesta esplicita dell’utente aggiorna la sua scheda: nome attività, giri e fasi con durata, FC minima/massima in BPM e MET facoltativi. Non inventare fasce cardio; chiedi i valori mancanti. Vale per nuove sessioni; le sessioni già iniziate conservano la scheda originale.',{'type':'object','properties':{'config':{'type':'object','properties':{'activity_name':{'type':'string'},'rounds':{'type':'integer'},'work':{'type':'integer'},'rest':{'type':'integer'},'plan':{'type':'array','items':{'type':'object','properties':{'name':{'type':'string'},'duration':{'type':'integer'},'target_min':{'type':['integer','null']},'target_max':{'type':['integer','null']},'met':{'type':['number','null']}},'required':['name','duration'],'additionalProperties':False}}},'additionalProperties':False}},'required':['config'],'additionalProperties':False})
async def plan(args:dict,ctx:ToolContext):
    error=await guard(ctx)
    if error:return ToolResult(error,is_error=True)
    if set(args)!={'config'} or not isinstance(args['config'],dict) or set(args['config'])-{'activity_name','rounds','work','rest','plan'}:
        return ToolResult('Configurazione scheda non valida.',is_error=True)
    try:
        await asyncio.to_thread(archive().update_settings,ctx.user.id,{'config':args['config']})
        return result({'saved':True,'config':archive().settings(ctx.user.id)['config'],'applies_to':'nuove sessioni'})
    except (StoreError,ValueError):return ToolResult('Controlla durate, giri e intervalli BPM: minimo e massimo devono essere entrambi presenti e ordinati.',is_error=True)


@register('allenamento_analisi','Legge grafici e statistiche della sessione personale selezionata, inclusi tempo sotto/dentro/sopra fascia, copertura e tempo senza misure. Distingui le stime cardio registrate sperimentali e i risultati provvisori tramite cardio_provenance. Non attribuire i vuoti a recupero o a una fascia. L’ID deve provenire da allenamento_stato.',{'type':'object','properties':{'session_id':{'type':'string'}},'required':['session_id'],'additionalProperties':False})
async def analysis(args:dict,ctx:ToolContext):
    error=await guard(ctx)
    if error:return ToolResult(error,is_error=True)
    if not isinstance(args.get('session_id'),str) or not args['session_id'] or args['session_id']=='last24h':return ToolResult('Scegli una sessione personale.',is_error=True)
    try:
        value=await asyncio.to_thread(archive().analysis,ctx.user.id,args['session_id'])
        return result({key:value[key] for key in ('activity_name','window_start','workout_end','stats','cardio','steps_in_records','cardio_provenance')})
    except (StoreError,ValueError):return ToolResult('Sessione non trovata nel tuo profilo.',is_error=True)


@register('allenamento_controlla','Avvia, mette in pausa, riprende o termina il proprio allenamento SOLO su richiesta esplicita dell’utente. Usa l’ID della sua sessione attiva; non modificare altri profili.',{'type':'object','properties':{'action':{'enum':['start','pause','resume','end']},'session_id':{'type':'string'},'activity_name':{'type':'string'}},'required':['action'],'additionalProperties':False})
async def control(args:dict,ctx:ToolContext):
    error=await guard(ctx)
    if error:return ToolResult(error,is_error=True)
    action=args.get('action')
    if action not in ('start','pause','resume','end'):return ToolResult('Comando non valido.',is_error=True)
    if action!='start' and not args.get('session_id'):return ToolResult('Serve l’ID della tua sessione attiva.',is_error=True)
    try:
        value=await asyncio.to_thread(archive().manual_session,ctx.user.id,action,None if action=='start' else args['session_id'],args.get('activity_name'))
        return result({'active_session':value['active_session'],'sessions':value['sessions'][:3]})
    except (StoreError,ValueError):return ToolResult('Comando non applicabile alla tua sessione.',is_error=True)
