import { useEffect,useRef,useState,type ReactNode } from 'react'
import { startAuthentication } from '@simplewebauthn/browser'
import { post } from '../lib/api'
import { useAuth } from '../lib/auth'
import { useBranding } from '../lib/branding'

export async function lockSession(){await post('/auth/lock',{});localStorage.setItem('familos:session',String(Date.now()));window.dispatchEvent(new Event('familos:locked'))}
export default function LockScreen({children}:{children:ReactNode}){
 const {me,refresh,logout}=useAuth(),{brand}=useBranding()
 const [secret,setSecret]=useState(''),[error,setError]=useState(''),[busy,setBusy]=useState(false),[now,setNow]=useState(new Date())
 const lastActivity=useRef(Date.now()),locking=useRef(false)
 const locked=!!me?.session_locked
 useEffect(()=>{
  const update=()=>{lastActivity.current=Date.now()}
  const timer=window.setInterval(()=>{
   const minutes=me?.prefs?.appearance?.lock_after_minutes??5
   if(me&&!locked&&minutes&&Date.now()-lastActivity.current>minutes*60000&&!locking.current){locking.current=true;lockSession().catch(()=>{}).finally(()=>locking.current=false)}
   setNow(new Date())
  },10000)
  window.addEventListener('pointerdown',update);window.addEventListener('keydown',update);window.addEventListener('pointermove',update)
  return()=>{clearInterval(timer);window.removeEventListener('pointerdown',update);window.removeEventListener('keydown',update);window.removeEventListener('pointermove',update)}
 },[me?.user.id,locked,me?.prefs?.appearance?.lock_after_minutes])
 async function run(action:()=>Promise<unknown>){setBusy(true);setError('');try{await action();setSecret('');lastActivity.current=Date.now();await refresh();localStorage.setItem("familos:session",String(Date.now()))}catch(e:any){setError(e.message)}finally{setBusy(false)}}
 return <><div className="os-lock-content" aria-hidden={locked} style={locked?{display:'none'}:undefined}>{children}</div>
 {locked&&<main className="os-lockscreen"><time>{now.toLocaleTimeString('it-IT',{hour:'2-digit',minute:'2-digit'})}</time><p className="os-lock-date">{now.toLocaleDateString('it-IT',{weekday:'long',day:'numeric',month:'long'})}</p><section className="os-lock-card"><img src={brand.logo_url} alt=""/><h1>{me?.user.display_name}</h1><p>{brand.name} è bloccato</p>
 {me?.user.has_passkey&&<button disabled={busy} onClick={()=>run(async()=>{const options=await post('/auth/unlock/passkey/options',{});const credential=await startAuthentication({optionsJSON:options});await post('/auth/unlock/passkey/verify',{credential})})}>Sblocca con Face ID / impronta</button>}
 <form onSubmit={e=>{e.preventDefault();run(()=>post('/auth/unlock/password',{secret}))}}><label>{me?.user.role==='child'?'PIN personale':'Password'}<input type="password" autoComplete="current-password" value={secret} onChange={e=>setSecret(e.target.value)} placeholder={me?.user.role==='child'?'Il tuo PIN':'La tua password'}/></label><button disabled={busy||!secret}>Sblocca</button></form>
 {error&&<p className="error" role="alert">{error}</p>}<small>Le passkey usano Face ID, Touch ID o Windows Hello sui dispositivi compatibili. Si configurano da Impostazioni → Passkey e sicurezza.</small><button className="ghost" onClick={logout}>Cambia utente</button></section></main>}
 </>
}
