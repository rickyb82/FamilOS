/**
 * Foto dal telefono pronte per l'assistente: dritte (rotazione EXIF), al massimo 2048 px sul lato lungo e in JPEG. Le foto
 * dell'iPhone sono HEIC (Safari le apre, i modelli spesso no) e pesano 3-12 MB. Se il browser non sa decodificarla, resta com'è.
 */
export async function photoForUpload(file: File, maxSide = 2048): Promise<File> {
  if (!file.type.startsWith('image/') || file.type === 'image/gif' || file.type === 'image/svg+xml') return file
  try {
    const bmp = await createImageBitmap(file)
    const scale = Math.min(1, maxSide / Math.max(bmp.width, bmp.height))
    const w = Math.max(1, Math.round(bmp.width * scale)), h = Math.max(1, Math.round(bmp.height * scale))
    const canvas = document.createElement('canvas')
    canvas.width = w; canvas.height = h
    const ctx = canvas.getContext('2d')
    if (!ctx) return file
    ctx.fillStyle = '#fff'; ctx.fillRect(0, 0, w, h)
    ctx.drawImage(bmp, 0, 0, w, h)
    bmp.close?.()
    const blob: Blob | null = await new Promise((r) => canvas.toBlob(r, 'image/jpeg', 0.85))
    if (!blob || (blob.size >= file.size && file.type === 'image/jpeg' && scale === 1)) return file
    const stem = (file.name || 'foto').replace(/\.[^.]+$/, '') || 'foto'
    return new File([blob], `${stem}.jpg`, { type: 'image/jpeg', lastModified: Date.now() })
  } catch {
    return file
  }
}
