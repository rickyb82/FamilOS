import { useId } from 'react'
import type { AppEntry } from '../lib/desktop'

/** Original vector artwork; catalogue apps keep their own manifest icon. */
export default function AppIcon({app}:{app:AppEntry}) {
 const uid=useId().replace(/:/g,'')
 const id=app.id.replace(/^editor-/, '')
 const palette:Record<string,string[]>={chat:['#d08cff','#6844df'],files:['#ffe38b','#edaa24'],messages:['#8deb91','#19a65b'],browser:['#61e7ff','#2157ce'],projects:['#ffcb7c','#e97838'],wordcraft:['#6ebcff','#2454c8'],gridcraft:['#83e5b1','#16865b'],pdfcraft:['#ff9e9a','#d14166'],deckcraft:['#ffc974','#ed7d37'],ideacraft:['#ffe992','#dc9a26'],mailcraft:['#74d8ff','#386be1']}
 const [top,bottom]=palette[id]||['#b8bdd5','#68718e']
 let glyph=<><path d="M18 12h21l9 9v31H18Z" fill="#fff"/><path d="M39 12v10h9M24 30h18M24 37h18M24 44h11" stroke={bottom}/></>
 if(id==='chat')glyph=<path d="m32 13 5 13 14 6-14 5-5 14-5-14-14-5 14-6Z"/>
 if(id==='files'||id==='projects')glyph=<><path d="M9 22v-7h19l6 7h21v29H9Z" fill="#ffe7a2" stroke="#ffe7a2"/><path d="M9 26h46l-3 25H12Z" fill="#fff3c4" stroke="#fff8dc"/><path d="M13 28h38" stroke="#fff"/></>
 if(id==='messages')glyph=<path d="M52 30c0 11-9 18-20 18h-6l-11 5 3-10c-4-3-6-7-6-13 0-11 9-18 20-18s20 7 20 18Z" fill="white" stroke="white"/>
 if(id==='browser')glyph=<><circle cx="32" cy="32" r="23" fill="#f4fbff" stroke="#dcf5ff"/><circle cx="32" cy="32" r="19" fill="#2b9fee" stroke="#92dbff"/><path d="m43 19-8 17-7-7Z" fill="#ff565d" stroke="#ff565d"/><path d="m21 45 7-16 7 7Z" fill="white" stroke="white"/></>
 if(id==='settings'||id==='installation')glyph=<><path d="m27 13 10 0 2 6 6 3 6 0 4 9-5 5-1 6 1 5-9 6-5-4-7 0-5 3-8-7 2-6-1-6-4-4 4-9 6 0Z" transform="translate(0 -2) scale(.95)"/><circle cx="32" cy="31" r="8" fill={bottom}/></>
 if(id==='apps')glyph=<>{[19,33,47].flatMap(x=>[19,33,47].map(y=><rect key={`${x}-${y}`} x={x-5} y={y-5} width="9" height="9" rx="2"/>))}</>
 if(id==='family'||id==='parental')glyph=<><circle cx="25" cy="24" r="7"/><circle cx="42" cy="28" r="5"/><path d="M12 48v-6c0-13 26-13 26 0v6m0-12c9-3 15 4 15 12"/></>
 if(id==='mailcraft')glyph=<><rect x="12" y="19" width="40" height="28" rx="4"/><path d="m13 21 19 15 19-15" stroke={bottom}/></>
 if(id==='gridcraft')glyph=<><rect x="16" y="15" width="33" height="35" rx="4"/><path d="M16 26h33M16 37h33M27 15v35M38 15v35" stroke={bottom}/></>
 if(id==='deckcraft')glyph=<><rect x="13" y="15" width="38" height="28" rx="3"/><path d="M32 43v9m-9 0h18M22 34v-8m10 8V22m10 12v-5" stroke={bottom}/></>
 if(id==='ideacraft')glyph=<><path d="M24 42c0-7-8-8-8-17a16 16 0 0 1 32 0c0 9-8 10-8 17Z"/><path d="M25 48h14m-12 5h10M32 42V28"/></>
 return <span className={`os-icon os-icon-art ${app.color}`} aria-hidden="true">{app.iconUrl?<img src={app.iconUrl} alt=""/>:<svg viewBox="0 0 64 64" fill="none"><defs><linearGradient id={uid} x2=".8" y2="1"><stop stopColor={top}/><stop offset="1" stopColor={bottom}/></linearGradient><linearGradient id={`${uid}shine`} x2="0" y2="1"><stop stopColor="white" stopOpacity=".5"/><stop offset="1" stopColor="white" stopOpacity="0"/></linearGradient></defs><rect x="1" y="1" width="62" height="62" rx="16" fill={`url(#${uid})`}/><rect x="2" y="2" width="60" height="31" rx="15" fill={`url(#${uid}shine)`}/><g stroke="white" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round" fill="white" fillOpacity="1">{glyph}</g><rect x="1.5" y="1.5" width="61" height="61" rx="15.5" stroke="white" strokeOpacity=".45"/></svg>}</span>
}
