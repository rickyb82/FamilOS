import { useBranding } from '../lib/branding'
import { useState } from 'react'
import { api,put } from '../lib/api'
import { useAuth } from '../lib/auth'
import { DEFAULT_APPEARANCE,DESKTOP_STYLES,type Appearance } from '../lib/appearance'
export default function ThemeSettings(){
 const {me,refresh}=useAuth()
 const {refresh:refreshBrand,personalWallpaper}=useBranding()
 const [wallpaper,setWallpaper]=useState<File|null>(null)
 const [value,setValue]=useState<Appearance>({...DEFAULT_APPEARANCE,...me?.prefs?.appearance})
 const [busy,setBusy]=useState(false),[message,setMessage]=useState(''),[error,setError]=useState('')
 async function save(){setBusy(true);setError('');try{await put('/auth/prefs',{appearance:value});await refresh();setMessage('Tema salvato sul tuo profilo.')}catch(e:any){setError(e.message)}finally{setBusy(false)}}
 return <section className="card stack"><h2>Colori e desktop</h2><p className="muted">Le tue preferenze ti seguono sui dispositivi con cui accedi a FamilOS.</p>
 <div className="desktop-theme-cards" role="group" aria-label="Stile desktop">{DESKTOP_STYLES.map(style=><button type="button" className="desktop-theme-card" key={style.id} aria-pressed={value.desktop_style===style.id} onClick={()=>{setValue({...value,desktop_style:style.id,wallpaper:style.id,accent:style.accent});setMessage('')}}><img src={`/wallpapers/${style.id}.png`} alt=""/><strong>{style.name} {value.desktop_style===style.id?'✓':''}</strong><small>{style.description}</small></button>)}</div>
 <p className="muted">Scegli uno stile, poi personalizza liberamente sfondo, colore ed effetti. Premi Salva tema per applicarlo.</p>
 <label>Effetti visivi<select value={value.effects} onChange={e=>setValue({...value,effects:e.target.value as Appearance['effects']})}><option value="full">Completi: vetro, ombre e animazioni</option><option value="reduced">Ridotti: vetro senza animazioni</option><option value="off">Essenziali: superfici opache</option></select></label>
 <label>Tema<select value={value.theme} onChange={e=>setValue({...value,theme:e.target.value as Appearance['theme']})}><option value="system">Segui il dispositivo</option><option value="light">Chiaro</option><option value="dark">Scuro</option></select></label>
 <fieldset className="theme-swatches"><legend>Colore principale</legend>{Object.entries({lavender:['Lavanda','#8064a1'],blue:['Oceano','#3576b5'],green:['Salvia','#348571'],rose:['Rosa','#b65d86'],amber:['Ambra','#a97927']}).map(([key,[label,color]])=><button type="button" key={key} title={label} aria-label={label} aria-pressed={value.accent===key} style={{background:color}} onClick={()=>setValue({...value,accent:key as Appearance['accent']})}>{value.accent===key?'✓':''}</button>)}</fieldset>
 <label>Sfondo<select value={value.wallpaper} onChange={e=>setValue({...value,wallpaper:e.target.value as Appearance['wallpaper']})}>{DESKTOP_STYLES.map(style=><option key={style.id} value={style.id}>{style.name}</option>)}<option value="soft">Nuvole (sfumatura)</option><option value="ocean">Oceano (sfumatura)</option><option value="forest">Bosco</option><option value="sunset">Tramonto (sfumatura)</option><option value="midnight">Notte</option>{personalWallpaper&&<option value="custom">La mia immagine</option>}</select></label>
 <label>Immagine di sfondo personale<input type="file" accept="image/png,image/jpeg,image/webp" onChange={e=>setWallpaper(e.target.files?.[0]||null)}/></label>
 <small className="muted">PNG, JPEG o WebP, fino a 8 MB. Verrà usata sul desktop e sulla schermata di blocco.</small>
 <button className="ghost" disabled={busy||!wallpaper} onClick={async()=>{setBusy(true);setError('');try{const data=new FormData();data.set('image',wallpaper!);await api('/branding/profile/wallpaper',{method:'POST',body:data});const next={...value,wallpaper:'custom' as const};await put('/auth/prefs',{appearance:next});setValue(next);await refreshBrand();await refresh();setWallpaper(null);setMessage('Sfondo personale aggiornato.')}catch(e:any){setError(e.message)}finally{setBusy(false)}}}>Carica e usa questo sfondo</button>
 <label>Blocco automatico<select value={value.lock_after_minutes} onChange={e=>setValue({...value,lock_after_minutes:Number(e.target.value) as Appearance['lock_after_minutes']})}><option value={0}>Solo manuale</option><option value={1}>Dopo 1 minuto</option><option value={5}>Dopo 5 minuti</option><option value={15}>Dopo 15 minuti</option><option value={30}>Dopo 30 minuti</option></select></label>
 {error&&<p className="error" role="alert">{error}</p>}{message&&<p role="status">{message}</p>}
 <button disabled={busy} onClick={save}>Salva tema</button></section>
}
