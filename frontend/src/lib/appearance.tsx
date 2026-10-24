import { useBranding } from './branding'
import { useEffect } from 'react'
import { useAuth } from './auth'
export type Appearance = { theme:'system'|'light'|'dark'; accent:'lavender'|'blue'|'green'|'rose'|'amber'; desktop_style:'aurora'|'oceano'|'tramonto'; effects:'full'|'reduced'|'off'; wallpaper:'aurora'|'oceano'|'tramonto'|'soft'|'ocean'|'forest'|'sunset'|'midnight'|'custom'; lock_after_minutes:0|1|5|15|30 }
export const DEFAULT_APPEARANCE:Appearance={theme:'system',accent:'lavender',desktop_style:'aurora',effects:'full',wallpaper:'aurora',lock_after_minutes:5}
const colors={lavender:['#8064a1','#e9e0f3'],blue:['#3576b5','#dcebf9'],green:['#348571','#dbefe7'],rose:['#b65d86','#f5e0ea'],amber:['#a97927','#f4e9cc']}
export const DESKTOP_STYLES = [
 {id:'aurora',name:'Aurora',description:'Luce viola e riflessi di seta',accent:'lavender'},
 {id:'oceano',name:'Oceano',description:'Vetro azzurro e onde turchesi',accent:'blue'},
 {id:'tramonto',name:'Tramonto',description:'Dune calde e sfumature ambrate',accent:'amber'},
] as const
const wallpapers={
 soft:['radial-gradient(ellipse at 0 0,#e1dcf2,transparent 60%),radial-gradient(ellipse at 100% 100%,#eaded3,transparent 60%),#efedf4','radial-gradient(ellipse at 0 0,#393247,transparent 60%),radial-gradient(ellipse at 100% 100%,#44393e,transparent 60%),#24232c'],
 ocean:['radial-gradient(ellipse at 0 0,#bcd9f4,transparent 65%),radial-gradient(ellipse at 100% 100%,#c0e8e7,transparent 65%),#e5eff5','radial-gradient(ellipse at 0 0,#183d5d,transparent 70%),#14232e'],
 forest:['radial-gradient(ellipse at 0 0,#cde4cf,transparent 65%),radial-gradient(ellipse at 100% 100%,#e8e0c8,transparent 65%),#e9efe5','radial-gradient(ellipse at 0 0,#284e43,transparent 70%),#172923'],
 sunset:['radial-gradient(ellipse at 0 0,#f4d6cb,transparent 65%),radial-gradient(ellipse at 100% 100%,#ddd0ed,transparent 65%),#f3e9eb','radial-gradient(ellipse at 0 0,#673e50,transparent 70%),#30222f'],
 midnight:['radial-gradient(ellipse at 0 0,#d1d9ee,transparent 65%),#e8ebf5','radial-gradient(ellipse at 0 0,#263b68,transparent 70%),#151c30'],
}
export function AppearanceEffects(){
 const {me}=useAuth()
 const {personalWallpaper}=useBranding()
 const prefs={...DEFAULT_APPEARANCE,...me?.prefs?.appearance}
 useEffect(()=>{
   const media=window.matchMedia('(prefers-color-scheme: dark)')
   const apply=()=>{
     const dark=prefs.theme==='dark'||prefs.theme==='system'&&media.matches
     const root=document.documentElement
     root.dataset.theme=dark?'dark':'light'
     root.dataset.desktopStyle=prefs.desktop_style
     root.dataset.effects=prefs.effects
     const [color,soft]=colors[prefs.accent]||colors.lavender
     root.style.setProperty('--primary',color);root.style.setProperty('--accent',color)
     root.style.setProperty('--accent-soft',dark?color+'35':soft)
     root.style.setProperty('--user-bubble',dark?color+'50':soft)
     root.style.setProperty('--desktop-background',prefs.wallpaper==='custom'&&personalWallpaper ? 'linear-gradient(#00000015,#00000015),url('+personalWallpaper+') center/cover no-repeat' : DESKTOP_STYLES.some(s=>s.id===prefs.wallpaper) ? `linear-gradient(#000000${dark?'38':'08'},#000000${dark?'38':'08'}),url(/wallpapers/${prefs.wallpaper}.png) center/cover no-repeat` : (wallpapers[prefs.wallpaper as keyof typeof wallpapers]||wallpapers.soft)[dark?1:0])
     root.style.colorScheme=dark?'dark':'light'
   }
   apply();media.addEventListener('change',apply)
   return()=>media.removeEventListener('change',apply)
 },[prefs.theme,prefs.accent,prefs.wallpaper,prefs.desktop_style,prefs.effects,personalWallpaper])
 return null
}
