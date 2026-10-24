import { useEffect, useMemo, useRef, useState } from 'react'
import Markdown from './Markdown'
import { get, patch } from '../lib/api'
import { fetchSpeech } from '../lib/speech'

export interface ArtifactData {
  id: string
  title: string
  kind: 'html' | 'svg' | 'markdown' | 'code' | 'mermaid'
  language: string
  version: number
  content: string
  conversation_id: string
}

const CAN_PREVIEW = new Set(['html', 'svg', 'markdown', 'mermaid'])

/** HTML della pagina di anteprima: iframe sandbox senza same-origin, quindi senza cookie né accesso alle API. */
/** Script iniettato negli artifact HTML: espone FamilAI.speak() che chiede l'audio alla pagina madre via postMessage
 *  (l'iframe e' sandboxato senza cookie: non puo' chiamare le API da solo). */
const BRIDGE = `<script>(function(){
  if (window.FamilAI) return;
  var seq = 0, pending = {}, current = null;
  function stop(){ if (current) { try { current.pause(); } catch(e){} current = null; } }
  window.addEventListener('message', function(ev){
    var d = ev.data || {};
    if (d.type !== 'familai:speak:result' || !pending[d.id]) return;
    var p = pending[d.id]; delete pending[d.id];
    if (d.error) { p.reject(new Error(d.error)); return; }
    try {
      var url = URL.createObjectURL(new Blob([d.audio], { type: d.mime || 'audio/mpeg' }));
      stop();
      var a = new Audio(url); current = a;
      a.onended = function(){ if (current === a) current = null; URL.revokeObjectURL(url); p.resolve(); };
      a.onerror = function(){ URL.revokeObjectURL(url); p.reject(new Error('audio')); };
      a.play().catch(p.reject);
    } catch (e) { p.reject(e); }
  });
  window.FamilAI = {
    speak: function(text, opts){
      text = String(text == null ? '' : text).trim();
      if (!text) return Promise.resolve();
      if (window.parent === window) {
        return new Promise(function(res){ try { var u = new SpeechSynthesisUtterance(text); u.lang = 'it-IT'; u.onend = function(){ res(); }; speechSynthesis.speak(u); } catch(e) { res(); } });
      }
      var id = ++seq;
      return new Promise(function(resolve, reject){
        pending[id] = { resolve: resolve, reject: reject };
        window.parent.postMessage({ type: 'familai:speak', id: id, text: text, opts: opts || {} }, '*');
      });
    },
    stopSpeaking: function(){ stop(); try { speechSynthesis.cancel(); } catch(e){} },
  };
})();</script>`

function injectBridge(html: string): string {
  const i = html.search(/<head[^>]*>/i)
  if (i >= 0) { const end = html.indexOf('>', i) + 1; return html.slice(0, end) + BRIDGE + html.slice(end) }
  const j = html.search(/<html[^>]*>/i)
  if (j >= 0) { const end = html.indexOf('>', j) + 1; return html.slice(0, end) + BRIDGE + html.slice(end) }
  return BRIDGE + html
}

function previewDoc(a: ArtifactData): string {
  if (a.kind === 'html') return injectBridge(a.content)
  if (a.kind === 'svg') return `<!doctype html><html><body style="margin:0;display:grid;place-items:center;min-height:100vh;background:#fff">${a.content}</body></html>`
  if (a.kind === 'mermaid')
    return `<!doctype html><html><body style="margin:16px;background:#fff"><pre class="mermaid">${a.content.replace(/</g, '&lt;')}</pre>
<script src="https://cdnjs.cloudflare.com/ajax/libs/mermaid/11.4.1/mermaid.min.js"></script><script>mermaid.initialize({startOnLoad:true});</script></body></html>`
  return ''
}

export default function ArtifactPanel({ artifact, all = [], canEdit, onClose, onChange, onSelect, onDelete }: {
  artifact: ArtifactData; all?: ArtifactData[]; canEdit: boolean; onClose: () => void; onChange: (a: ArtifactData) => void
  onSelect?: (a: ArtifactData) => void; onDelete?: (a: ArtifactData) => void | Promise<void>
}) {
  const [tab, setTab] = useState<'preview' | 'code'>(CAN_PREVIEW.has(artifact.kind) ? 'preview' : 'code')
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(artifact.content)
  const [versions, setVersions] = useState<number[]>([])
  const [shown, setShown] = useState<ArtifactData>(artifact)
  const [reloadKey, setReloadKey] = useState(0)

  useEffect(() => {
    setShown(artifact)
    setDraft(artifact.content)
    setEditing(false)
    if (!CAN_PREVIEW.has(artifact.kind)) setTab('code')
    get(`/artifacts/${artifact.id}`).then((a) => setVersions([...(a.history || []).map((h: any) => h.version), a.version]))
  }, [artifact.id, artifact.version])

  const doc = useMemo(() => previewDoc(shown), [shown, reloadKey])
  const frame = useRef<HTMLIFrameElement>(null)

  // ponte voce: l'artifact chiede l'audio, il pannello lo recupera dal backend e lo rimanda nell'iframe
  useEffect(() => {
    const onMsg = async (ev: MessageEvent) => {
      const d = ev.data || {}
      if (d.type !== 'familai:speak' || !frame.current || ev.source !== frame.current.contentWindow) return
      const reply = (payload: any, transfer: Transferable[] = []) => frame.current?.contentWindow?.postMessage({ type: 'familai:speak:result', id: d.id, ...payload }, '*', transfer)
      try {
        const { blob } = await fetchSpeech(String(d.text).slice(0, 2000), { voice: d.opts?.voice, style: d.opts?.style })
        const buf = await blob.arrayBuffer()
        reply({ audio: buf, mime: blob.type }, [buf])
      } catch (e: any) {
        reply({ error: e.message || 'Sintesi vocale non disponibile' })
      }
    }
    window.addEventListener('message', onMsg)
    return () => window.removeEventListener('message', onMsg)
  }, [])

  async function save() {
    const a = await patch<ArtifactData>(`/artifacts/${artifact.id}`, { content: draft })
    onChange(a)
    setEditing(false)
  }
  async function showVersion(v: number) {
    setShown(await get(`/artifacts/${artifact.id}/versions/${v}`))
  }
  function download() {
    const ext = { html: 'html', svg: 'svg', markdown: 'md', mermaid: 'mmd' }[shown.kind as string] ??
      ({ python: 'py', javascript: 'js', typescript: 'ts', java: 'java', bash: 'sh' } as any)[shown.language] ?? 'txt'
    const blob = new Blob([shown.content], { type: 'text/plain' })
    const u = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = u
    a.download = `${shown.title.replace(/[^\w.-]+/g, '_')}.${ext}`
    a.click()
    URL.revokeObjectURL(u)
  }

  return (
    <aside className="artifact-panel">
      <header className="artifact-header">
        {all.length > 1 && onSelect ? (
          <select className="title-select" value={artifact.id} title="Scegli quale artifact vedere"
            onChange={(e) => { const a = all.find((x) => x.id === e.target.value); if (a) onSelect(a) }}>
            {all.map((a, i) => <option key={a.id} value={a.id}>📦 {i + 1}. {a.title}</option>)}
          </select>
        ) : (
          <div className="title" title={shown.title}>📦 {shown.title}</div>
        )}
        <div className="row">
          {versions.length > 1 && (
            <select value={shown.version} onChange={(e) => showVersion(Number(e.target.value))} title="Versione">
              {versions.map((v) => <option key={v} value={v}>v{v}</option>)}
            </select>
          )}
          {CAN_PREVIEW.has(shown.kind) && (
            <div className="tabs small">
              <button className={tab === 'preview' ? 'active' : ''} onClick={() => setTab('preview')}>Anteprima</button>
              <button className={tab === 'code' ? 'active' : ''} onClick={() => setTab('code')}>Codice</button>
            </div>
          )}
          {tab === 'preview' && <button className="icon" title="Ricarica" onClick={() => setReloadKey((k) => k + 1)}>↻</button>}
          <button className="icon" title="Copia" onClick={() => navigator.clipboard.writeText(shown.content)}>📋</button>
          <button className="icon" title="Scarica" onClick={download}>⬇️</button>
          {shown.kind === 'html' && <a className="icon" title="Apri in nuova scheda" href={`/api/artifacts/${shown.id}/raw`} target="_blank" rel="noreferrer">↗</a>}
          {canEdit && onDelete && <button className="icon danger" title="Elimina definitivamente questo artifact" onClick={() => { if (confirm(`Eliminare definitivamente "${artifact.title}" con tutte le versioni?`)) onDelete(artifact) }}>🗑</button>}
          <button className="icon" onClick={onClose} title="Chiudi">✕</button>
        </div>
      </header>

      <div className="artifact-body">
        {tab === 'preview' && shown.kind === 'markdown' && (
          <Markdown className="markdown pad">{shown.content}</Markdown>
        )}
        {tab === 'preview' && shown.kind !== 'markdown' && (
          <iframe
            ref={frame}
            key={`${shown.id}-${shown.version}-${reloadKey}`}
            className="artifact-frame"
            sandbox="allow-scripts allow-modals allow-forms allow-popups allow-pointer-lock"
            referrerPolicy="no-referrer"
            srcDoc={doc}
            title={shown.title}
          />
        )}
        {tab === 'code' && (
          editing ? (
            <textarea className="code-editor" value={draft} onChange={(e) => setDraft(e.target.value)} spellCheck={false} />
          ) : (
            <pre className="code-view"><code>{shown.content}</code></pre>
          )
        )}
      </div>

      {tab === 'code' && canEdit && shown.version === artifact.version && (
        <footer className="artifact-footer row">
          {editing ? (
            <>
              <button onClick={save}>Salva come nuova versione</button>
              <button className="ghost" onClick={() => { setEditing(false); setDraft(artifact.content) }}>Annulla</button>
            </>
          ) : (
            <button className="ghost" onClick={() => setEditing(true)}>✏️ Modifica</button>
          )}
          <small className="muted">{shown.kind}{shown.language ? ` · ${shown.language}` : ''} · v{shown.version}</small>
        </footer>
      )}
    </aside>
  )
}
