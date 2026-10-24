import { get, post } from './api'

/** Notifiche push sul telefono (Web Push). Su iPhone funzionano solo con Emily aggiunta alla schermata Home (iOS 16.4+). */
export type PushState = 'unsupported' | 'needs-install' | 'denied' | 'off' | 'on'

export function isIOS(): boolean {
  return /iPad|iPhone|iPod/.test(navigator.userAgent) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1)
}

export function isStandalone(): boolean {
  return window.matchMedia?.('(display-mode: standalone)').matches || (navigator as any).standalone === true
}

function b64ToBytes(b64: string): Uint8Array {
  const s = atob((b64 + '='.repeat((4 - (b64.length % 4)) % 4)).replace(/-/g, '+').replace(/_/g, '/'))
  return Uint8Array.from(s, (c) => c.charCodeAt(0))
}

async function registration(): Promise<ServiceWorkerRegistration | null> {
  if (!('serviceWorker' in navigator)) return null
  return (await navigator.serviceWorker.getRegistration('/')) || (await navigator.serviceWorker.register('/sw.js', { scope: '/' }).catch(() => null))
}

export async function pushState(): Promise<PushState> {
  if (!('serviceWorker' in navigator) || !window.isSecureContext) return 'unsupported'
  if (!('PushManager' in window) || !('Notification' in window)) return isIOS() && !isStandalone() ? 'needs-install' : 'unsupported'
  if (Notification.permission === 'denied') return 'denied'
  const reg = await registration()
  const sub = await reg?.pushManager.getSubscription()
  return sub ? 'on' : 'off'
}

function deviceName(): string {
  const ua = navigator.userAgent
  if (/iPhone/.test(ua)) return 'iPhone'
  if (/iPad/.test(ua) || isIOS()) return 'iPad'
  if (/Android/.test(ua)) return 'Android'
  if (/Mac/.test(ua)) return 'Mac'
  if (/Windows/.test(ua)) return 'Windows'
  return 'Browser'
}

/** Chiede il permesso (va chiamata da un tocco dell'utente) e registra il dispositivo. */
export async function enablePush(): Promise<void> {
  const perm = await Notification.requestPermission()
  if (perm !== 'granted') throw new Error('Permesso negato: riattivalo dalle impostazioni del telefono (Notifiche › Emily)')
  const reg = await registration()
  if (!reg) throw new Error('Il browser non permette le notifiche push')
  const { public_key } = await get<{ public_key: string }>('/push/key')
  let sub = await reg.pushManager.getSubscription()
  if (!sub) sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: b64ToBytes(public_key) as BufferSource })
  await post('/push/subscribe', { subscription: sub.toJSON(), device: deviceName() })
}

export async function disablePush(): Promise<void> {
  const reg = await registration()
  const sub = await reg?.pushManager.getSubscription()
  if (sub) {
    await post('/push/unsubscribe', { endpoint: sub.endpoint }).catch(() => {})
    await sub.unsubscribe().catch(() => {})
  }
}

export async function testPush(): Promise<void> {
  await post('/push/test')
}
