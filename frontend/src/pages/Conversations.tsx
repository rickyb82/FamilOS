import {useEffect,useState} from 'react'
import {useNavigate} from 'react-router-dom'
import {get,del,type Conversation} from '../lib/api'
import {useAuth} from '../lib/auth'
export default function Conversations(){
 const {me}=useAuth(),navigate=useNavigate()
 const [rows,setRows]=useState<Conversation[]>([]),[query,setQuery]=useState(''),[selected,setSelected]=useState<string[]>([]),[error,setError]=useState(''),[loading,setLoading]=useState(true),[busy,setBusy]=useState(false)
 async function load(){try{setRows(await get<Conversation[]>('/chat/conversations'));setError('')}catch(e:any){setError(e.message)}finally{setLoading(false)}}
 useEffect(()=>{load();window.addEventListener('familai:conversations',load);return()=>window.removeEventListener('familai:conversations',load)},[])
 const shown=rows.filter(c=>c.title.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase())).sort((a,b)=>b.updated_at.localeCompare(a.updated_at))
 async function remove(ids:string[]){
  const own=ids.filter(id=>rows.some(c=>c.id===id&&c.owner_id===me?.user.id))
  if(!own.length||!confirm(`Eliminare definitivamente ${own.length===1?'questa chat':own.length+' chat'} e i relativi messaggi e artifact? I documenti e le copie salvate in Giochi rimangono.`))return
  setBusy(true);setError('');const deleted:string[]=[]
  try{for(const id of own){await del('/chat/conversations/'+id);deleted.push(id)}}catch(e:any){setError('Eliminazione interrotta: '+e.message)}finally{setRows(r=>r.filter(c=>!deleted.includes(c.id)));setSelected(s=>s.filter(id=>!deleted.includes(id)));setBusy(false);window.dispatchEvent(new Event('familai:conversations'))}
 }
 return <div className="page stack"><header className="row between"><h2>Tutte le chat</h2><button onClick={()=>navigate('/')}>Nuova chat</button></header><label>Cerca per titolo<input value={query} onChange={e=>setQuery(e.target.value)} placeholder="Cerca nelle tue chat…"/></label><div className="row wrap"><span>{shown.length} chat</span><button disabled={busy||!shown.some(c=>c.owner_id===me?.user.id)} onClick={()=>setSelected(shown.filter(c=>c.owner_id===me?.user.id).map(c=>c.id))}>Seleziona le mie chat visibili</button><button disabled={busy||!selected.length} onClick={()=>setSelected([])}>Deseleziona</button><button disabled={busy||!selected.length} onClick={()=>remove(selected)}>Elimina selezionate ({selected.length})</button></div>{error&&<p role="alert">{error}</p>}{loading?<p>Caricamento…</p>:!shown.length?<p>Nessuna chat trovata.</p>:shown.map(c=><article className="card row between" key={c.id}><div className="row">{c.owner_id===me?.user.id&&<input type="checkbox" aria-label={'Seleziona '+c.title} disabled={busy} checked={selected.includes(c.id)} onChange={e=>setSelected(s=>e.target.checked?[...s,c.id]:s.filter(id=>id!==c.id))}/>}<button className="ghost" onClick={()=>navigate('/c/'+c.id)}>{c.title}</button></div><div className="row wrap"><small>{c.owner_id!==me?.user.id?'Condivisa · ':''}{new Date(c.updated_at).toLocaleString('it-IT')}</small>{c.owner_id===me?.user.id&&<button disabled={busy} onClick={()=>remove([c.id])}>Elimina</button>}</div></article>)}</div>
}
