import { createContext, useContext, useEffect, useState, type ReactNode } from 'react'
import { get } from './api'
import { useAuth } from './auth'

export interface InstallationBrand { name: string; logo_url: string; revision: string; custom_logo: boolean }
const defaults: InstallationBrand = { name:'FamilOS',logo_url:'/api/branding/icon/512',revision:'default',custom_logo:false }
const Context = createContext({ brand:defaults,installationBrand:defaults,personalLogo:false,personalWallpaper:"",refresh:async()=>{} })
export const useBranding = () => useContext(Context)

export function BrandingProvider({ children }: { children:ReactNode }) {
  const { me } = useAuth()
  const [installationBrand,setInstallationBrand] = useState(defaults)
  const [personal,setPersonal] = useState<{logo_url:string;revision:string;wallpaper_url?:string}|null>(null)
  async function refresh() {
    try {
      const value = await get<InstallationBrand>('/branding')
      if(value.name&&value.logo_url) setInstallationBrand(value)
    } catch {}
    if(me) {
      try { const value=await get<any>('/branding/profile');setPersonal(value.custom_logo||value.wallpaper_url?value:null) } catch { setPersonal(null) }
    } else setPersonal(null)
  }
  useEffect(()=>{ void refresh() },[me?.user.id])
  const brand = personal?.logo_url ? {...installationBrand,logo_url:personal.logo_url} : installationBrand
  useEffect(()=>{
    document.querySelector<HTMLLinkElement>('link[rel="icon"]')?.setAttribute('href',brand.logo_url.replace('/512','/64'))
    // The installed PWA uses the public installation identity; personal artwork remains authenticated.
    document.querySelector<HTMLLinkElement>('link[rel="apple-touch-icon"]')?.setAttribute('href',installationBrand.logo_url.replace('/512','/180'))
    document.querySelector<HTMLLinkElement>('link[rel="manifest"]')?.setAttribute('href','/api/branding/manifest.webmanifest?v='+installationBrand.revision)
    document.querySelector('meta[name="apple-mobile-web-app-title"]')?.setAttribute('content',installationBrand.name)
  },[brand.logo_url,installationBrand])
  return <Context.Provider value={{brand,installationBrand,personalLogo:!!personal?.logo_url,personalWallpaper:personal?.wallpaper_url||"",refresh}}>{children}</Context.Provider>
}
