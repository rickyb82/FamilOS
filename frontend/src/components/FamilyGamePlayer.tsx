import { useEffect, useMemo, useRef, useState } from 'react'
import { post, del } from '../lib/api'
import { gameDocument, parseProject } from '../games/project'
export default function FamilyGamePlayer({game,room,onExit}:{game:{id:string;title:string;content:string};room?:string;onExit:()=>void}) {
 const frame=useRef<HTMLIFrameElement>(null),pose=useRef<any>({x:0,y:0,z:0,rotation:0,action:'idle'})
 const [restart,setRestart]=useState(0),[error,setError]=useState(''),[players,setPlayers]=useState(1),[logs,setLogs]=useState<string[]>([])
 const doc=useMemo(()=>gameDocument(parseProject(game.content)),[game.content])
 useEffect(()=>{const receive=(e:MessageEvent)=>{
  if(e.source!==frame.current?.contentWindow)return
  if(e.data?.type==='familos:game-log'&&e.data.level==='error')setLogs(l=>[...l.slice(-9),String(e.data.message).slice(0,2000)])
  if(e.data?.type==='familos:game-state'&&room){const s=e.data.state;if(s&&['x','y','z','rotation'].every(k=>typeof s[k]==='number'&&Number.isFinite(s[k])&&Math.abs(s[k])<=100000)&&['idle','run','jump','swing'].includes(s.action))pose.current={x:s.x,y:s.y,z:s.z,rotation:s.rotation,action:s.action}}
 };window.addEventListener('message',receive);return()=>window.removeEventListener('message',receive)},[room])
 useEffect(()=>{
  if(!room)return
  let active=true,timer:ReturnType<typeof setTimeout>
  const sync=async()=>{try{const result=await post('/game-rooms/'+room+'/state',pose.current);if(!active)return;setPlayers(result.players.length+1);frame.current?.contentWindow?.postMessage({type:'familos:game-peers',players:result.players},'*');setError('')}catch(e:any){if(!active)return;setError(e.message);if([401,403,404,409,423].includes(e.status)){active=false;return}}if(active)timer=setTimeout(sync,200)}
  sync();return()=>{active=false;clearTimeout(timer);del('/game-rooms/'+room+'/membership').catch(()=>{})}
 },[room])
 return <div className="stack" style={{height:'100%',padding:12,boxSizing:'border-box'}}><header className="row"><button onClick={onExit}>← Giochi</button><b>{game.title}</b><span>{room?`${players}/8 · Stanza famiglia`:'Gioco individuale'}</span><button onClick={()=>{setRestart(x=>x+1);setLogs([])}}>Riavvia</button><button onClick={onExit}>Ferma ed esci</button></header>{error&&<p role="alert">Multiplayer interrotto: {error}. Esci e riapri una stanza.</p>}<iframe key={`${game.id}-${restart}`} ref={frame} title={'Gioco: '+game.title} srcDoc={doc} sandbox="allow-scripts allow-pointer-lock" allow="autoplay; fullscreen" referrerPolicy="no-referrer" style={{flex:1,width:'100%',minHeight:320,border:0,borderRadius:16}}/>{!!logs.length&&<details><summary>Errori del gioco</summary><pre>{logs.join('\n')}</pre></details>}</div>
}
