export interface PanelGroup { label: string; items: { id: string; label: string; icon: string }[] }
export default function PanelMenu({ groups, active, onChange, label = 'Sezioni' }: { groups: PanelGroup[]; active: string; onChange: (id: string) => void; label?: string }) {
  return <aside className="panel-menu">
    <label className="panel-menu-mobile">{label}<select value={active} onChange={e => onChange(e.target.value)}>{groups.map(group => <optgroup key={group.label} label={group.label}>{group.items.map(item => <option key={item.id} value={item.id}>{item.label}</option>)}</optgroup>)}</select></label>
    <nav aria-label={label}>{groups.map(group => <section key={group.label}><h3>{group.label}</h3>{group.items.map(item => <button key={item.id} className={active === item.id ? 'selected' : ''} aria-current={active === item.id ? 'page' : undefined} onClick={() => onChange(item.id)}><span aria-hidden="true">{item.icon}</span>{item.label}</button>)}</section>)}</nav>
  </aside>
}
