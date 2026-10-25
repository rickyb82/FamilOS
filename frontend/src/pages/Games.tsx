import { useEffect, useState } from 'react'
import { get, post } from '../lib/api'
import { useAuth } from '../lib/auth'
import { parseProject } from '../games/project'
import FamilyGamePlayer from '../components/FamilyGamePlayer'
interface SavedGame {id:string;title:string;runtime:string;updated_at:string;content?:string;sharing:'private'|'pending'|'approved';owner_id:string;revision:number}
interface Room {id:string;game_id:string;title:string;players:number}
export default function Games() {
 const {me}=useAuth(),adult=me?.user.role!=='child'
 const [games,setGames]=useState<SavedGame[]>([]),[rooms,setRooms]=useState<Room[]>([]),[playing,setPlaying]=useState<SavedGame|null>(null),[room,setRoom]=useState<string>(),[error,setError]=useState(''),[busy,setBusy]=useState(false)
 const load=async()=>{try{const [list,live]=await Promise.all([get<SavedGame[]>('/games'),get<Room[]>('/game-rooms')]);setGames(list);setRooms(live)}catch(e:any){setError(e.message)}}
 useEffect(()=>{load();const timer=setInterval(load,10000);return()=>clearInterval(timer)},[])
 async function run(fn:()=>Promise<any>){setBusy(true);setError('');try{await fn();await load()}catch(e:any){setError(e.message)}finally{setBusy(false)}}
 async function play(id:string,rid?:string){await run(async()=>{const selected=await get<SavedGame>('/games/'+id);parseProject(selected.content!);setRoom(rid);setPlaying(selected)})}
 async function importGame(file?:File){if(!file)return;await run(async()=>{if(file.size>12*1024*1024)throw new Error('Massimo 12 MiB per gioco');const content=await file.text();parseProject(content);await post('/games',{content,title:file.name.replace(/\.json$/i,'')})})}
 if(playing)return <FamilyGamePlayer game={{id:playing.id,title:playing.title,content:playing.content!}} room={room} onExit={()=>{setPlaying(null);setRoom(undefined);load()}}/>
 return <div className="page stack"><header><h1>🎮 Giochi</h1><p>Crea nella chat, salva qui e gioca. I mondi condivisi restano solo nella tua famiglia.</p></header>{error&&<p role="alert">{error}</p>}<div className="row"><label className="card">Importa gioco FamilOS<input type="file" accept=".json" disabled={busy} onChange={e=>importGame(e.target.files?.[0])}/></label><button disabled={busy} onClick={()=>run(async()=>{await post('/games/templates/ragnoman')} )}>Aggiungi Ragnoman Rooftops</button><button disabled={busy} onClick={load}>Aggiorna cartella</button></div>
 {rooms.length>0&&<section className="card stack"><h2>Stanze della famiglia</h2>{rooms.map(r=><div key={r.id} className="row"><b>{r.title}</b><span>{r.players}/8 giocatori</span><button disabled={busy||r.players>=8} onClick={()=>play(r.game_id,r.id)}>Entra</button></div>)}</section>}
 {!games.length&&<p>Crea un gioco nella chat e premi «Salva in Giochi», oppure aggiungi il mondo di esempio.</p>}<div className="grid2">{games.map(g=><section className="card stack" style={{padding:24}} key={g.id}><span style={{fontSize:38}}>🎮</span><h2>{g.title}</h2><small>{g.sharing==='approved'?'Disponibile alla famiglia':g.sharing==='pending'?'In attesa di approvazione':'Privato · solo tu'} · {new Date(g.updated_at).toLocaleDateString()}</small><button disabled={busy} onClick={()=>play(g.id)}>▶ {g.sharing==='pending'&&g.owner_id!==me?.user.id?'Prova prima di approvare':'Gioca'}</button>
 {g.sharing==='approved'&&<button disabled={busy} onClick={()=>run(async()=>{const r=await post('/game-rooms',{game_id:g.id});const selected=await get<SavedGame>('/games/'+g.id);setRoom(r.id);setPlaying(selected)})}>Crea stanza famiglia</button>}
 {g.owner_id===me?.user.id&&g.sharing==='private'&&<button disabled={busy} onClick={()=>run(()=>post('/games/'+g.id+'/share'))}>{adult?'Condividi con la famiglia':'Chiedi di condividere'}</button>}
 {adult&&g.sharing==='pending'&&<div className="row"><button disabled={busy} onClick={()=>{if(confirm('Hai provato questa versione? Approvarla la rende giocabile agli account della tua famiglia.'))run(()=>post('/games/'+g.id+'/review',{approve:true,revision:g.revision}))}}>Approva condivisione</button><button disabled={busy} onClick={()=>run(()=>post('/games/'+g.id+'/review',{approve:false,revision:g.revision}))}>Non approvare</button></div>}
 {g.sharing!=='private'&&(adult||g.owner_id===me?.user.id)&&<button disabled={busy} onClick={()=>run(()=>post('/games/'+g.id+'/unshare'))}>Ritira condivisione</button>}
 </section>)}</div><small>Quando aggiorni un gioco salvato, torna privato: la nuova versione va condivisa e, per i bambini, approvata di nuovo. Le stanze supportano fino a 8 account; il gioco deve usare le funzioni multiplayer FamilOS.</small></div>
}
