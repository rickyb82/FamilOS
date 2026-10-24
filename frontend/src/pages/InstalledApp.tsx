import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { post } from '../lib/api'

export default function InstalledApp() {
  const { appId } = useParams()
  const [app, setApp] = useState<{name:string; url:string} | null>(null)
  const [error, setError] = useState('')
  const [attempt, setAttempt] = useState(0)
  useEffect(() => {
    let active = true
    setApp(null); setError('')
    post<{name:string; url:string}>(`/installed-apps/${appId}/launch`, {}).then(value => { if (active) setApp(value) }).catch(e => { if (active) setError(e.message) })
    return () => { active = false }
  }, [appId, attempt])
  return <section style={{height:'100%',display:'flex',flexDirection:'column'}}>
    <div className="row"><b>{app?.name || 'Applicazione'}</b><button className="ghost" onClick={() => { if (confirm('Riaprire l’app? Salva prima il lavoro.')) setAttempt(x=>x+1) }}>Riapri sessione</button></div>
    {error && <p role="alert">{error}</p>}
    {!app && !error && <p>Apertura applicazione…</p>}
    {app && <iframe title={app.name} src={app.url} sandbox="allow-scripts allow-forms allow-downloads" referrerPolicy="no-referrer" style={{flex:1,minHeight:400,width:'100%',border:0}} />}
  </section>
}
