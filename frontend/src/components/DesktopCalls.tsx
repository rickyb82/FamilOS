import { useEffect, useState } from 'react'
import { get } from '../lib/api'
import { calls } from '../lib/calls'
import CallOverlay from './CallOverlay'

export default function DesktopCalls({userId}:{userId:string}) {
  const [names,setNames]=useState<Record<string,string>>({})
  useEffect(()=>{
    let active=true
    calls.start(userId)
    get<Array<{id:string;name:string}>>('/msg/contacts').then(items=>{if(active)setNames(Object.fromEntries(items.map(u=>[u.id,u.name])))}).catch(()=>{})
    return ()=>{active=false;calls.stop()}
  },[userId])
  return <CallOverlay names={names}/>
}
