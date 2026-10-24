import { useBranding } from '../lib/branding'

/** Il logo personalizzabile: nella barra laterale (small) e nelle pagine di accesso (grande). */
export default function Brand({ size = 'small', tagline }: { size?: 'small' | 'large'; tagline?: string }) {
  const { brand } = useBranding()
  return (
    <span className={`brand-mark ${size}`}>
      <img src={brand.logo_url} alt={brand.name} className="brand-logo" />
      {tagline && <small className="muted brand-tagline">{tagline}</small>}
    </span>
  )
}
