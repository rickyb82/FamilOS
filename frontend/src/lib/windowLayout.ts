export type WindowMode = 'float'|'full'|'left'|'right'|'top-left'|'top-right'|'bottom-left'|'bottom-right'
export const zones = ['top-left','top-right','bottom-left','bottom-right'] as const
export function zoneStyle(mode:WindowMode) {
 const right=mode==='right'||mode.endsWith('-right'), bottom=mode.startsWith('bottom-')
 const quarter=zones.includes(mode as typeof zones[number])
 return {left:right?'calc(50% + 4px)':8,top:bottom?'calc(50% + 4px)':8,width:mode==='full'?'calc(100% - 16px)':'calc(50% - 12px)',height:quarter?'calc(50% - 12px)':'calc(100% - 16px)'}
}
export function snapZone(x:number,y:number,width:number,height:number):WindowMode|null {
 if(x<0||y<0||x>width||y>height)return null
 const left=x<36,right=x>width-36,top=y<60,bottom=y>height-60
 if(left||right){if(top)return left?'top-left':'top-right';if(bottom)return left?'bottom-left':'bottom-right';return left?'left':'right'}
 return y<24?'full':null
}
