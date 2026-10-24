import ThemeSettings from './ThemeSettings'
import { useState } from 'react'
import { api } from '../lib/api'
import { useBranding } from '../lib/branding'

export default function PersonalAppearance() {
  const { brand, personalLogo, refresh } = useBranding()
  const [file,setFile] = useState<File|null>(null)
  const [busy,setBusy] = useState(false)
  const [message,setMessage] = useState('')
  const [error,setError] = useState('')
  async function save(reset=false) {
    const data = new FormData()
    if (reset) data.set('reset','true')
    if (file) data.set('logo',file)
    setBusy(true);setError('');setMessage('')
    try {
      await api('/branding/profile',{method:'POST',body:data})
      setFile(null);await refresh()
      setMessage(reset ? 'Stai usando il logo dell’installazione.' : 'Il tuo logo personale è stato aggiornato.')
    } catch(e:any) { setError(e.message) } finally { setBusy(false) }
  }
  return <><ThemeSettings /><section className="card stack"><h2>Il tuo logo</h2>
    <p className="muted">Personalizza l’aspetto del tuo spazio. Gli altri membri mantengono il loro logo; la pagina di accesso usa quello dell’installazione.</p>
    <div className="installation-brand-preview"><img src={brand.logo_url} alt="Il tuo logo" /><b>{brand.name}</b></div>
    <label>Scegli un’immagine<input type="file" accept="image/png,image/jpeg,image/webp" onChange={e=>setFile(e.target.files?.[0]||null)} /></label>
    <small className="muted">PNG, JPEG o WebP, fino a 2 MB. Consigliata un’immagine quadrata.</small>
    {error && <p className="error" role="alert">{error}</p>}{message && <p role="status">{message}</p>}
    <div className="row"><button disabled={busy||!file} onClick={()=>save()}>Usa il mio logo</button><button className="ghost" disabled={busy||!personalLogo} onClick={()=>save(true)}>Usa il logo dell’installazione</button></div>
  </section></>
}
