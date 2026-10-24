import { useBranding } from '../lib/branding'
import { useState } from 'react'
import { api,put } from '../lib/api'
import { useAuth } from '../lib/auth'
import { DEFAULT_APPEARANCE,type Appearance } from '../lib/appearance'
export default function ThemeSettings(){
 const {me,refresh}=useAuth()
 const {refresh:refreshBrand,personalWallpaper}=useBranding()
 const [wallpaper,setWallpaper]=useState<File|null>(null)
 const [value,setValue]=useState<Appearance>({...DEFAULT_APPEARANCE,...me?.prefs?.appearance})
 const [busy,setBusy]=useState(false),[message,setMessage]=useState(''),[error,setError]=useState('')
 async function save(){setBusy(true);setError('');try{await put('/auth/prefs',{appearance:value});await refresh();setMessage('Tema salvato sul tuo profilo.')}catch(e:any){setError(e.message)}finally{setBusy(false)}}
 return <section className="card stack"><h2>Colori e desktop</h2><p className="muted">Le tue preferenze ti seguono sui dispositivi con cui accedi a FamilOS.</p>
 <label>Tema<select value={value.theme} onChange={e=>setValue({...value,theme:e.target.value as Appearance['theme']})}><option value="system">Segui il dispositivo</option><option value="light">Chiaro</option><option value="dark">Scuro</option></select></label>
 <fieldset className="theme-swatches"><legend>Colore principale</legend>{Object.entries({lavender:['Lavanda','#8064a1'],blue:['Oceano','#3576b5'],green:['Salvia','#348571'],rose:['Rosa','#b65d86'],amber:['Ambra','#a97927']}).map(([key,[label,color]])=><button type="button" key={key} title={label} aria-label={label} aria-pressed={value.accent===key} style={{background:color}} onClick={()=>setValue({...value,accent:key as Appearance['accent']})}>{value.accent===key?'✓':''}</button>)}</fieldset>
 <label>Sfondo<select value={value.wallpaper} onChange={e=>setValue({...value,wallpaper:e.target.value as Appearance['wallpaper']})}><option value="soft">Nuvole</option><option value="ocean">Oceano</option><option value="forest">Bosco</option><option value="sunset">Tramonto</option><option value="midnight">Notte</option>{personalWallpaper&&<option value="custom">La mia immagine</option>}</select></label>
 <label>Immagine di sfondo personale<input type="file" accept="image/png,image/jpeg,image/webp" onChange={e=>setWallpaper(e.target.files?.[0]||null)}/></label>
 <small className="muted">PNG, JPEG o WebP, fino a 8 MB. Verrà usata sul desktop e sulla schermata di blocco.</small>
 <button className="ghost" disabled={busy||!wallpaper} onClick={async()=>{setBusy(true);setError('');try{const data=new FormData();data.set('image',wallpaper!);await api('/branding/profile/wallpaper',{method:'POST',body:data});const next={...value,wallpaper:'custom' as const};await put('/auth/prefs',{appearance:next});setValue(next);await refreshBrand();await refresh();setWallpaper(null);setMessage('Sfondo personale aggiornato.')}catch(e:any){setError(e.message)}finally{setBusy(false)}}}>Carica e usa questo sfondo</button>
 <label>Blocco automatico<select value={value.lock_after_minutes} onChange={e=>setValue({...value,lock_after_minutes:Number(e.target.value) as Appearance['lock_after_minutes']})}><option value={0}>Solo manuale</option><option value={1}>Dopo 1 minuto</option><option value={5}>Dopo 5 minuti</option><option value={15}>Dopo 15 minuti</option><option value={30}>Dopo 30 minuti</option></select></label>
 {error&&<p className="error" role="alert">{error}</p>}{message&&<p role="status">{message}</p>}
 <button disabled={busy} onClick={save}>Salva tema</button></section>
}
