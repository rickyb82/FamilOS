import { ParentBrowserReview } from "./BrowserReviews"
import { useEffect,useState } from 'react'
import { get,put } from '../lib/api'
import { useAuth } from '../lib/auth'
interface Child{id:string;display_name:string;avatar:string;age_group:string;education_mode:string;override_until:string}
export default function ParentalControls(){
 const {me}=useAuth()
 const [children,setChildren]=useState<Child[]>([]),[duration,setDuration]=useState('hour'),[busy,setBusy]=useState(false),[error,setError]=useState('')
 const load=()=>get<Child[]>('/parental/children').then(setChildren)
 useEffect(()=>{if(me?.user.role!=='child')load().catch(e=>setError(e.message))},[])
 async function change(child:Child,mode:string){setBusy(true);setError('');try{await put('/parental/children/'+child.id+'/education',{mode,duration});await load()}catch(e:any){setError(e.message)}finally{setBusy(false)}}
 if(me?.user.role==='child')return <div className="page"><h2>Solo per i genitori</h2></div>
 return <div className="page stack narrow"><h1>Genitori</h1><p className="muted">La modalità tutor aiuta a imparare passo passo. Puoi autorizzare temporaneamente risposte dirette; restano attivi filtri, limiti e orari del profilo.</p>{error&&<p className="error" role="alert">{error}</p>}<label>Durata dello sblocco<select value={duration} onChange={e=>setDuration(e.target.value)}><option value="hour">Un’ora</option><option value="today">Fino a fine giornata</option></select></label>{!children.length&&<p>Nessun account bambino presente. Aggiungilo dalla gestione Famiglia.</p>}{children.map(child=><section className="card stack" key={child.id}><h2>{child.avatar} {child.display_name}</h2><p>{child.education_mode==='tutor'?'Tutor educativo attivo':'Risposte dirette autorizzate fino alle '+new Date(child.override_until).toLocaleTimeString('it-IT',{hour:'2-digit',minute:'2-digit'})}</p><button disabled={busy} onClick={()=>change(child,child.education_mode==='tutor'?'standard':'tutor')}>{child.education_mode==='tutor'?'Sblocca risposte dirette':'Ripristina subito il tutor'}</button></section>)}<ParentBrowserReview /></div>
}
