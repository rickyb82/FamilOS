import { useEffect, useState } from 'react'
import { get, post } from '../lib/api'
export interface Installed { id:string; name:string; description:string; version:string; enabled:boolean; icon:string; users:string[] }

export default function AppCatalog({ disabled, onChange }: { disabled:boolean; onChange:()=>void }) {
  const [members, setMembers] = useState<Array<{id:string;display_name:string;role:string}>>([])
  const [users, setUsers] = useState<string[]>([])
  useEffect(()=>{get<Array<{id:string;display_name:string;role:string}>>('/admin/members').then(m=>setMembers(m.filter(x=>x.role==='adult'))).catch(()=>{})},[])
  const [apps, setApps] = useState<Installed[]>([])
  const [manifest, setManifest] = useState<any>(null)
  const [icon, setIcon] = useState('')
  const [confirmed, setConfirmed] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  useEffect(() => { get<Installed[]>('/installed-apps').then(setApps).catch(e=>setError(e.message)) }, [disabled])
  async function install() {
    setBusy(true);setError('')
    try { await post('/admin/app-catalog/install',{manifest,icon,users,confirm_maintenance:confirmed});setConfirmed(false);onChange() }
    catch(e:any) {setError(e.message)} finally {setBusy(false)}
  }
  async function action(app:Installed, access = false) {
    setBusy(true);setError('')
    try {await post(`/admin/app-catalog/${app.id}/action`,{action:access?'access':app.enabled?'stop':'start',users:access?users:[],confirm_maintenance:confirmed});setConfirmed(false);onChange()}
    catch(e:any) {setError(e.message)} finally {setBusy(false)}
  }
  return <section className="card stack">
    <h3>Catalogo applicazioni</h3>
    <p>Importa il manifest e l’icona forniti dallo sviluppatore. FamilOS gestisce container, rete e dati senza chiedere porte o domini.</p>
    <p className="muted">Installa solo pacchetti di sviluppatori di cui ti fidi: l’immagine Docker contiene codice eseguibile. Gli amministratori hanno accesso. Puoi autorizzare anche gli adulti selezionati; i dati interni all’app potrebbero essere condivisi se lo sviluppatore non separa gli utenti.</p>
    {error && <p role="alert" className="error">{error}</p>}
    <label>Manifest FamilOS (.json)<input type="file" accept="application/json,.json" disabled={busy||disabled} onChange={async e=>{
      setError('');setManifest(null);const f=e.target.files?.[0];if(!f)return
      try {if(f.size>65536)throw Error('Manifest troppo grande');setManifest(JSON.parse(await f.text()))}catch(e:any){setError(e.message)}
    }}/></label>
    <label>Icona dell’app (.png)<input type="file" accept="image/png" disabled={busy||disabled} onChange={e=>{
      setIcon('');const f=e.target.files?.[0];if(!f)return
      if(f.size>131072){setError('Icona massimo 128 KiB');return}
      const reader=new FileReader();reader.onload=()=>setIcon(String(reader.result).split(',')[1]||'');reader.readAsDataURL(f)
    }}/></label>
    {manifest && <div className="installation-deployment"><b>{typeof manifest.name==='string'?manifest.name:'Manifest'} · {typeof manifest.version==='string'?manifest.version:''}</b><p>{typeof manifest.description==='string'?manifest.description:''}</p><p>Il server verifica compatibilità, digest delle immagini e permessi prima di installare.</p></div>}
    <fieldset><legend>Adulti da autorizzare</legend>{members.map(m=><label key={m.id} className="row"><input type="checkbox" checked={users.includes(m.id)} onChange={e=>setUsers(v=>e.target.checked?[...v,m.id]:v.filter(x=>x!==m.id))}/>{m.display_name}</label>)}<p className="muted">La selezione si applica alla nuova installazione oppure premendo “Salva accessi” su un’app esistente.</p></fieldset>
    <label className="row"><input type="checkbox" checked={confirmed} onChange={e=>setConfirmed(e.target.checked)} disabled={busy||disabled}/>Confermo il backup e l’interruzione temporanea dei servizi.</label>
    <button onClick={install} disabled={disabled||busy||!manifest||!icon||!confirmed}>Installa o aggiorna applicazione</button>
    <div className="app-catalog-grid">{apps.map(app=><article key={app.id} className="installation-deployment">
      <img src={'data:image/png;base64,'+app.icon} alt="" width="48" height="48"/>
      <h4>{app.name}</h4><p>{app.description}</p><p>{app.version} · {app.enabled?'Attiva':'Disattivata'}</p>
      <button disabled={disabled||busy||!confirmed} onClick={()=>action(app)}>{app.enabled?'Disattiva conservando i dati':'Avvia'}</button>
      <p>Adulti autorizzati: {app.users.length}</p><button disabled={disabled||busy||!confirmed} onClick={()=>action(app,true)}>Salva accessi selezionati</button>
    </article>)}</div>
    {!apps.length&&<p className="muted">Le app installate compariranno qui e nel desktop FamilOS.</p>}
  </section>
}
