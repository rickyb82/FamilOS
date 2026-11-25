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
    return result({key:value[key] for key in ('server_time','settings','latest','active_session','sessions','weekly_occurrences','recorded_cardio','daily_energy')})


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


@register('allenamento_analisi','Legge riepiloghi e statistiche della sessione personale selezionata; per i valori intermedi puntuali usa allenamento_campioni, inclusi tempo sotto/dentro/sopra fascia, copertura e tempo senza misure. Distingui le stime cardio registrate sperimentali e i risultati provvisori tramite cardio_provenance. Non attribuire i vuoti a recupero o a una fascia. L’ID deve provenire da allenamento_stato.',{'type':'object','properties':{'session_id':{'type':'string'}},'required':['session_id'],'additionalProperties':False})
async def analysis(args:dict,ctx:ToolContext):
    error=await guard(ctx)
    if error:return ToolResult(error,is_error=True)
    if not isinstance(args.get('session_id'),str) or not args['session_id'] or args['session_id']=='last24h':return ToolResult('Scegli una sessione personale.',is_error=True)
    try:
        value=await asyncio.to_thread(archive().analysis,ctx.user.id,args['session_id'])
        return result({key:value[key] for key in ('activity_name','window_start','window_end','workout_end','stats','cardio','steps_in_records','cardio_provenance')})
    except (StoreError,ValueError):return ToolResult('Sessione non trovata nel tuo profilo.',is_error=True)


@register('allenamento_campioni','Legge TUTTI i punti intermedi disponibili di una metrica nella propria sessione, per pagine senza riduzione dei campioni. Usa session_id da allenamento_stato; gli offset sono secondi dall’inizio della sessione. Metriche: battito, SpO₂, temperatura, movimento, passi e respiro. Segui next_cursor con gli stessi parametri finché has_more è falso per analisi complete. Di default esclude il post allenamento; include_recovery include fino a 10 minuti successivi. Non inventare campioni mancanti; rispetta qualità, sorgente, interpolazione e stato provvisorio.',{'type':'object','properties':{'session_id':{'type':'string'},'metric':{'enum':['heart_rate','spo2','temperature','motion','step_increments','respiratory_rate']},'start_offset_seconds':{'type':'number','minimum':0},'end_offset_seconds':{'type':'number','minimum':0},'cursor':{'type':'string'},'limit':{'type':'integer','minimum':1,'maximum':1000},'include_recovery':{'type':'boolean'}},'required':['session_id'],'additionalProperties':False})
async def samples(args:dict,ctx:ToolContext):
    error=await guard(ctx)
    if error:return ToolResult(error,is_error=True)
    allowed={'session_id','metric','start_offset_seconds','end_offset_seconds','cursor','limit','include_recovery'}
    if set(args)-allowed or not isinstance(args.get('session_id'),str) or args['session_id']=='last24h':
        return ToolResult('Scegli una sessione personale e parametri campioni validi.',is_error=True)
    try:
        query=dict(args)
        while True:
            value=await asyncio.to_thread(archive().samples,ctx.user.id,**query)
            # A complete page, including its cursor, must survive chat clipping.
            if len(json.dumps(value,ensure_ascii=False,allow_nan=False))<=40_000 or value['returned']<=1:break
            query['limit']=max(1,value['returned']//2)
        value['page_limit_applied']=query.get('limit',200)
        return result(value)
    except (StoreError,ValueError,TypeError):return ToolResult('Sessione non trovata nel tuo profilo oppure intervallo o cursore non valido.',is_error=True)


@register('allenamento_grafico','Crea e mostra un grafico personale nella chat quando l’utente chiede di vedere il proprio allenamento. Include le sei metriche disponibili e tutti i punti intermedi con dettagli, qualità e statistiche. Nessun download esterno. Usa session_id ottenuto da allenamento_stato; non affermare di avere creato il grafico se il tool fallisce.',{'type':'object','properties':{'session_id':{'type':'string'}},'required':['session_id'],'additionalProperties':False})
async def chart(args:dict,ctx:ToolContext):
    error=await guard(ctx)
    if error:return ToolResult(error,is_error=True)
    if set(args)!={'session_id'} or not isinstance(args['session_id'],str) or args['session_id']=='last24h':return ToolResult('Scegli una sessione personale.',is_error=True)
    try:
        value=await asyncio.to_thread(archive().analysis,ctx.user.id,args['session_id'])
        from ..services.homepulse.report import render
        document,counts=await asyncio.to_thread(render,value)
        from .artifacts import create_artifact
        created=await create_artifact({'title':'Dettaglio allenamento','kind':'html','content':document},ctx)
        created.content+=' Campioni disponibili: '+json.dumps(counts,ensure_ascii=False)
        return created
    except (StoreError,ValueError):return ToolResult('Sessione non trovata nel tuo profilo.',is_error=True)


@register('allenamento_energia','Legge la stima delle calorie della giornata personale, base/attive/totale, serie oraria, metodo e copertura. Usa i MET acquisiti dell’anello e le fasi con MET esplicito senza doppio conteggio; non inventa calorie dai soli BPM o dai vuoti. La stima può essere parziale e non include termogenesi alimentare. date facoltativa YYYY-MM-DD nel fuso Europe/Rome.',{'type':'object','properties':{'date':{'type':'string'}},'additionalProperties':False})
async def energy(args:dict,ctx:ToolContext):
    error=await guard(ctx)
    if error:return ToolResult(error,is_error=True)
    if set(args)-{'date'}:return ToolResult('Parametro non valido.',is_error=True)
    try:return result(await asyncio.to_thread(archive().daily_energy,ctx.user.id,args.get('date')))
    except (StoreError,ValueError):return ToolResult('Data non valida.',is_error=True)


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
