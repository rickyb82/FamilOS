import { useEffect,useState } from 'react'
import { get,post } from '../lib/api'
interface Page {url:string;title:string;text:string;links:{url:string;label:string}[]}
export default function SafeBrowser(){
 const [sites,setSites]=useState<string[]>([]),[url,setUrl]=useState(''),[pages,setPages]=useState<Page[]>([]),[position,setPosition]=useState(-1),[busy,setBusy]=useState(false),[error,setError]=useState('')
 const page=pages[position]
 useEffect(()=>{get<{sites:string[]}>('/browser/sites').then(c=>setSites(c.sites)).catch(e=>setError(e.message))},[])
 async function open(target:string){setBusy(true);setError('');try{const next=await post<Page>('/browser/read',{url:target});setPages(values=>[...values.slice(0,position+1),next]);setPosition(position+1);setUrl(next.url)}catch(e:any){setError(e.message)}finally{setBusy(false)}}
 function move(next:number){setPosition(next);setUrl(pages[next].url);setError('')}
 return <div className="safe-browser">
 <form className="safe-browser-toolbar" onSubmit={e=>{e.preventDefault();open(url)}}><button type="button" disabled={busy||position<1} onClick={()=>move(position-1)} aria-label="Pagina precedente">←</button><button type="button" disabled={busy||position>=pages.length-1} onClick={()=>move(position+1)} aria-label="Pagina successiva">→</button><button type="button" aria-label="Siti autorizzati" onClick={()=>{setPosition(-1);setUrl('')}}>⌂</button><input aria-label="Indirizzo del sito" placeholder="Scrivi un sito HTTPS…" value={url} onChange={e=>setUrl(e.target.value)}/><button disabled={busy||!url.trim()}>{busy?'Verifico…':'Apri'}</button></form>
 <div className="safe-browser-body">{error&&<p className="error" role="alert">{error}</p>}{busy&&<p role="status">Controllo il sito e la pagina prima di mostrarli…</p>}
 {!page&&<><p className="os-eyebrow">ESPLORA E IMPARA</p><h1>Navigazione protetta</h1><p className="muted">Scegli un sito autorizzato. Le pagine si aprono in modalità lettura, senza pubblicità attive, script o moduli da compilare.</p><div className="safe-browser-sites">{sites.map(site=><button key={site} disabled={busy} onClick={()=>open('https://'+site)}>◎<span>{site}</span>↗</button>)}</div></>}
 {page&&<article><small className="muted">{new URL(page.url).hostname} · modalità lettura</small><h1>{page.title}</h1><div className="safe-browser-text">{page.text}</div>{!!page.links.length&&<section><h2>Continua a esplorare</h2><div className="safe-browser-links">{page.links.map((link,index)=><button key={index} disabled={busy} onClick={()=>open(link.url)}>{link.label} →</button>)}</div></section>}</article>}
 </div></div>
}
