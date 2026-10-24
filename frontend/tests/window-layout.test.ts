import {expect,it} from 'vitest'
import {snapZone,zoneStyle,zones} from '../src/lib/windowLayout'
it('snaps only within desktop edges and corners',()=>{
 expect(snapZone(1,2,1200,800)).toBe('top-left')
 expect(snapZone(1190,790,1200,800)).toBe('bottom-right')
 expect(snapZone(1190,300,1200,800)).toBe('right')
 expect(snapZone(600,10,1200,800)).toBe('full')
 expect(snapZone(600,300,1200,800)).toBeNull()
 expect(snapZone(-1,20,1200,800)).toBeNull()
})
it('gives all four zones distinct positions and equal sizes',()=>{
 const styles=zones.map(zoneStyle)
 expect(new Set(styles.map(s=>`${s.left}:${s.top}`)).size).toBe(4)
 expect(new Set(styles.map(s=>`${s.width}:${s.height}`)).size).toBe(1)
})
