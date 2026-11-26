import { useCallback, useEffect, useId, useRef, useState, type FormEvent, type ReactNode } from 'react'
import { useAuth } from '../lib/auth'
import {
  activePhase, ageLabel, analysisCsv, dateLabel, defaultConfig, defaultDetection, demoAnalysis,
  demoState, durationLabel, energyValue, epochSeconds, heartQuality, profileComposition, timeLabel,
  valueLabel, wellness, WEEKDAYS, WELLNESS_TIMEZONE,
  type AutoDetection, type BridgeDevice, type Pairing, type Phase, type Profile,
  type WeeklySlot, type WellnessAnalysis, type WellnessSession, type WellnessSettings, type WellnessState, type WorkoutConfig,
} from '../lib/wellness'
import './Wellness.css'
import SleepPanel from './SleepPanel'
import { MiniHeart, SharedTimeline, EMPTY_POINTS } from './WellnessCharts'

type View = 'dashboard' | 'sleep' | 'plan' | 'profile' | 'calendar' | 'devices'
const VIEWS: Array<{ id: View; label: string; icon: string }> = [
  { id: 'dashboard', label: 'Panoramica', icon: 'pulse' }, { id: 'sleep', label: 'Sonno', icon: 'moon' }, { id: 'plan', label: 'La mia scheda', icon: 'plan' },
  { id: 'profile', label: 'Profilo', icon: 'person' }, { id: 'calendar', label: 'Calendario', icon: 'calendar' },
  { id: 'devices', label: 'iPhone e anello', icon: 'phone' },
]
function Icon({ name, size = 20 }: { name: string; size?: number }) {
  const paths: Record<string, ReactNode> = {
    pulse: <path d="M3 12h4l3-7 4 14 3-7h4" />, heart: <path d="M20.8 4.6a5.5 5.5 0 0 0-7.8 0L12 5.7l-1.1-1.1a5.5 5.5 0 0 0-7.8 7.8L12 21l8.8-8.6a5.5 5.5 0 0 0 0-7.8Z" />,
    plan: <><path d="M9 4h9a2 2 0 0 1 2 2v14H4V6a2 2 0 0 1 2-2h3" /><path d="M9 2h6v4H9zM8 10h8M8 14h8M8 18h4" /></>,
    person: <><circle cx="12" cy="7" r="4" /><path d="M4 21v-3a8 8 0 0 1 16 0v3" /></>,
    calendar: <><rect x="3" y="5" width="18" height="16" rx="3" /><path d="M7 2v6m10-6v6M3 11h18M8 15h1m6 0h1m-8 3h1" /></>,
    phone: <><rect x="6" y="2" width="12" height="20" rx="3" /><path d="M10 5h4m-3 14h2" /></>,
    moon: <path d="M20.5 14A9 9 0 0 1 10 3.5 9 9 0 1 0 20.5 14Z" />,
    ring: <><ellipse cx="12" cy="12" rx="8" ry="9" /><ellipse cx="12" cy="12" rx="5" ry="6" /></>,
    play: <path d="m8 4 12 8-12 8Z" />, pause: <><path d="M8 5v14m8-14v14" /></>, stop: <rect x="5" y="5" width="14" height="14" rx="2" />,
    add: <path d="M12 4v16M4 12h16" />, close: <path d="m6 6 12 12M6 18 18 6" />,
    expand: <path d="M8 3H3v5m13-5h5v5M3 16v5h5m8 0h5v-5" />,
    download: <><path d="M12 3v12m-5-5 5 5 5-5M4 17v4h16v-4" /></>,
    settings: <><path d="M4 7h16M4 17h16" /><circle cx="8" cy="7" r="3" /><circle cx="16" cy="17" r="3" /></>,
    temperature: <><path d="M9 14V5a3 3 0 0 1 6 0v9a5 5 0 1 1-6 0Z" /><path d="M12 8v9" /></>,
    drop: <path d="M12 2C9 7 4 11 4 15a8 8 0 0 0 16 0c0-4-5-8-8-13Z" />,
    breath: <><path d="M3 8h12a3 3 0 1 0-3-3M3 12h16a3 3 0 1 1-3 3M3 16h6a3 3 0 1 1-3 3" /></>,
    motion: <><path d="m3 17 5-5 4 3 8-12M16 3h4v4" /><path d="M4 21h16" /></>,
    check: <path d="m5 12 4 4L20 5" />, copy: <><rect x="8" y="8" width="12" height="13" rx="2" /><path d="M15 8V3H3v12h5" /></>,
  }
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name] || paths.pulse}</svg>
}
function Button({ children, icon, primary = false, ...props }: React.ButtonHTMLAttributes<HTMLButtonElement> & { icon?: string; primary?: boolean }) {
  return <button {...props} className={`wl-button ${primary ? 'wl-primary' : ''} ${props.className || ''}`}>{icon && <Icon name={icon} size={17} />}{children}</button>
}
function Modal({ title, children, onClose }: { title: string; children: ReactNode; onClose: () => void }) {
  const id = useId(), ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null
    ref.current?.closest('.wellness')?.scrollTo?.(0, 0)
    ref.current?.querySelector<HTMLElement>('button,input,select,textarea')?.focus()
    const key = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { event.preventDefault(); onClose() }
      if (event.key !== 'Tab') return
      const items = Array.from(ref.current?.querySelectorAll<HTMLElement>('button:not(:disabled),input:not(:disabled),select,textarea,a[href]') || [])
      const first = items[0], last = items[items.length - 1]
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus() }
      if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus() }
    }
    document.addEventListener('keydown', key)
    return () => { document.removeEventListener('keydown', key); previous?.focus() }
  }, [onClose])
  return <div className="wl-overlay"><div ref={ref} role="dialog" aria-modal="true" aria-labelledby={id} className="wl-modal"><div className="wl-modal-heading"><h2 id={id}>{title}</h2><button className="wl-icon-button" onClick={onClose} aria-label="Chiudi finestra"><Icon name="close" /></button></div>{children}</div></div>
}

export default function Wellness() {
  const { me } = useAuth()
  const adult = !!me && me.user.role !== 'child' && !me.acting_as_child
  const allowed = adult && me?.features?.wellness !== false
  const [state, setState] = useState<WellnessState | null>(null), [analysis, setAnalysis] = useState<WellnessAnalysis | null>(null)
  const [view, setView] = useState<View>('dashboard'), [selected, setSelected] = useState('')
  const [demo, setDemo] = useState(false), [clock, setClock] = useState(Date.now() / 1000), [tv, setTv] = useState(false)
  const [loadError, setLoadError] = useState(''), [actionError, setActionError] = useState(''), [notice, setNotice] = useState(''), [busy, setBusy] = useState('')
  const [lastUpdate, setLastUpdate] = useState(0), [pairing, setPairing] = useState<Pairing | null>(null)
  const [visible, setVisible] = useState(() => document.visibilityState !== 'hidden')
  useEffect(() => {
    const changed = () => setVisible(document.visibilityState !== 'hidden')
    document.addEventListener('visibilitychange', changed)
    return () => document.removeEventListener('visibilitychange', changed)
  }, [])
  const root = useRef<HTMLElement>(null)
  const mounted = useRef(true)
  useEffect(() => { mounted.current = true; return () => { mounted.current = false } }, [])
  useEffect(() => {
    if (!visible) return
    setClock(Date.now() / 1000)
    const timer = window.setInterval(() => setClock(Date.now() / 1000), 1000)
    return () => clearInterval(timer)
  }, [visible])
  const refresh = useCallback(async () => {
    const next = await wellness.state()
    if (mounted.current) { setState(next); setLoadError(''); setLastUpdate(Date.now() / 1000) }
    return next
  }, [])
  useEffect(() => {
    if (!allowed || demo || !visible) return
    let alive = true, timer: ReturnType<typeof setTimeout>
    const poll = async () => {
      try { await refresh() } catch (error) { if (alive && mounted.current) setLoadError(error instanceof Error ? error.message : 'Il server non è raggiungibile') }
      if (alive) timer = setTimeout(poll, 1000)
    }
    poll()
    return () => { alive = false; clearTimeout(timer) }
  }, [allowed, demo, refresh, visible])
  useEffect(() => {
    if (!allowed || demo || !visible || view !== 'dashboard') return
    let alive = true, timer: ReturnType<typeof setTimeout>
    const poll = async () => {
      try { const next = await wellness.analysis(selected || undefined); if (alive) setAnalysis(next) } catch { /* Historical decoding may still be queued. */ }
      if (alive) timer = setTimeout(poll, 5000)
    }
    setAnalysis(null); poll()
    return () => { alive = false; clearTimeout(timer) }
  }, [allowed, demo, selected, visible, view])
  const perform = useCallback(async (name: string, operation: () => Promise<unknown>) => {
    if (demo) return
    setBusy(name); setActionError(''); setNotice('')
    try { await operation(); await refresh(); return true }
    catch (error) { setActionError(error instanceof Error ? error.message : 'Operazione non riuscita'); return false }
    finally { if (mounted.current) setBusy('') }
  }, [demo, refresh])
  const snapshot = demo ? demoState(clock) : state
  const shownAnalysis = demo ? demoAnalysis(clock) : analysis
  const serverNow = snapshot ? (epochSeconds(snapshot.server_time) ?? clock) + (demo ? 0 : Math.max(0, clock - lastUpdate)) : clock
  const personalConfig = snapshot?.settings.config || defaultConfig()
  const session = snapshot?.active_session || snapshot?.sessions.find(item => item.status === 'paused')
  const config = session?.config || personalConfig
  const currentPhase = session ? activePhase(config, session.active_seconds || 0) : null
  const quality = heartQuality(snapshot?.latest.heart_rate, serverNow)
  const offline = !demo && (!!loadError || !!lastUpdate && clock - lastUpdate > 8)
  const outsideStamp = epochSeconds(snapshot?.latest.heart_rate_outside_display?.captured_at)
  const outsideIsNewer = outsideStamp != null && (quality.captured == null || outsideStamp > quality.captured)
  const recordedHeart = snapshot?.latest.heart_rate?.source === 'oura-recorded-offline'
  const recordedBad = snapshot?.recorded_cardio?.signal_check === -2 && (snapshot.recorded_cardio.available_until >= (quality.captured || 0))
  const hasSignal = quality.reliable && !offline && !outsideIsNewer && !recordedBad
  const paired = snapshot?.devices.filter(device => !device.revoked) || []
  const hello = me?.user.display_name?.split(' ')[0] || 'Il tuo spazio'
  const changeDemo = () => { setDemo(value => !value); setSelected(''); setView('dashboard'); setActionError(''); setNotice(''); setPairing(null) }
  const closePairing = useCallback(() => setPairing(null), [])
  async function createPairing() {
    if (demo) return
    await perform('pair', async () => setPairing(await wellness.pair('iPhone personale')))
  }
  async function toggleTv() {
    if (tv) { setTv(false); if (document.fullscreenElement === root.current) await document.exitFullscreen().catch(() => {}); return }
    setTv(true)
    if (root.current?.requestFullscreen) await root.current.requestFullscreen().catch(() => setNotice('Vista TV attiva. Puoi usare anche lo schermo intero del browser.'))
  }
  async function sessionAction(action: 'start' | 'pause' | 'resume' | 'end', activityName?: string) {
    const command = action === 'start' && session?.provisional ? 'activity' : action
    await perform('session', () => wellness.session(command, { ...(session?.id ? { session_id: session.id } : {}), activity_name: activityName || config.activity_name || 'Allenamento' }))
  }
  if (!allowed) return <div className="wellness wl-access" role="status"><Icon name="heart" size={36} /><h1>Uno spazio personale</h1><p>{adult ? 'Allenamento non è abilitato per questo profilo.' : 'L’app Allenamento è disponibile nei profili adulti.'}</p></div>
  return <section ref={root} className={`wellness ${tv ? 'wl-tv' : ''}`} aria-label="Allenamento personale">
    <header className="wl-header"><div className="wl-brand"><span className="wl-brand-icon"><Icon name="pulse" size={23} /></span><div><span>FAMILOS</span><strong>Allenamento</strong></div></div><div className="wl-header-actions"><span className="wl-clock">{timeLabel(clock)}</span><Button onClick={changeDemo}>{demo ? 'Esci dalla demo' : 'Esplora demo'}</Button><Button icon="expand" onClick={toggleTv}>{tv ? 'Esci dalla vista TV' : 'Vista TV'}</Button></div></header>
    <nav className="wl-tabs" role="tablist" aria-label="Sezioni allenamento">{VIEWS.map(item => <button key={item.id} id={`wl-tab-${item.id}`} role="tab" aria-selected={view === item.id} aria-controls="wl-main-panel" onClick={() => setView(item.id)}><Icon name={item.icon} size={18} /><span>{item.label}</span></button>)}</nav>
    <div className="wl-body" id="wl-main-panel" role="tabpanel" aria-labelledby={`wl-tab-${view}`}>
      {demo && <div className="wl-banner wl-demo" role="status"><strong>DEMO</strong> Valori e sessione di esempio. Il tuo profilo e i tuoi sensori restano separati.</div>}
      {offline && <div className="wl-banner wl-warning" role="status">Dati non aggiornati · {loadError || 'Collegamento al server interrotto'}. Il battito non viene mostrato come live.</div>}
      {actionError && <div className="wl-banner wl-error" role="alert">{actionError}<button onClick={() => setActionError('')} aria-label="Chiudi errore"><Icon name="close" size={16} /></button></div>}
      {notice && <div className="wl-banner" role="status">{notice}<button onClick={() => setNotice('')} aria-label="Chiudi messaggio"><Icon name="close" size={16} /></button></div>}
      {view === 'dashboard' && <>
        <div className="wl-page-title"><div><span className="wl-eyebrow">IL TUO RITMO, IL TUO SPAZIO</span><h1>{hello === 'Il tuo spazio' ? 'Ogni allenamento conta.' : `${hello}, ritrova il tuo ritmo.`}</h1><p>Il corpo durante la sessione. I progressi, nel tempo.</p></div><span className={`wl-status ${hasSignal ? 'wl-status-live' : ''}`}><i />{demo ? 'Simulazione attiva' : hasSignal ? 'Battito aggiornato' : paired.length ? 'iPhone associato' : 'In attesa del tuo iPhone'}</span></div>
        <div className="wl-dashboard">
          <section className="wl-card wl-heart-card"><div className="wl-card-heading"><h2>Frequenza cardiaca</h2><span>{demo ? 'SIMULAZIONE' : recordedHeart ? 'CARDIO REGISTRATO' : 'OURA GEN3'}</span></div><div className="wl-heart-main"><div className="wl-heart-value"><Icon name="heart" size={43} /><strong>{hasSignal ? valueLabel(snapshot?.latest.heart_rate?.bpm) : '—'}</strong><span>bpm</span></div><div className="wl-heart-target"><TargetLabel bpm={hasSignal ? snapshot?.latest.heart_rate?.bpm : null} phase={currentPhase} /><p>{hasSignal ? `Ricevuto ${ageLabel(snapshot?.latest.heart_rate?.captured_at, serverNow)}` : quality.fresh ? 'Il segnale richiede una verifica' : 'La lettura apparirà quando il sensore invia dati recenti.'}</p></div></div>{outsideIsNewer && <div className="wl-quality-note" role="status">Ultima lettura fuori dall’intervallo di visualizzazione · {timeLabel(outsideStamp, true)}. Il dato originale è conservato nell’archivio.</div>}<QualityNote warnings={quality.warnings} flags={snapshot?.latest.heart_rate?.flags_raw} /><div className="wl-heart-chart"><MiniHeart points={shownAnalysis?.heart_rate || EMPTY_POINTS} now={shownAnalysis?.window_end || serverNow} historical={!!selected && !demo} /></div><div className="wl-heart-meta"><span>{recordedHeart ? 'Calcolo locale · WHR U2.5.1' : 'Rilevazioni, senza riempire i vuoti'}</span><span>{demo ? 'Dati sintetici' : recordedHeart ? snapshot?.latest.heart_rate?.provisional ? 'Risultato provvisorio' : 'Risultato finale' : 'Acquisizione reale · Roma'}</span></div><div className="wl-session-stats"><div><span>Media sessione</span><strong>{valueLabel(session?.stats.average_bpm)} <small>bpm</small></strong></div><div><span>Picco sessione</span><strong>{valueLabel(session?.stats.max_bpm)} <small>bpm</small></strong></div><div><span>Tempo attivo</span><strong>{durationLabel(session?.active_seconds || 0)}</strong></div></div></section>
          <SessionCard config={config} session={session} phase={currentPhase} busy={!!busy} demo={demo} onAction={sessionAction} onEdit={() => setView('plan')} />
          <section className="wl-metric-grid" aria-label="Altri segnali"><SensorCard icon="drop" title={snapshot?.spo2_catalog?.latest_sleep ? "SpO₂ · media sonno" : "SpO₂ · ultima misura"} value={snapshot?.spo2_catalog?.latest_sleep?.display_percentage ?? snapshot?.latest.spo2?.value} unit="%" utcMs={snapshot?.spo2_catalog?.latest_sleep ? snapshot.spo2_catalog.latest_sleep.ended_at * 1000 : snapshot?.latest.spo2?.utc_ms} note={snapshot?.spo2_catalog?.latest_sleep ? "Media del periodo di sonno" : "Dato storico elaborato"} color="aqua" /><SensorCard icon="temperature" title="Temperatura anello" value={snapshot?.latest.temperature?.value} unit="°C" utcMs={snapshot?.latest.temperature?.utc_ms} note="Sensore 1 · non temperatura interna" color="coral" digits={1} /><SensorCard icon="motion" title="Movimento" value={snapshot?.latest.motion?.value} unit="s" utcMs={snapshot?.latest.motion?.utc_ms} note="Secondi nel record storico" color="violet" /><SensorCard icon="activity" title="Passi" value={snapshot?.latest.steps?.value} unit="" utcMs={snapshot?.latest.steps?.utc_ms} note="Incremento nell’ultimo record" color="lime" /><SensorCard icon="breath" title="Frequenza respiratoria" value={snapshot?.latest.respiratory_rate?.value} unit="resp/min" utcMs={snapshot?.latest.respiratory_rate?.utc_ms} note="Media nel record storico" color="aqua" digits={1} emptyNote="Nessun record del respiro disponibile" /></section>
        </div>
        <section className="wl-card wl-timeline-card"><div className="wl-card-heading"><div><h2>Una sola linea del tempo</h2><p>Battito, respiro e movimento, allineati all’istante di acquisizione.</p></div><div className="wl-timeline-controls"><label className="wl-sr-only" htmlFor="wl-session-select">Periodo da analizzare</label><select id="wl-session-select" value={selected} onChange={event => setSelected(event.target.value)} disabled={demo}><option value="">Ultime 24 ore</option>{snapshot?.sessions.map(item => <option key={item.id} value={item.id}>{item.activity_name} · {dateLabel(item.started_at)} {timeLabel(epochSeconds(item.started_at))}</option>)}</select><Button icon="download" disabled={!shownAnalysis} onClick={() => shownAnalysis && downloadCsv(shownAnalysis)}>CSV</Button></div></div><SharedTimeline analysis={shownAnalysis} /><div className="wl-timeline-foot"><span>Ora di Roma · i dati scaricati in ritardo restano al loro orario originale</span><span>{shownAnalysis?.steps_in_records != null ? `${valueLabel(shownAnalysis.steps_in_records)} passi nei record del periodo` : 'I campioni assenti rimangono vuoti'}</span></div></section>
        <CardioSummary analysis={shownAnalysis} />
        {snapshot?.daily_energy && <section className="wl-card"><div className="wl-card-heading"><h2>Calorie di oggi · finora</h2><span>STIMA</span></div><div className="wl-energy"><div><strong>{valueLabel(snapshot.daily_energy.base_kcal)}</strong><span>kcal di base</span></div><div><strong>{valueLabel(snapshot.daily_energy.active_kcal)}</strong><span>kcal attive stimate</span></div><div><strong>{valueLabel(snapshot.daily_energy.total_kcal)}</strong><span>kcal · totale parziale</span></div></div><p className="wl-note">Attività stimabile: {durationLabel(snapshot.daily_energy.estimated_activity_seconds)} · senza MET: {durationLabel(snapshot.daily_energy.unestimated_activity_seconds)}. Base da profilo; attività da MET dell’anello o delle fasi, senza doppio conteggio.</p>{snapshot.daily_energy.warnings.map((warning,index)=><p className="wl-note" key={index}>{warning}</p>)}</section>}
        <div className="wl-bottom-grid"><section className="wl-card"><div className="wl-card-heading"><h2>Energia della sessione</h2><span>STIMA</span></div><div className="wl-energy"><div><strong>{valueLabel(energyValue(session?.stats, 'net_kcal'))}</strong><span>kcal attive</span></div><div><strong>{valueLabel(energyValue(session?.stats, 'gross_kcal'))}</strong><span>kcal totali</span></div></div><p className="wl-note">{session?.stats.estimated_seconds ? `${durationLabel(session.stats.estimated_seconds)} stimati${session.stats.unestimated_seconds ? ` · ${durationLabel(session.stats.unestimated_seconds)} senza stima` : ''}. ` : 'Nessun intervallo stimabile per ora. '}Stima da peso, durata e MET delle fasi. La massa magra informa la stima basale separata; non aumenta automaticamente la precisione del consumo durante lo sforzo.</p><button className="wl-text-button" onClick={() => setView('profile')}>Rivedi il tuo profilo</button></section><WeeklyPreview schedule={snapshot?.settings.weekly_schedule || []} onOpen={() => setView('calendar')} /><section className="wl-card wl-connect-card"><Icon name="phone" size={32} /><h2>{paired.length ? 'Il collegamento è tuo.' : 'Porta il tuo anello qui.'}</h2><p>{paired.length ? `${paired.length} ${paired.length === 1 ? 'iPhone associato' : 'dispositivi associati'}. Gestisci il collegamento e lo stato della sincronizzazione.` : 'Associa il tuo iPhone: legge il Gen3 e porta i dati in FamilOS, anche quando la dashboard è sulla TV.'}</p><Button onClick={() => setView('devices')}>{paired.length ? 'Gestisci dispositivi' : 'Collega iPhone'}</Button></section></div>
        <HistoryList sessions={snapshot?.sessions || []} selected={selected} onSelect={id => setSelected(id)} />
      </>}
      {view === 'sleep' && <SleepPanel key={`${me?.user.id}:${demo}`} demo={demo} />}
      {view !== 'dashboard' && view !== 'devices' && view !== 'sleep' && !snapshot && <div className="wl-card" role="status">Caricamento del tuo profilo…</div>}
      {view === 'plan' && snapshot && <PlanEditor key={demo ? 'demo' : 'personal'} config={personalConfig} disabled={demo || !!busy} onSave={async next => { const saved = await perform('settings', () => wellness.settings({ config: next })); if (saved) setNotice('Scheda salvata nel tuo profilo.'); return !!saved }} />}
      {view === 'profile' && snapshot && <ProfileEditor key={demo ? 'demo' : 'personal'} profile={snapshot.settings.profile} disabled={demo || !!busy} onSave={async profile => { const saved = await perform('settings', () => wellness.settings({ profile })); if (saved) setNotice('Profilo aggiornato.'); return !!saved }} />}
      {view === 'calendar' && snapshot && <CalendarEditor key={demo ? 'demo' : 'personal'} settings={snapshot.settings} disabled={demo || !!busy} onSave={async values => { const saved = await perform('settings', () => wellness.settings(values)); if (saved) setNotice('Calendario e preferenze salvati.'); return !!saved }} />}
      {view === 'devices' && <DevicesPanel devices={snapshot?.devices || []} archive={snapshot?.archive} now={serverNow} disabled={demo || !!busy} busy={busy} onPair={createPairing} onRevoke={id => perform('revoke', () => wellness.revoke(id))} onBinding={async (key, id) => { const saved = await perform('binding', () => wellness.bindRing(key, id)); if (saved) setNotice('Associazione anello aggiornata sul server.'); return !!saved }} />}
      <footer className="wl-footer"><span>FAMILOS <b>·</b> IL TUO SPAZIO PERSONALE</span><span>Le stime supportano il tuo allenamento. Non misurano soglie metaboliche individuali.</span></footer>
    </div>
    {pairing && <PairingDialog pairing={pairing} now={clock} onClose={closePairing} onNotice={setNotice} />}
  </section>
}

function TargetLabel({ bpm, phase }: { bpm?: number | null; phase: ReturnType<typeof activePhase> }) {
  if (phase?.target_min == null || phase.target_max == null) return <><span className="wl-target-label">Il tuo battito, adesso</span><p>Gli intervalli cardiaci si impostano nella scheda.</p></>
  const where = bpm == null ? 'In attesa del segnale' : bpm < phase.target_min ? 'Sotto il tuo intervallo' : bpm > phase.target_max ? 'Sopra il tuo intervallo' : 'Nel tuo intervallo'
  return <><span className={`wl-target-label ${bpm != null && bpm >= phase.target_min && bpm <= phase.target_max ? 'wl-target-inside' : ''}`}>{where}</span><strong className="wl-target-range">{phase.target_min}–{phase.target_max} <small>bpm</small></strong></>
}
function QualityNote({ warnings, flags }: { warnings: string[]; flags?: number }) {
  if (!warnings.length && !flags) return null
  const labels = [...warnings]
  if (flags != null && flags & 16 && !labels.length) labels.push('Movimento elevato: lettura ottica da verificare.')
  if (flags != null && flags & 4 && !labels.length) labels.push('Segnale ottico non affidabile.')
  if (!labels.length) return null
  return <div className="wl-quality-note" role="status">{labels.map((warning, index) => <span key={index}>{warning}</span>)}</div>
}
function SensorCard({ title, icon, value, unit, utcMs, note, color, digits = 0, emptyNote = 'Non ancora disponibile' }: { title: string; icon: string; value?: number; unit: string; utcMs?: number; note: string; color: string; digits?: number; emptyNote?: string }) {
  return <div className={`wl-card wl-sensor wl-sensor-${color}`}><div><Icon name={icon} size={21} /><span>{title}</span></div><strong>{valueLabel(value, digits)} <small>{value != null ? unit : ''}</small></strong><p>{note}</p><small>{utcMs != null ? `${dateLabel(utcMs / 1000)} · ${timeLabel(utcMs / 1000)}` : emptyNote}</small></div>
}
function SessionCard({ config, session, phase, busy, demo, onAction, onEdit }: { config: WorkoutConfig; session?: WellnessSession | null; phase: ReturnType<typeof activePhase>; busy: boolean; demo: boolean; onAction: (action: 'start' | 'pause' | 'resume' | 'end', name?: string) => void; onEdit: () => void }) {
  const [activity, setActivity] = useState(config.activity_name || 'Allenamento')
  useEffect(() => setActivity(config.activity_name || 'Allenamento'), [config.activity_name])
  const active = session && !['ended', 'completed', 'cancelled'].includes(session.status), paused = session?.status === 'paused'
  const total = config.plan?.reduce((sum, item) => sum + item.duration, 0) * config.rounds || 0
  return <section className="wl-card wl-session-card"><div className="wl-card-heading"><h2>La tua sessione</h2><button className="wl-icon-button" onClick={onEdit} aria-label="Modifica la scheda"><Icon name="settings" size={18} /></button></div><span className="wl-eyebrow wl-phase-label">{session?.provisional ? 'POSSIBILE ALLENAMENTO RILEVATO' : paused ? 'IN PAUSA' : active ? phase?.name || 'SESSIONE IN CORSO' : 'PRONTO QUANDO LO SEI TU'}</span><div className="wl-timer">{phase && active ? durationLabel(phase.remaining) : durationLabel(active ? session.active_seconds : total)}</div><div className="wl-session-progress"><span style={{ width: `${total ? Math.min(100, (session?.active_seconds || 0) / total * 100) : 0}%` }} /></div><div className="wl-timer-detail"><span>{phase ? `Giro ${phase.round} di ${config.rounds}` : config.plan.length ? `${config.plan.length} fasi · ${config.rounds} ${config.rounds === 1 ? 'giro' : 'giri'}` : 'Sessione libera · senza fasi automatiche'}</span><strong>{phase ? `${phase.target_min != null ? `${phase.target_min}–${phase.target_max} bpm` : 'Intervallo libero'}` : active ? 'Tempo attivo' : 'La tua intensità'}</strong></div><label className="wl-activity-label">Attività<input value={active && !session.provisional ? session.activity_name : activity} onChange={event => setActivity(event.target.value)} maxLength={80} disabled={!!active && !session.provisional} /></label>{!active || session.provisional ? <Button primary icon="play" disabled={busy || demo || !activity.trim()} onClick={() => onAction('start', activity)}>{session?.provisional ? 'Conferma e inizia' : 'Inizia allenamento'}</Button> : <div className="wl-session-buttons"><Button primary icon={paused ? 'play' : 'pause'} disabled={busy || demo} onClick={() => onAction(paused ? 'resume' : 'pause')}>{paused ? 'Riprendi' : 'Pausa'}</Button><Button icon="stop" disabled={busy || demo} onClick={() => onAction('end')}>Termina</Button></div>}<div className="wl-tv-energy"><span><strong>{valueLabel(energyValue(session?.stats, 'net_kcal'))}</strong> kcal attive</span><span><strong>{valueLabel(energyValue(session?.stats, 'gross_kcal'))}</strong> kcal totali</span><small>Stima · peso e MET</small></div><p className="wl-note">{session?.auto_detected ? 'Proposta dal rilevamento automatico. Il nome dell’attività resta una tua scelta.' : 'Gli intervalli cardiaci sono quelli della tua scheda. Il trainer non li ricava automaticamente dall’età.'}</p></section>
}
function WeeklyPreview({ schedule, onOpen }: { schedule: WeeklySlot[]; onOpen: () => void }) {
  return <section className="wl-card"><div className="wl-card-heading"><h2>Il tuo appuntamento con te</h2><Icon name="calendar" size={19} /></div>{schedule.length ? <div className="wl-week-preview">{[...schedule].sort((a, b) => a.weekday - b.weekday || a.start.localeCompare(b.start)).slice(0, 3).map(slot => <div key={slot.id}><span>{WEEKDAYS[slot.weekday - 1]?.slice(0, 3).toUpperCase()}</span><strong>{slot.start}<small>– {slot.end}</small></strong><small>{slot.label}</small></div>)}</div> : <p className="wl-empty-note">Riserva un momento al tuo allenamento. Il calendario appartiene al tuo profilo.</p>}<button className="wl-text-button" onClick={onOpen}>Apri calendario</button></section>
}
function HistoryList({ sessions, selected, onSelect }: { sessions: WellnessSession[]; selected: string; onSelect: (id: string) => void }) {
  return <section className="wl-history"><div className="wl-section-heading"><h2>Le tue ultime sessioni</h2><span>{sessions.length ? `${sessions.length} registrate` : 'La prima inizia da te'}</span></div>{!sessions.length ? <div className="wl-card wl-history-empty"><Icon name="plan" size={25} /><p>Quando termini un allenamento, il riepilogo e i grafici compariranno qui.</p></div> : <div className="wl-history-list">{sessions.slice(0, 8).map(session => <button className={`wl-history-item ${selected === session.id ? 'wl-selected' : ''}`} key={session.id} onClick={() => onSelect(session.id)} aria-pressed={selected === session.id}><span className="wl-history-symbol"><Icon name="pulse" /></span><span><strong>{session.activity_name}</strong><small>{dateLabel(session.started_at)} · {timeLabel(epochSeconds(session.started_at))}{session.auto_detected ? ' · rilevata' : ''}</small></span><span><strong>{durationLabel(session.active_seconds)}</strong><small>tempo attivo</small></span><span><strong>{valueLabel(session.stats?.average_bpm)} <small>bpm</small></strong><small>media</small></span><span><strong>{valueLabel(energyValue(session.stats, 'net_kcal'))} <small>kcal</small></strong><small>stima attive</small></span></button>)}</div>}</section>
}

function PlanEditor({ config, disabled, onSave }: { config: WorkoutConfig; disabled: boolean; onSave: (config: WorkoutConfig) => Promise<boolean> }) {
  const [draft, setDraft] = useState<WorkoutConfig>(() => ({ ...config, plan: (config.plan || []).map(item => ({ ...item })) })), [error, setError] = useState('')
  const [saved, setSaved] = useState(false)
  const total = draft.plan.reduce((sum, item) => sum + item.duration, 0) * draft.rounds
  function edit(index: number, change: Partial<Phase>) { setSaved(false); setDraft(current => ({ ...current, plan: current.plan.map((item, at) => at === index ? { ...item, ...change } : item) })) }
  async function submit(event: FormEvent) {
    event.preventDefault(); setError('')
    if (draft.plan.some(item => item.target_min != null && item.target_max != null && item.target_min >= item.target_max || (item.target_min == null) !== (item.target_max == null))) { setError('Inserisci entrambi gli estremi dell’intervallo e mantieni la FC minima inferiore alla massima.'); return }
    if (total > 86400) { setError('La scheda completa non può superare 24 ore.'); return }
    if (await onSave(draft)) setSaved(true)
  }
  return <div className="wl-editor"><div className="wl-page-title"><div><span className="wl-eyebrow">IL TUO PROGRAMMA</span><h1>Una scheda che ti segue.</h1><p>Fasi, recuperi e obiettivi cardiaci scelti da te.</p></div><span className="wl-editor-total"><strong>{durationLabel(total)}</strong><small>durata programmata</small></span></div><form onSubmit={submit}><div className="wl-card wl-form-grid wl-plan-meta"><label>Nome dell’attività<input required maxLength={80} value={draft.activity_name || ''} onChange={event => setDraft({ ...draft, activity_name: event.target.value })} placeholder="Es. Circuito forza" /></label><label>Giri della scheda<input required type="number" min={1} max={100} value={draft.rounds} onChange={event => setDraft({ ...draft, rounds: Number(event.target.value) })} /></label><label>FC massima personale · opzionale<input type="number" min={80} max={240} value={draft.hrmax ?? ''} onChange={event => setDraft({ ...draft, hrmax: optionalNumber(event.target.value) })} placeholder="bpm" /></label></div><div className="wl-phase-list">{draft.plan.map((item, index) => <div className="wl-card wl-phase-editor" key={index}><div className="wl-phase-index">{index + 1}</div><label>Fase o esercizio<input required maxLength={80} value={item.name} onChange={event => edit(index, { name: event.target.value })} /></label><label>Durata · secondi<input required type="number" min={5} max={7200} value={item.duration} onChange={event => edit(index, { duration: Number(event.target.value) })} /></label><div className="wl-bpm-fields"><label>FC minima<input aria-label={`FC minima fase ${index + 1}`} type="number" min={40} max={240} value={item.target_min ?? ''} onChange={event => edit(index, { target_min: optionalNumber(event.target.value) })} placeholder="bpm" /></label><label>FC massima<input aria-label={`FC massima fase ${index + 1}`} type="number" min={40} max={240} value={item.target_max ?? ''} onChange={event => edit(index, { target_max: optionalNumber(event.target.value) })} placeholder="bpm" /></label></div><label>MET · opzionale<input aria-label={`MET fase ${index + 1}`} type="number" min={1} max={20} step="0.1" value={item.met ?? ''} onChange={event => edit(index, { met: optionalNumber(event.target.value) })} placeholder="Non stimato" /></label><button type="button" className="wl-icon-button" onClick={() => setDraft({ ...draft, plan: draft.plan.filter((_, at) => at !== index) })} aria-label={`Elimina fase ${index + 1}`}><Icon name="close" size={18} /></button></div>)}</div>{!draft.plan.length && <div className="wl-card wl-plan-empty"><Icon name="plan" size={33} /><h2>Parti da una fase.</h2><p>Senza una scheda, la sessione registra il tempo e i sensori senza cambiare automaticamente esercizio.</p></div>}<Button type="button" icon="add" disabled={disabled || draft.plan.length >= 40} onClick={() => setDraft({ ...draft, plan: [...draft.plan, { name: '', duration: 60, target_min: null, target_max: null, met: null }] })}>Aggiungi fase</Button><div className="wl-card wl-method-note"><h3>I numeri al loro posto</h3><p>I target BPM sono intervalli personali, non soglie metaboliche misurate. Inserisci i MET solo se disponi di una stima per quella fase: i minuti senza MET restano esclusi dal conteggio calorico.</p></div>{error && <p className="wl-inline-error" role="alert">{error}</p>}<div className="wl-form-actions"><Button type="submit" primary icon="check" disabled={disabled}>Salva la scheda</Button>{saved && <span role="status">Scheda salvata</span>}{disabled && <span>La demo non modifica il tuo programma.</span>}</div></form></div>
}
function optionalNumber(value: string) { return value === '' ? null : Number(value) }
function ProfileEditor({ profile, disabled, onSave }: { profile: Profile; disabled: boolean; onSave: (profile: Profile) => Promise<boolean> }) {
  const [draft, setDraft] = useState<Profile>({ ...profile }), [saved, setSaved] = useState(false)
  const composition = profileComposition(draft)
  const fields: Array<{ key: keyof Profile; label: string; min: number; max: number; step?: string }> = [
    { key: 'weight_kg', label: 'Peso · kg', min: 30, max: 350, step: '0.1' }, { key: 'height_cm', label: 'Altezza · cm', min: 100, max: 250, step: '0.1' }, { key: 'age', label: 'Età · opzionale', min: 18, max: 100 },
    { key: 'resting_hr', label: 'FC a riposo · bpm · opzionale', min: 20, max: 200 },
    { key: 'body_fat_pct', label: 'Massa grassa · %', min: 2, max: 65, step: '0.1' }, { key: 'reported_lean_pct', label: 'Massa magra dichiarata · %', min: 2, max: 98, step: '0.1' }, { key: 'reported_retention_pct', label: 'Ritenzione dichiarata · %', min: 0, max: 100, step: '0.1' },
  ]
  return <div className="wl-editor"><div className="wl-page-title"><div><span className="wl-eyebrow">IL PUNTO DI PARTENZA SEI TU</span><h1>Il tuo profilo.</h1><p>Misure dichiarate, stime trasparenti. Aggiornale quando cambiano.</p></div><span className="wl-private-label"><Icon name="person" size={16} />Personale</span></div><div className="wl-profile-grid"><form className="wl-card" onSubmit={async event => { event.preventDefault(); setSaved(await onSave(draft)) }}><div className="wl-form-grid">{fields.map(field => <label key={field.key}>{field.label}<input type="number" min={field.min} max={field.max} step={field.step || '1'} value={draft[field.key] ?? ''} onChange={event => { setSaved(false); setDraft({ ...draft, [field.key]: optionalNumber(event.target.value) }) }} placeholder="Non inserito" /></label>)}<label>Parametro fisiologico per energia<select value={draft.energy_sex ?? ""} onChange={event => setDraft({ ...draft, energy_sex: event.target.value === "male" ? "male" : event.target.value === "female" ? "female" : null })}><option value="">Non specificato · intervallo</option><option value="male">Maschile</option><option value="female">Femminile</option></select></label><label className="wl-span-all">Fonte della composizione corporea<input maxLength={200} value={draft.composition_source || ''} onChange={event => setDraft({ ...draft, composition_source: event.target.value || null })} placeholder="Es. bilancia o misurazione professionale" /></label><label className="wl-span-all">Il tuo obiettivo<textarea rows={3} maxLength={200} value={draft.goal || ''} onChange={event => setDraft({ ...draft, goal: event.target.value || null })} placeholder="Che cosa vuoi migliorare?" /></label></div><div className="wl-form-actions"><Button type="submit" primary icon="check" disabled={disabled}>Salva profilo</Button>{saved && <span role="status">Profilo salvato</span>}</div></form><aside className="wl-card wl-composition"><h2>Composizione e stima basale</h2><div><span>Massa grassa</span><strong>{valueLabel(composition.fatKg, 1)} <small>kg</small></strong></div><div><span>Massa magra ricavabile</span><strong>{valueLabel(composition.leanKg, 1)} <small>kg</small></strong></div><div><span>Metabolismo basale stimato</span><strong>{valueLabel(composition.basalKcal)} <small>kcal/giorno</small></strong></div>{composition.conflict ? <p className="wl-composition-warning" role="status">Da chiarire: massa magra e grassa non sono complementari. La stima da composizione resta vuota; per il conteggio giornaliero si può usare la formula da peso, altezza ed età. Conserviamo le percentuali dichiarate.</p> : <p className="wl-note">Stima di Cunningham: 500 + 22 × massa magra in kg. Non è una misurazione del metabolismo né una prescrizione alimentare.</p>}<p className="wl-note">L’acqua appartiene alla massa priva di grasso. La ritenzione dichiarata non viene sommata come terzo compartimento né sottratta automaticamente.</p></aside></div></div>
}

function CalendarEditor({ settings, disabled, onSave }: { settings?: WellnessSettings; disabled: boolean; onSave: (settings: Partial<WellnessSettings>) => Promise<boolean> }) {
  const [slots, setSlots] = useState<WeeklySlot[]>(() => (settings?.weekly_schedule || []).map(slot => ({ ...slot })))
  const [detection, setDetection] = useState<AutoDetection>(() => ({ ...(settings?.auto_detection || defaultDetection()), gym: { ...(settings?.auto_detection?.gym || defaultDetection().gym) } }))
  const [saved, setSaved] = useState(false), [error, setError] = useState('')
  function editSlot(index: number, change: Partial<WeeklySlot>) { setSaved(false); setSlots(current => current.map((slot, at) => index === at ? { ...slot, ...change } : slot)) }
  const gym = detection.gym
  return <div className="wl-editor"><div className="wl-page-title"><div><span className="wl-eyebrow">SPAZIO AL MOVIMENTO</span><h1>La tua settimana.</h1><p>Orari ricorrenti nel fuso di Roma, sempre nel tuo profilo.</p></div><span className="wl-private-label">Europe/Rome</span></div><div className="wl-week-grid">{WEEKDAYS.map((label, index) => <div key={label} className={slots.some(slot => slot.weekday === index + 1) ? 'wl-day wl-day-active' : 'wl-day'}><span>{label.slice(0, 3)}</span>{slots.filter(slot => slot.weekday === index + 1).map(slot => <div key={slot.id}><strong>{slot.start}</strong><small>{slot.end}</small><span>{slot.label}</span></div>)}{!slots.some(slot => slot.weekday === index + 1) && <small>Tempo per te</small>}</div>)}</div><form onSubmit={async event => { event.preventDefault(); setError(''); if (slots.some(slot => slot.start >= slot.end)) { setError('Ogni appuntamento deve terminare dopo l’orario di inizio.'); return } setSaved(await onSave({ weekly_schedule: slots, auto_detection: detection })) }}><section className="wl-card"><div className="wl-card-heading"><h2>Appuntamenti ricorrenti</h2><span>ORA DI ROMA</span></div><div className="wl-calendar-list">{slots.map((slot, index) => <div className="wl-calendar-row" key={slot.id}><label>Giorno<select value={slot.weekday} onChange={event => editSlot(index, { weekday: Number(event.target.value) })}>{WEEKDAYS.map((label, at) => <option key={label} value={at + 1}>{label}</option>)}</select></label><label>Dalle<input type="time" required value={slot.start} onChange={event => editSlot(index, { start: event.target.value })} /></label><label>Alle<input type="time" required value={slot.end} onChange={event => editSlot(index, { end: event.target.value })} /></label><label>Nome<input required maxLength={80} value={slot.label} onChange={event => editSlot(index, { label: event.target.value })} /></label><button type="button" className="wl-icon-button" aria-label={`Elimina appuntamento ${index + 1}`} onClick={() => setSlots(slots.filter((_, at) => at !== index))}><Icon name="close" size={18} /></button></div>)}</div><Button type="button" icon="add" disabled={disabled || slots.length >= 100} onClick={() => setSlots([...slots, { id: crypto.randomUUID(), weekday: 1, start: '09:00', end: '10:00', timezone: WELLNESS_TIMEZONE, label: 'Palestra' }])}>Aggiungi appuntamento</Button></section><section className="wl-card wl-auto-card"><div className="wl-card-heading"><div><h2>Quando arrivi in palestra</h2><p>L’iPhone può riconoscere il luogo e l’aumento prolungato del battito, poi proporti una sessione.</p></div><label className="wl-switch"><input type="checkbox" checked={detection.enabled} onChange={event => setDetection({ ...detection, enabled: event.target.checked })} /><span>Rilevamento automatico</span></label></div><div className="wl-form-grid"><label>Nome della palestra<input maxLength={80} value={gym.name} onChange={event => setDetection({ ...detection, gym: { ...gym, name: event.target.value } })} /></label><label>Indirizzo<input maxLength={250} value={gym.address} onChange={event => setDetection({ ...detection, gym: { ...gym, address: event.target.value } })} /></label><label>Latitudine · opzionale<input type="number" step="any" min={-90} max={90} value={gym.latitude ?? ''} onChange={event => setDetection({ ...detection, gym: { ...gym, latitude: optionalNumber(event.target.value) } })} /></label><label>Longitudine · opzionale<input type="number" step="any" min={-180} max={180} value={gym.longitude ?? ''} onChange={event => setDetection({ ...detection, gym: { ...gym, longitude: optionalNumber(event.target.value) } })} /></label><label>Raggio del luogo · m<input type="number" min={25} max={2000} value={gym.radius_m} onChange={event => setDetection({ ...detection, gym: { ...gym, radius_m: Number(event.target.value) } })} /></label><label>Aumento del battito · bpm<input type="number" min={1} max={100} value={detection.heart_rate_delta_bpm} onChange={event => setDetection({ ...detection, heart_rate_delta_bpm: Number(event.target.value) })} /></label><label>Persistenza · secondi<input type="number" min={5} max={600} value={detection.sustain_seconds} onChange={event => setDetection({ ...detection, sustain_seconds: Number(event.target.value) })} /></label><label>Pausa tra proposte · minuti<input type="number" min={1} max={1440} value={detection.cooldown_seconds / 60} onChange={event => setDetection({ ...detection, cooldown_seconds: Number(event.target.value) * 60 })} /></label></div><p className="wl-note">Sull’iPhone scegli i permessi Posizione “Sempre” e Notifiche quando vuoi abilitare questa funzione. Puoi confermare l’attività quando sei pronto. I parametri di rilevamento non definiscono zone mediche o soglie metaboliche.</p></section>{error && <p className="wl-inline-error" role="alert">{error}</p>}<div className="wl-form-actions"><Button type="submit" primary icon="check" disabled={disabled}>Salva calendario e rilevamento</Button>{saved && <span role="status">Preferenze salvate</span>}</div></form></div>
}

function DevicesPanel({ devices, archive, now, disabled, busy, onPair, onRevoke, onBinding }: { devices: BridgeDevice[]; archive?: WellnessState['archive']; now: number; disabled: boolean; busy: string; onPair: () => void; onRevoke: (id: string) => Promise<unknown>; onBinding: (key: string, id: string) => Promise<boolean> }) {
  const [manual, setManual] = useState(false), [key, setKey] = useState(''), [ringId, setRingId] = useState(''), [bindingError, setBindingError] = useState('')
  const secure = window.location.protocol === 'https:'
  return <div className="wl-editor"><div className="wl-page-title"><div><span className="wl-eyebrow">UN COLLEGAMENTO PERSONALE</span><h1>Dal Gen3 alla tua TV.</h1><p>L’iPhone raccoglie i segnali. FamilOS tiene insieme il tuo allenamento.</p></div></div><div className="wl-device-flow"><div><Icon name="ring" size={35} /><strong>Oura Gen3</strong><span>I tuoi sensori</span></div><i /><div><Icon name="phone" size={35} /><strong>Il tuo iPhone</strong><span>Bluetooth e coda offline</span></div><i /><div><Icon name="pulse" size={35} /><strong>FamilOS</strong><span>Dashboard e archivio personale</span></div></div><div className="wl-device-grid"><section className="wl-card"><div className="wl-card-heading"><h2>Dispositivi associati</h2><Button primary icon="add" disabled={disabled} onClick={onPair}>{busy === 'pair' ? 'Preparazione…' : 'Collega iPhone'}</Button></div>{!devices.length && <p className="wl-empty-note">Apri l’app iPhone, poi usa il codice o il QR che generi qui. Il codice è monouso e ha una scadenza.</p>}<div className="wl-device-list">{devices.map(device => <div className="wl-device-item" key={device.id}><span className="wl-device-icon"><Icon name="phone" size={24} /></span><div><strong>{device.name}</strong><small>{device.revoked ? 'Collegamento revocato' : `Ultimo contatto ${ageLabel(device.last_seen_at, now)}`}</small></div>{!device.revoked && <Button disabled={disabled} onClick={() => { if (window.confirm(`Revocare il collegamento di ${device.name}? Potrai associarlo di nuovo.`)) onRevoke(device.id) }}>Revoca</Button>}</div>)}</div></section><section className="wl-card wl-archive-card"><span className="wl-eyebrow">IL TUO ARCHIVIO</span><h2>Ogni campione al suo posto.</h2><div><strong>{valueLabel(archive?.history_records)}</strong><span>record storici catalogati</span></div><div><strong>{valueLabel(archive?.events)}</strong><span>eventi ricevuti</span></div><p className="wl-note">I dati ricevuti in ritardo vengono accodati senza duplicare i record. Restano associati all’orario di acquisizione.</p>{!!archive?.decode_errors && <p className="wl-archive-warning">{archive.decode_errors} record da verificare durante l’elaborazione.</p>}{archive?.processing_status === 'processing' && <p role="status" className="wl-note">Elaborazione dei sensori in corso…</p>}</section></div><section className="wl-card wl-mobile-permissions"><h2>Pronto anche quando lo schermo si spegne</h2><p>Nell’app iPhone scegli Bluetooth, notifiche e, se usi il rilevamento in palestra, posizione “Sempre”. I dati in attesa vengono conservati nella coda dell’iPhone e inviati quando il server torna raggiungibile.</p></section><section className="wl-card wl-manual-binding"><button className="wl-text-button" onClick={() => { setManual(!manual); setKey(''); setBindingError('') }} aria-expanded={manual}>Configurazione manuale dell’anello</button>{manual && <form onSubmit={async event => { event.preventDefault(); setBindingError(''); if (!secure) { setBindingError('Usa il server privato tramite HTTPS per inserire una chiave.'); return } if (!/^[0-9a-fA-F]{32}$/.test(key.trim())) { setBindingError('La chiave deve contenere 32 caratteri esadecimali.'); return } const value = key.trim(); setKey(''); await onBinding(value, ringId.trim()) }}><p className="wl-note">Usa questa opzione solo se devi importare la tua chiave proprietario. Viene inviata nel corpo della richiesta al server privato HTTPS, mai in un URL e mai mostrata tra i dati pubblici della dashboard.</p><div className="wl-form-grid"><label>Identificatore logico dell’anello<input required maxLength={120} value={ringId} onChange={event => setRingId(event.target.value)} autoComplete="off" /></label><label>Chiave proprietario · 16 byte<input required type="password" maxLength={32} value={key} onChange={event => setKey(event.target.value)} autoComplete="off" spellCheck={false} disabled={!secure || disabled} /></label></div>{!secure && <p className="wl-inline-error">L’inserimento della chiave richiede HTTPS sul tuo server privato.</p>}{bindingError && <p className="wl-inline-error" role="alert">{bindingError}</p>}<Button type="submit" disabled={disabled || !secure || !key.trim() || !ringId.trim()}>Importa associazione</Button></form>}</section></div>
}
function PairingDialog({ pairing, now, onClose, onNotice }: { pairing: Pairing; now: number; onClose: () => void; onNotice: (message: string) => void }) {
  const expires = epochSeconds(pairing.expires_at), expired = expires != null && now >= expires
  async function copy(value: string) { try { await navigator.clipboard.writeText(value); onNotice('Copiato negli appunti.'); } catch { onNotice('Copia manualmente il testo mostrato nella finestra.'); } }
  return <Modal title="Collega il tuo iPhone" onClose={onClose}><p className="wl-modal-intro">Nell’app FamilOS Wellness sul tuo iPhone, scegli “Collega a FamilOS” e inquadra il QR oppure inserisci server e codice.</p><div className="wl-pair-content">{pairing.qr_png_data_url?.startsWith('data:image/png;base64,') && !expired && <img className="wl-pair-qr" src={pairing.qr_png_data_url} alt="QR per associare questo iPhone al tuo profilo" referrerPolicy="no-referrer" />}<div className="wl-pair-details"><span className="wl-eyebrow">CODICE MONOUSO</span><strong className="wl-pair-code">{expired ? 'SCADUTO' : pairing.pairing_code}</strong><span>{expired ? 'Chiudi e genera un nuovo codice.' : expires != null ? `Scade tra ${durationLabel(expires - now)}` : 'Usa il codice prima della scadenza indicata dal server.'}</span><Button icon="copy" disabled={expired} onClick={() => copy(pairing.pairing_code)}>Copia codice</Button></div></div><label>Indirizzo del tuo server<input readOnly value={pairing.server_url} aria-label="Indirizzo server FamilOS" onFocus={event => event.currentTarget.select()} /></label><div className="wl-form-actions"><Button icon="copy" onClick={() => copy(pairing.server_url)}>Copia indirizzo server</Button><Button primary onClick={onClose}>Ho finito</Button></div><p className="wl-note">Il codice associa un solo dispositivo al tuo profilo. Potrai revocarlo nella sezione dispositivi. La chiave dell’anello non compare nel QR o nell’indirizzo mostrato.</p></Modal>
}
function downloadCsv(analysis: WellnessAnalysis) {
  const url = URL.createObjectURL(new Blob([analysisCsv(analysis)], { type: 'text/csv;charset=utf-8' }))
  const link = document.createElement('a'); link.href = url; link.download = 'familos-allenamento.csv'; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000)
}


function CardioSummary({analysis}:{analysis:WellnessAnalysis|null}) {
  const stats=analysis?.cardio
  if(!stats)return null
  return <section className="wl-card" aria-label="Statistiche fasce cardio"><h2>Tempo nelle fasce cardio</h2>{stats.target_seconds>0?<><div className="wl-session-stats"><div><span>Sotto fascia</span><strong>{durationLabel(stats.below_seconds)}</strong></div><div><span>Dentro fascia</span><strong>{durationLabel(stats.inside_seconds)}</strong></div><div><span>Sopra fascia</span><strong>{durationLabel(stats.above_seconds)}</strong></div><div><span>Senza misure</span><strong>{durationLabel(stats.unknown_seconds)}</strong></div></div><p>Copertura: {valueLabel((stats.coverage_fraction||0)*100)}% del tempo con obiettivi.</p>{stats.phases.map(phase=><div key={phase.phase_index}><h3>{phase.name} · {phase.target_min}–{phase.target_max} BPM</h3><p>Dentro {durationLabel(phase.inside_seconds)} · sotto {durationLabel(phase.below_seconds)} · sopra {durationLabel(phase.above_seconds)} · senza misure {durationLabel(phase.unknown_seconds)}</p></div>)}</>:<p>Nessuna fascia impostata nella scheda di questa sessione.</p>}<p className="wl-note">Le durate sono stimate dagli intervalli fra campioni consecutivi, fino a 8 secondi. Pause e interruzioni del segnale sono escluse. {durationLabel(stats.unconfigured_seconds)} senza un obiettivo configurato.</p></section>
}
