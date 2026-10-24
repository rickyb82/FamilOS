import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import { useBranding, type InstallationBrand } from '../lib/branding'

export default function AdminBranding() {
  const { installationBrand: brand, refresh } = useBranding()
  const [name, setName] = useState(brand.name)
  const [logo, setLogo] = useState<File | null>(null)
  const [preview, setPreview] = useState('')
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  const [fileKey, setFileKey] = useState(0)
  useEffect(() => { setName(brand.name) }, [brand.name])
  useEffect(() => {
    if (!logo) { setPreview(''); return }
    const url = URL.createObjectURL(logo)
    setPreview(url)
    return () => URL.revokeObjectURL(url)
  }, [logo])
  async function save(reset = false) {
    setBusy(true); setError(''); setMessage('')
    const data = new FormData()
    data.set('name', name.trim() || 'FamilOS')
    if (logo) data.set('logo', logo)
    if (reset) data.set('reset', 'true')
    try {
      const value = await api<InstallationBrand>('/admin/branding', { method: 'POST', body: data })
      setName(value.name); setLogo(null); setFileKey(k => k + 1)
      await refresh()
      setMessage(reset ? 'Identità FamilOS ripristinata.' : 'Identità aggiornata per tutta l’installazione.')
    } catch (e: any) { setError(e.message) }
    finally { setBusy(false) }
  }
  return <section className="card stack installation-branding">
    <h3>Nome e logo dell’installazione</h3>
    <p className="muted">Personalizza il tuo spazio. Nome e logo compaiono sul desktop, nella pagina di accesso e nell’icona della web app; sono visibili anche prima del login.</p>
    <div className="installation-brand-preview"><img src={preview || brand.logo_url} alt="Anteprima logo" /><b>{name || brand.name}</b></div>
    <label>Nome del sistema<input maxLength={60} value={name} onChange={e => setName(e.target.value)} /></label>
    <label>Logo personalizzato<input key={fileKey} type="file" accept="image/png,image/jpeg,image/webp" onChange={e => setLogo(e.target.files?.[0] || null)} /></label>
    <small className="muted">PNG, JPEG o WebP, massimo 2 MB e 4096 × 4096 pixel. Consigliata un’immagine quadrata. Le icone PWA già installate possono aggiornarsi alla successiva installazione.</small>
    {error && <p className="error" role="alert">{error}</p>}
    {message && <p role="status">{message}</p>}
    <div className="row"><button disabled={busy || !name.trim()} onClick={() => save()}>Salva identità</button><button className="ghost" disabled={busy} onClick={() => save(true)}>Ripristina FamilOS</button></div>
  </section>
}
