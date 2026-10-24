import { useId } from 'react'
import type { AppEntry } from '../lib/desktop'

/** Original vector artwork; catalogue apps keep their own manifest icon. */
export default function AppIcon({app}:{app:AppEntry}) {
 const uid=useId().replace(/:/g,'')
 const id=app.id.replace(/^editor-/, '')
 const palette:Record<string,string[]>={chat:['#d08cff','#6844df'],files:['#65d6ff','#1877ed'],messages:['#8deb91','#19a65b'],browser:['#61e7ff','#2157ce'],projects:['#ffcb7c','#e97838'],wordcraft:['#6ebcff','#2454c8'],gridcraft:['#83e5b1','#16865b'],pdfcraft:['#ff9e9a','#d14166'],deckcraft:['#ffc974','#ed7d37'],ideacraft:['#ffe992','#dc9a26'],mailcraft:['#74d8ff','#386be1']}
 const [top,bottom]=palette[id]||['#b8bdd5','#68718e']
 let glyph=<><rect x="19" y="18" width="26" height="28" rx="5"/><path d="M25 26h14M25 32h14M25 38h8"/></>
 if(id==='chat')glyph=<path d="m32 13 5 13 14 6-14 5-5 14-5-14-14-5 14-6Z"/>
 if(id==='files'||id==='projects')glyph=<><path d="M12 23v-5h16l5 5h19v25H12Z"/><path d="M12 27h40l-4 21H16Z"/></>
 if(id==='messages')glyph=<><path d="M14 18h36v25H30l-10 8v-8h-6Z"/><path d="M23 30h1m7 0h1m7 0h1"/></>
 if(id==='browser')glyph=<><circle cx="32" cy="32" r="20"/><path d="m41 21-5 16-14 6 6-16Z"/><path d="m28 27 8 10"/></>
 if(id==='settings'||id==='installation')glyph=<><path d="m27 13 10 0 2 6 6 3 6 0 4 9-5 5-1 6 1 5-9 6-5-4-7 0-5 3-8-7 2-6-1-6-4-4 4-9 6 0Z" transform="translate(0 -2) scale(.95)"/><circle cx="32" cy="31" r="8"/></>
 if(id==='apps')glyph=<>{[19,33,47].flatMap(x=>[19,33,47].map(y=><rect key={`${x}-${y}`} x={x-5} y={y-5} width="9" height="9" rx="2"/>))}</>
 if(id==='family'||id==='parental')glyph=<><circle cx="25" cy="24" r="7"/><circle cx="42" cy="28" r="5"/><path d="M12 48v-6c0-13 26-13 26 0v6m0-12c9-3 15 4 15 12"/></>
 if(id==='mailcraft')glyph=<><rect x="12" y="19" width="40" height="28" rx="4"/><path d="m13 21 19 15 19-15"/></>
 if(id==='gridcraft')glyph=<><rect x="16" y="15" width="33" height="35" rx="4"/><path d="M16 26h33M16 37h33M27 15v35M38 15v35"/></>
 if(id==='deckcraft')glyph=<><rect x="13" y="15" width="38" height="28" rx="3"/><path d="M32 43v9m-9 0h18M22 34v-8m10 8V22m10 12v-5"/></>
 if(id==='ideacraft')glyph=<><path d="M24 42c0-7-8-8-8-17a16 16 0 0 1 32 0c0 9-8 10-8 17Z"/><path d="M25 48h14m-12 5h10M32 42V28"/></>
 return <span className={`os-icon os-icon-art ${app.color}`} aria-hidden="true">{app.iconUrl?<img src={app.iconUrl} alt=""/>:<svg viewBox="0 0 64 64" fill="none"><defs><linearGradient id={uid} x2=".8" y2="1"><stop stopColor={top}/><stop offset="1" stopColor={bottom}/></linearGradient><linearGradient id={`${uid}shine`} x2="0" y2="1"><stop stopColor="white" stopOpacity=".5"/><stop offset="1" stopColor="white" stopOpacity="0"/></linearGradient></defs><rect x="1" y="1" width="62" height="62" rx="16" fill={`url(#${uid})`}/><rect x="2" y="2" width="60" height="31" rx="15" fill={`url(#${uid}shine)`}/><g stroke="white" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round" fill="white" fillOpacity=".18">{glyph}</g><rect x="1.5" y="1.5" width="61" height="61" rx="15.5" stroke="white" strokeOpacity=".45"/></svg>}</span>
}
