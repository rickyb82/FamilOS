import { post } from './api'
import { subscribeLive, type LiveEvent } from './events'

/**
 * Chiamate e videochiamate di Messaggi (WebRTC "mesh": ogni browser si collega direttamente agli altri, fino a 5).
 * Segnalazione: POST /api/msg/rooms/{id}/call/* e gli eventi "call" del flusso SSE. Chi entra in una chiamata in corso
 * manda l'offerta a chi c'e' gia' (cosi' due browser non si offrono a vicenda). Stato osservabile con subscribe().
 */
export interface Peer { id: string; stream: MediaStream | null; state: string }
export interface Incoming { roomId: string; video: boolean; fromName: string; roomName: string }
export interface CallState {
  roomId: string | null; video: boolean; roomName: string; status: 'idle' | 'starting' | 'calling' | 'active'
  local: MediaStream | null; peers: Peer[]; muted: boolean; camOff: boolean; sharing: boolean; error: string; notice?: string; startedAt: number
  incoming: Incoming | null
}

type Listener = (s: CallState) => void
const idle: CallState = { roomId: null, video: true, roomName: '', status: 'idle', local: null, peers: [], muted: false, camOff: false, sharing: false, error: '', startedAt: 0, incoming: null }

class CallManager {
  state: CallState = { ...idle }
  private listeners = new Set<Listener>()
  private pcs = new Map<string, RTCPeerConnection>()
  private streams = new Map<string, MediaStream>()
  private pendingIce = new Map<string, RTCIceCandidateInit[]>()
  private ice: RTCIceServer[] = []
  private camTrack: MediaStreamTrack | null = null
  private unsub: (() => void) | null = null
  private ringTimer: number | null = null
  me = ''

  start(meId: string) {
    this.me = meId
    if (!this.unsub) this.unsub = subscribeLive((e) => this.onEvent(e))
    window.addEventListener('pagehide', () => { if (this.state.roomId) this.leaveBeacon(this.state.roomId) })
  }
  clearError() { this.set({ error: '' }) }
  subscribe(l: Listener) { this.listeners.add(l); l(this.state); return () => { this.listeners.delete(l) } }
  private set(p: Partial<CallState>) { this.state = { ...this.state, ...p }; this.listeners.forEach((l) => l(this.state)) }
  private peersOut(): Peer[] { return [...this.pcs.keys()].map((id) => ({ id, stream: this.streams.get(id) || null, state: this.pcs.get(id)?.connectionState || 'new' })) }

  /** Avvia una chiamata nella conversazione o entra in quella in corso. */
  async join(roomId: string, video: boolean, roomName = '') {
    if (this.state.roomId && this.state.roomId !== roomId) await this.hangup()
    this.set({ roomId, video, roomName, status: 'starting', error: '', incoming: null })
    let local: MediaStream
    try {
      local = await this.media(video)
    } catch (e: any) {
      this.set({ ...idle, error: e.message })
      return
    }
    this.camTrack = local.getVideoTracks()[0] || null
    this.set({ local, muted: false, camOff: false, sharing: false })
    try {
      const r = await post<{ peers: string[]; iceServers: RTCIceServer[]; video: boolean }>(`/msg/rooms/${roomId}/call/join`, { video })
      this.ice = r.iceServers || []
      this.set({ status: r.peers.length ? 'active' : 'calling', startedAt: Date.now(), video: r.video })
      for (const pid of r.peers) await this.offer(pid)
      if (!r.peers.length) this.ringTimer = window.setTimeout(() => { if (this.state.status === 'calling') { this.set({ error: 'Nessuna risposta' }); this.hangup() } }, 45000)
    } catch (e: any) {
      local.getTracks().forEach((t) => t.stop())
      this.set({ ...idle, error: e.message })
    }
  }

  /** Microfono (e videocamera): se la videocamera manca o e' occupata si entra solo con l'audio. Errori spiegati. */
  private async media(video: boolean): Promise<MediaStream> {
    if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) throw new Error('Le chiamate funzionano solo con Emily aperta in HTTPS.')
    const audio = { echoCancellation: true, noiseSuppression: true }
    try {
      return await navigator.mediaDevices.getUserMedia({ audio, video: video ? { width: { ideal: 1280 }, height: { ideal: 720 } } : false })
    } catch (e: any) {
      if (video && ['NotFoundError', 'NotReadableError', 'OverconstrainedError', 'AbortError'].includes(e?.name)) {
        try {
          const s = await navigator.mediaDevices.getUserMedia({ audio })
          this.set({ notice: e?.name === 'NotFoundError' ? 'Nessuna videocamera: sei in chiamata solo con l\'audio' : 'Videocamera occupata da un\'altra app: sei in chiamata solo con l\'audio' })
          return s
        } catch (e2: any) { throw new Error(explainMedia(e2, false)) }
      }
      throw new Error(explainMedia(e, video))
    }
  }

  async accept() { const inc = this.state.incoming; if (inc) { stopRing(); await this.join(inc.roomId, inc.video, inc.roomName) } }
  async decline() {
    const inc = this.state.incoming
    stopRing(); this.set({ incoming: null })
    if (inc) try { await post(`/msg/rooms/${inc.roomId}/call/decline`) } catch {}
  }

  async hangup() {
    const roomId = this.state.roomId
    if (this.ringTimer) { clearTimeout(this.ringTimer); this.ringTimer = null }
    this.pcs.forEach((pc) => pc.close()); this.pcs.clear(); this.streams.clear(); this.pendingIce.clear()
    this.state.local?.getTracks().forEach((t) => t.stop())
    this.camTrack?.stop(); this.camTrack = null
    const err = this.state.error
    this.set({ ...idle, error: err, incoming: this.state.incoming, notice: '' })
    if (roomId) try { await post(`/msg/rooms/${roomId}/call/leave`) } catch {}
  }
  private leaveBeacon(roomId: string) {
    try { fetch(`/api/msg/rooms/${roomId}/call/leave`, { method: 'POST', keepalive: true, credentials: 'include', headers: { 'X-Requested-With': 'FamilAI', 'Content-Type': 'application/json' }, body: '{}' }) } catch {}
  }

  toggleMute() { const on = !this.state.muted; this.state.local?.getAudioTracks().forEach((t) => (t.enabled = !on)); this.set({ muted: on }) }
  toggleCam() { const off = !this.state.camOff; this.state.local?.getVideoTracks().forEach((t) => (t.enabled = !off)); this.set({ camOff: off }) }
  async toggleShare() {
    if (!this.state.local) return
    if (this.state.sharing) { await this.replaceVideo(this.camTrack); this.set({ sharing: false }); return }
    try {
      const scr = await (navigator.mediaDevices as any).getDisplayMedia({ video: true })
      const track: MediaStreamTrack = scr.getVideoTracks()[0]
      track.onended = () => { if (this.state.sharing) { this.replaceVideo(this.camTrack); this.set({ sharing: false }) } }
      await this.replaceVideo(track)
      this.set({ sharing: true })
    } catch {}
  }
  private async replaceVideo(track: MediaStreamTrack | null) {
    if (!track) return
    for (const pc of this.pcs.values()) {
      const s = pc.getSenders().find((x) => x.track?.kind === 'video')
      if (s) await s.replaceTrack(track)
    }
    const local = new MediaStream([...(this.state.local?.getAudioTracks() || []), track])
    this.set({ local })
  }

  private pc(peerId: string): RTCPeerConnection {
    let pc = this.pcs.get(peerId)
    if (pc) return pc
    pc = new RTCPeerConnection({ iceServers: this.ice })
    this.pcs.set(peerId, pc)
    this.state.local?.getTracks().forEach((t) => pc!.addTrack(t, this.state.local!))
    pc.onicecandidate = (e) => { if (e.candidate) this.signal(peerId, { ice: e.candidate.toJSON() }) }
    pc.ontrack = (e) => { this.streams.set(peerId, e.streams[0] || new MediaStream([e.track])); this.set({ peers: this.peersOut(), status: 'active' }) }
    pc.onconnectionstatechange = () => {
      if (pc!.connectionState === 'failed' || pc!.connectionState === 'closed') this.dropPeer(peerId)
      else this.set({ peers: this.peersOut() })
    }
    this.set({ peers: this.peersOut() })
    return pc
  }
  private dropPeer(peerId: string) {
    this.pcs.get(peerId)?.close(); this.pcs.delete(peerId); this.streams.delete(peerId)
    this.set({ peers: this.peersOut() })
  }
  private async offer(peerId: string) {
    const pc = this.pc(peerId)
    const o = await pc.createOffer()
    await pc.setLocalDescription(o)
    this.signal(peerId, { sdp: pc.localDescription })
  }
  private signal(to: string, data: any) {
    const roomId = this.state.roomId
    if (roomId) post(`/msg/rooms/${roomId}/call/signal`, { to, data }).catch(() => {})
  }

  private async onEvent(e: LiveEvent) {
    if (e.kind !== 'call') return
    const mine = e.room_id === this.state.roomId
    switch (e.type) {
      case 'ring':
        if (this.state.roomId) return   // gia' in chiamata: niente squillo sopra
        this.set({ incoming: { roomId: e.room_id, video: !!e.video, fromName: e.from_name, roomName: e.room_name || '' } })
        startRing()
        window.setTimeout(() => { if (this.state.incoming?.roomId === e.room_id) { stopRing(); this.set({ incoming: null }) } }, 45000)
        break
      case 'joined':
        if (mine) { if (this.ringTimer) { clearTimeout(this.ringTimer); this.ringTimer = null } this.set({ status: 'active' }) }
        break
      case 'left':
        if (mine) this.dropPeer(e.user_id)
        break
      case 'declined':
        if (mine && this.pcs.size === 0) { this.set({ error: `${e.name} ha rifiutato la chiamata` }); this.hangup() }
        break
      case 'ended':
        if (this.state.incoming?.roomId === e.room_id) { stopRing(); this.set({ incoming: null }) }
        if (mine) this.hangup()
        break
      case 'signal': {
        if (!mine) return
        const from = e.from as string, d = e.data || {}
        const pc = this.pc(from)
        try {
          if (d.sdp) {
            await pc.setRemoteDescription(d.sdp)
            for (const c of this.pendingIce.get(from) || []) await pc.addIceCandidate(c)
            this.pendingIce.delete(from)
            if (d.sdp.type === 'offer') {
              const a = await pc.createAnswer()
              await pc.setLocalDescription(a)
              this.signal(from, { sdp: pc.localDescription })
            }
          } else if (d.ice) {
            if (pc.remoteDescription) await pc.addIceCandidate(d.ice)
            else this.pendingIce.set(from, [...(this.pendingIce.get(from) || []), d.ice])
          }
        } catch {}
        break
      }
    }
  }
}

export const calls = new CallManager()

/** True se la pagina stessa vieta il dispositivo (Permissions-Policy del server), non l'utente. */
function policyBlocks(video: boolean): boolean {
  try {
    const pol = (document as any).permissionsPolicy || (document as any).featurePolicy
    if (!pol?.allowsFeature) return false
    return !pol.allowsFeature('microphone') || (video && !pol.allowsFeature('camera'))
  } catch { return false }
}

function explainMedia(e: any, video: boolean): string {
  const what = video ? 'microfono e videocamera' : 'il microfono'
  switch (e?.name) {
    case 'NotAllowedError':
    case 'SecurityError':
      if (policyBlocks(video)) return `È il server di Emily a vietare ${what} (intestazione Permissions-Policy vecchia). Sul server: ./familai.sh update; poi ricarica Emily ignorando la cache (Ctrl+Shift+R, sul Mac ⌘+Shift+R).`
      return `Il browser non dà ${what} a Emily. Clicca il lucchetto 🔒 accanto all'indirizzo › Impostazioni sito (o Autorizzazioni) › consenti Fotocamera e Microfono, poi ricarica la pagina e riprova. Su Mac controlla anche Impostazioni di Sistema › Privacy › Fotocamera/Microfono per il browser.`
    case 'NotFoundError':
      return video ? 'Non trovo né microfono né videocamera su questo computer.' : 'Non trovo un microfono su questo computer.'
    case 'NotReadableError':
      return `${video ? 'Microfono o videocamera sono' : 'Il microfono è'} già in uso da un'altra app (Teams, Zoom, …): chiudila e riprova.`
    default:
      return `Non riesco ad accedere a ${what}${e?.message ? ` (${e.message})` : ''}.`
  }
}

// ------------------------------------------------------------------ suoneria (WebAudio, niente file audio)
let ringCtx: AudioContext | null = null
let ringInt: number | null = null
function startRing() {
  stopRing()
  try {
    ringCtx = new (window.AudioContext || (window as any).webkitAudioContext)()
    const beep = () => {
      if (!ringCtx) return
      const t = ringCtx.currentTime
      for (const [f, at] of [[880, 0], [660, 0.25]] as const) {
        const o = ringCtx.createOscillator(), g = ringCtx.createGain()
        o.frequency.value = f; g.gain.setValueAtTime(0.0001, t + at); g.gain.exponentialRampToValueAtTime(0.2, t + at + 0.02); g.gain.exponentialRampToValueAtTime(0.0001, t + at + 0.22)
        o.connect(g).connect(ringCtx.destination); o.start(t + at); o.stop(t + at + 0.24)
      }
    }
    beep(); ringInt = window.setInterval(beep, 1800)
  } catch {}
}
export function stopRing() {
  if (ringInt) { clearInterval(ringInt); ringInt = null }
  try { ringCtx?.close() } catch {}
  ringCtx = null
}
