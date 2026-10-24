/**
 * Emily dentro l'app nativa (iPhone/Android, guscio Capacitor in modalità server.url: il sito è questo, l'app lo apre a tutto
 * schermo). Il guscio inietta `window.Capacitor` con i plugin già pronti (PushNotifications, App, SplashScreen): qui si usano
 * senza dipendenze npm, così la stessa build del frontend serve il browser, la PWA e l'app.
 * - notifiche: il token APNs/FCM del dispositivo va al backend (POST /push/native) che lo usa in services/native_push.py;
 * - un tocco su una notifica porta alla pagina indicata (data.url);
 * - un link di Emily aperto nell'app (universal/app link) porta alla stessa pagina.
 */
const cap = (): any => (window as any).Capacitor

export function isNativeApp(): boolean {
  try { return !!cap()?.isNativePlatform?.() } catch { return false }
}

export function nativePlatform(): 'ios' | 'android' | '' {
  if (!isNativeApp()) return ''
  const p = String(cap().getPlatform?.() || '')
  return p === 'ios' || p === 'android' ? p : ''
}

function plugin(name: string): any {
  return cap()?.Plugins?.[name]
}

function appVersion(): string {
  const m = navigator.userAgent.match(/EmilyApp\/([\w.-]+)/)
  return m ? m[1] : ''
}

export type NativePushState = 'off' | 'on' | 'denied'

export async function nativePushState(): Promise<NativePushState> {
  const pn = plugin('PushNotifications')
  if (!pn) return 'off'
  const { receive } = await pn.checkPermissions()
  if (receive === 'denied') return 'denied'
  return receive === 'granted' && localStorage.getItem('emily.native-push') === 'on' ? 'on' : 'off'
}

/** Chiede il permesso (da un tocco dell'utente), registra il dispositivo e manda il token al backend. */
export async function enableNativePush(): Promise<void> {
  const pn = plugin('PushNotifications')
  if (!pn) throw new Error("Questa versione dell'app non riceve notifiche")
  let { receive } = await pn.checkPermissions()
  if (receive === 'prompt' || receive === 'prompt-with-rationale') receive = (await pn.requestPermissions()).receive
  if (receive !== 'granted') throw new Error('Permesso negato: riattivalo dalle impostazioni del telefono (Notifiche › Emily)')
  const token = await new Promise<string>((resolve, reject) => {
    const t = setTimeout(() => reject(new Error('Il telefono non ha risposto: riprova tra poco')), 15000)
    pn.addListener('registration', (r: { value: string }) => { clearTimeout(t); resolve(r.value) })
    pn.addListener('registrationError', (e: any) => { clearTimeout(t); reject(new Error(e?.error || 'Registrazione non riuscita')) })
    pn.register()
  })
  const { post } = await import('./api')
  const r = await post<{ ok: boolean; enabled: boolean }>('/push/native', { platform: nativePlatform(), token, device: deviceLabel(), app_version: appVersion() })
  try { localStorage.setItem('emily.native-push', 'on'); localStorage.setItem('emily.native-token', token) } catch {}
  if (!r.enabled) throw new Error("Dispositivo registrato, ma il server non ha ancora le chiavi per le notifiche native (l'amministratore le imposta in .env)")
}

export async function disableNativePush(): Promise<void> {
  const pn = plugin('PushNotifications')
  let token = ''
  try { token = localStorage.getItem('emily.native-token') || ''; localStorage.removeItem('emily.native-push'); localStorage.removeItem('emily.native-token') } catch {}
  if (token) {
    const { post } = await import('./api')
    await post('/push/native/unregister', { token }).catch(() => {})
  }
  await pn?.removeAllDeliveredNotifications?.().catch?.(() => {})
}

function deviceLabel(): string {
  const p = nativePlatform()
  return p === 'ios' ? (/iPad/.test(navigator.userAgent) ? 'iPad (app)' : 'iPhone (app)') : 'Android (app)'
}

/** Collega gli eventi del guscio alla navigazione di Emily (una volta, all'avvio). */
export function wireNative(navigate: (to: string) => void): void {
  if (!isNativeApp()) return
  const toPath = (u: string): string => {
    try { const url = new URL(u, location.origin); return url.origin === location.origin ? url.pathname + url.search + url.hash : '' } catch { return u.startsWith('/') ? u : '' }
  }
  plugin('PushNotifications')?.addListener('pushNotificationActionPerformed', (a: any) => {
    const p = toPath(String(a?.notification?.data?.url || '/app'))
    if (p) navigate(p)
  })
  plugin('App')?.addListener('appUrlOpen', (e: { url: string }) => {
    const p = toPath(e.url)
    if (p) navigate(p)
  })
  plugin('SplashScreen')?.hide?.().catch?.(() => {})
}
