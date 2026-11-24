import { useEffect, useState } from 'react'
import { get, put } from '../lib/api'

export default function WellnessModule() {
  const [enabled,setEnabled]=useState<boolean|null>(null),[busy,setBusy]=useState(false),[error,setError]=useState('')
  useEffect(()=>{get<{enabled:boolean}>('/admin/wellness-module').then(value=>setEnabled(value.enabled)).catch(e=>setError(e.message))},[])
  async function toggle(){setBusy(true);setError('');try{const next=await put<{enabled:boolean}>('/admin/wellness-module',{enabled:!enabled,confirm_maintenance:true});setEnabled(next.enabled);window.location.reload()}catch(e:any){setError(e.message)}finally{setBusy(false)}}
  return <section className="card stack" aria-label="Installazione Allenamento"><h3>Allenamento</h3><p>Scheda personale, fasce cardio, dashboard TV e collegamento Oura tramite iPhone o Android. Include i tool Allenamento nella chat Emily.</p><p>{enabled===null?'Verifica installazione…':enabled?'Modulo attivo':'Modulo non installato'}</p><button disabled={enabled===null||busy} onClick={toggle}>{enabled?'Disattiva conservando i dati':'Installa Allenamento'}</button><p className="muted small">Modulo opzionale incluso in questa versione. I dati restano personali; disattivarlo conserva archivio e associazioni. Il motore nativo per lo storico Oura è un componente aggiuntivo configurato separatamente.</p>{error&&<p role="alert" className="error">{error}</p>}</section>
}
