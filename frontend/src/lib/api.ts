// Client HTTP verso il backend. Cookie di sessione + header anti-CSRF.
export class ApiError extends Error {
  status: number
  mfa: boolean
  constructor(status: number, message: string, mfa = false) {
    super(message)
    this.status = status
    this.mfa = mfa
  }
}

export const HEADERS = { 'X-Requested-With': 'FamilAI' }

export async function api<T = any>(path: string, init: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = { ...HEADERS, ...(init.headers as any) }
  if (init.body && !(init.body instanceof FormData)) headers['Content-Type'] = 'application/json'
  const r = await fetch(`/api${path}`, { credentials: 'include', ...init, headers })
  if (!r.ok) {
    if (r.status === 423) window.dispatchEvent(new Event("familos:locked"))
    let msg = r.statusText
    try {
      const j = await r.json()
      msg = typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail ?? j)
    } catch {}
    throw new ApiError(r.status, msg, r.headers.get('X-MFA') === 'required')
  }
  if (r.status === 204) return undefined as T
  return r.json()
}

export const get = <T = any>(p: string) => api<T>(p)
export const post = <T = any>(p: string, body?: any) =>
  api<T>(p, { method: 'POST', body: body instanceof FormData ? body : JSON.stringify(body ?? {}) })
export const patch = <T = any>(p: string, body: any) => api<T>(p, { method: 'PATCH', body: JSON.stringify(body) })
export const put = <T = any>(p: string, body: any) => api<T>(p, { method: 'PUT', body: JSON.stringify(body) })
export const del = <T = any>(p: string) => api<T>(p, { method: 'DELETE' })

/** Invia un messaggio e legge lo stream SSE di eventi. */
export async function streamMessage(
  conversationId: string,
  body: any,
  onEvent: (ev: any) => void,
  signal?: AbortSignal,
  action: 'messages' | 'regenerate' = 'messages',
) {
  const r = await fetch(`/api/chat/conversations/${conversationId}/${action}`, {
    method: 'POST',
    credentials: 'include',
    headers: { ...HEADERS, 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal,
  })
  if (!r.ok || !r.body) {
    let msg = r.statusText
    try {
      msg = (await r.json()).detail ?? msg
    } catch {}
    throw new ApiError(r.status, msg)
  }
  const reader = r.body.getReader()
  const dec = new TextDecoder()
  let buf = ''
  while (true) {
    const { value, done } = await reader.read()
    if (done) break
    buf += dec.decode(value, { stream: true })
    let idx
    while ((idx = buf.indexOf('\n\n')) >= 0) {
      const raw = buf.slice(0, idx)
      buf = buf.slice(idx + 2)
      for (const line of raw.split('\n')) {
        if (line.startsWith('data: ')) {
          try {
            onEvent(JSON.parse(line.slice(6)))
          } catch {}
        }
      }
    }
  }
}

// ---- tipi ----
export interface User {
  id: string
  username: string
  display_name: string
  avatar: string
  email: string | null
  role: 'admin' | 'adult' | 'child'
  age_group: string
  birth_year: number | null
  is_active: boolean
  has_passkey: boolean
  has_totp: boolean
  mfa_choice?: string
  last_login?: string | null
  last_logout?: string | null
  active_sessions?: number
  departments?: string[]
  apps?: string[] | null
  admin_apps?: string[]
  role_label?: string
  quota_bytes?: number | null
  quota_effective?: number
  storage_used?: number
  policy: Record<string, any>
  effective_policy: Record<string, any>
}
export interface Me {
  session_locked?: boolean
  user: User
  family_name: string
  model_lock?: string
  prefs?: { tools_off?: string[]; appearance?: import('./appearance').Appearance }
  mfa_verified: boolean
  mfa_required: boolean
  providers: Provider[]
  tools: string[]
  features?: { wordcraft_web?: boolean; wordcraft_mcp?: boolean; gridcraft_web?: boolean; gridcraft_mcp?: boolean; pdfcraft_web?: boolean; pdfcraft_mcp?: boolean; deckcraft_web?: boolean; deckcraft_mcp?: boolean; ideacraft_mcp?: boolean; ideacraft_web?: boolean; mailcraft_web?: boolean; mailcraft_mcp?: boolean; messaggi?: boolean; archivio?: boolean; apps?: string[]; admin_apps?: string[]; org_mode?: boolean; passkeys?: boolean; mfa_optional?: boolean; debug_llm?: boolean }
}
export interface Provider {
  id: string
  label: string
  models: { id: string; label: string }[]
  default_model: string
}
export interface Conversation {
  id: string
  title: string
  provider: string
  model: string
  project_id: string | null
  owner_id: string
  updated_at: string
  shared?: boolean
  owner_name?: string | null
}
export interface Message {
  id: string
  role: 'user' | 'assistant' | 'tool'
  author_id: string | null
  content: any[]
  created_at: string
  flagged: boolean
  usage: Record<string, any>
  thinking?: string
  streaming?: boolean
}
export interface Project {
  id: string
  name: string
  description: string
  instructions: string
  icon: string
  owner_id: string
  shared?: boolean
  owner_name?: string | null
}
export interface FileOut {
  id: string
  filename: string
  mime: string
  size: number
  kind: string
  url: string
  owner_id: string
  project_id: string | null
  conversation_id: string | null
  created_at: string
  index_status: 'none' | 'pending' | 'indexed' | 'failed'
  chunk_count: number
  folder_id?: string | null
  access?: 'owner' | 'write' | 'read' | 'parent'
  owner_name?: string
  sha256?: string | null
}

/** Dimensioni leggibili (1.234 → "1,2 KB"). */
export function fmtBytes(n: number): string {
  if (!n || n < 0) return '0 B'
  const u = ['B', 'KB', 'MB', 'GB', 'TB']; let i = 0; let v = n
  while (v >= 1024 && i < u.length - 1) { v /= 1024; i++ }
  return `${i < 2 ? Math.round(v) : v.toFixed(1)} ${u[i]}`
}
