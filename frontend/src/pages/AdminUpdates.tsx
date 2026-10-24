import { useEffect, useState } from 'react'
import { get, post } from '../lib/api'

type Channel = 'stable' | 'experimental'
interface Offer { id: string; tag: string; channel: Channel; experimental: boolean; newer: boolean; notes: string }
interface Status {
  connected: boolean; version?: string; channel: Channel; message?: string; setup_command?: string
  repositories?: Record<string, string>; offers?: Record<string, Offer | null>
  installed?: Record<string, { tag: string | null; enabled: boolean }>
  job?: { status: string; message: string; backup?: string; previous_commit?: string } | null
}
const targets = [['core', 'FamilOS'], ['wordcraft', 'WordCraft'], ['gridcraft', 'GridCraft'], ['pdfcraft', 'PdfCraft'], ['deckcraft', 'DeckCraft'], ['ideacraft', 'IdeaCraft'], ['mailcraft', 'MailCraft']]

export default function AdminUpdates() {
  const [status, setStatus] = useState<Status | null>(null)
  const [channel, setChannel] = useState<Channel>('stable')
  const [target, setTarget] = useState('core')
  const [confirmed, setConfirmed] = useState(false)
  const [experimental, setExperimental] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  useEffect(() => {
    let active = true
    let first = true
    let timer: ReturnType<typeof setTimeout>
    const refresh = async () => {
      try {
        const result = await get<Status>('/admin/updates')
        if (!active) return
        setStatus(result)
        if (first) { setChannel(result.channel); first = false }
      } catch (e: any) { if (active) setError(e.message) }
      finally { if (active) timer = setTimeout(refresh, 5000) }
    }
    refresh()
    return () => { active = false; clearTimeout(timer) }
  }, [])
  const offer = status?.offers?.[target]?.channel === channel ? status.offers[target] : null
  const blocked = ['running', 'recovery_required', 'interrupted'].includes(status?.job?.status || '')
  const disabled = busy || !status?.connected || blocked
  async function act(action: 'check' | 'install' | 'disable') {
    setBusy(true); setError('')
    try {
      await post('/admin/updates/' + action, action === 'disable' ? { target, confirm_maintenance: confirmed } : {
        target, channel, ...(action === 'install' ? { offer_id: offer?.id, confirm_maintenance: confirmed, accept_experimental: experimental } : {}),
      })
      setConfirmed(false); setExperimental(false)
      setStatus(await get<Status>('/admin/updates'))
    } catch (e: any) { setError(e.message) }
    finally { setBusy(false) }
  }
  return <section className="card stack">
    <h2>Aggiornamenti e app</h2>
    <p>Le release si installano solo su richiesta dell’amministratore. Versione del sistema: <b>{status?.version || '—'}</b>.</p>
    {error && <p role="alert" className="error">{error}</p>}
    {!status && <p>Connessione all’agente aggiornamenti…</p>}
    {status && !status.connected && <div className="installation-deployment"><h3>Attivazione sulla VM Ubuntu</h3><p>{status.message}</p><code>{status.setup_command}</code><p>Dopo l’attivazione, ricrea il backend come indicato dal comando. L’agente gestisce Docker dal server; questa pagina resta riservata agli amministratori.</p></div>}
    <label>Sistema o applicazione<select value={target} disabled={busy || blocked} onChange={e => { setTarget(e.target.value); setConfirmed(false); setExperimental(false) }}>{targets.map(([id, label]) => <option key={id} value={id}>{label}</option>)}</select></label>
    {status?.repositories?.[target] && <p className="muted">Sorgente configurata sul server: {status.repositories[target]}</p>}
    {target !== 'core' && <p>Release registrata: {status?.installed?.[target]?.tag || 'Non registrata dall’agente'}</p>}
    <label>Canale aggiornamenti<select value={channel} disabled={busy || blocked} onChange={e => { setChannel(e.target.value as Channel); setConfirmed(false); setExperimental(false) }}><option value="stable">Stabile · consigliato</option><option value="experimental">Sperimentale · include prerelease</option></select></label>
    <p className="muted">{channel === 'stable' ? 'Solo release stabili pubblicate. Nessun aggiornamento automatico.' : 'Include alpha, beta e release candidate pubblicate. Possono contenere problemi: scegli questo canale solo se vuoi provare le novità.'} Tornare al canale stabile non installa versioni precedenti e non annulla le migrazioni dei dati.</p>
    <button disabled={disabled} onClick={() => act('check')}>Controlla release</button>
    {status?.offers && target in status.offers && !status.offers[target] && <p role="status">Nessuna release compatibile pubblicata per il canale verificato.</p>}
    {offer && <div className="installation-deployment"><h3>{offer.tag} {offer.experimental ? '· Sperimentale' : '· Stabile'}</h3><p>{offer.newer ? 'Release disponibile per l’installazione.' : 'Nessuna versione successiva a quella installata.'}</p><details><summary>Novità della release</summary><p style={{ whiteSpace: 'pre-wrap' }}>{offer.notes}</p></details></div>}
    <label className="row"><input type="checkbox" checked={confirmed} disabled={disabled} onChange={e => setConfirmed(e.target.checked)} />Confermo il backup e l’interruzione temporanea del servizio.</label>
    {offer?.experimental && <label className="row"><input type="checkbox" checked={experimental} disabled={disabled} onChange={e => setExperimental(e.target.checked)} />Accetto di installare questa release sperimentale.</label>}
    <button disabled={disabled || !offer?.newer || !confirmed || (offer.experimental && !experimental)} onClick={() => act('install')}>Installa release {offer?.tag || ''}</button>
    {target !== 'core' && <button className="ghost" disabled={disabled || !confirmed} onClick={() => act('disable')}>Disattiva app conservando i dati</button>}
    <p className="muted small">Per installare o disattivare serve un accesso effettuato negli ultimi 15 minuti. Le prime compilazioni delle app possono richiedere molto tempo. Se il controllo finale fallisce, il recupero avviene dalla VM usando il backup; non è previsto un downgrade automatico.</p>
    {status?.job && <div className="installation-deployment" role="status"><h3>Stato operazione</h3><p>{status.job.message}</p>{status.job.backup && <p>Backup sulla VM: <code>{status.job.backup}</code></p>}{status.job.previous_commit && <p>Commit precedente: <code>{status.job.previous_commit}</code></p>}{['interrupted', 'recovery_required'].includes(status.job.status) && <p>È necessario un intervento sulla VM. Segui docs/UPDATES.md prima di avviare un altro aggiornamento.</p>}</div>}
  </section>
}
