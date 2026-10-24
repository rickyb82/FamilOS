/** Collegamenti agli editor ArtCraft nel browser: WordCraft (documenti), GridCraft (fogli), PdfCraft (PDF) e DeckCraft
 *  (presentazioni), serviti su /wordcraft/, /gridcraft/, /pdfcraft/ e /deckcraft/ quando i rispettivi profili sono attivi. */
export type ArtApp = 'wordcraft' | 'gridcraft' | 'pdfcraft' | 'deckcraft' | 'ideacraft'

export const WORDCRAFT_EXT = ['.docx', '.docm', '.dotx', '.doc', '.odt', '.rtf', '.txt', '.md', '.html', '.htm', '.tex']
export const GRIDCRAFT_EXT = ['.xlsx', '.xlsm', '.xls', '.xlsb', '.ods', '.csv', '.tsv']
export const PDFCRAFT_EXT = ['.pdf']
export const DECKCRAFT_EXT = ['.pptx', '.potx', '.ppsx', '.deckcraft']
export const IDEACRAFT_EXT = ['.ideacraft.json', '.mm', '.opml', '.xmind', '.mmd', '.drawio']

export const APP_LABEL: Record<ArtApp, string> = { wordcraft: 'WordCraft', gridcraft: 'GridCraft', pdfcraft: 'PdfCraft', deckcraft: 'DeckCraft', ideacraft: 'IdeaCraft' }
export const APP_ICON: Record<ArtApp, string> = { wordcraft: '📝', gridcraft: '📊', pdfcraft: '📕', deckcraft: '📽️', ideacraft: '🧠' }
/** Lingua dell'interfaccia degli editor (parametro ?lang= aggiunto dalla patch FamilAI; PdfCraft e WordCraft hanno l'italiano nativo). */
export const UI_LANG = 'it'

/** Quale editor apre questo file (undefined = nessuno). */
export function editorFor(filename: string): ArtApp | undefined {
  const lower = (filename || '').toLowerCase()
  if (WORDCRAFT_EXT.some((e) => lower.endsWith(e))) return 'wordcraft'
  if (GRIDCRAFT_EXT.some((e) => lower.endsWith(e))) return 'gridcraft'
  if (PDFCRAFT_EXT.some((e) => lower.endsWith(e))) return 'pdfcraft'
  if (DECKCRAFT_EXT.some((e) => lower.endsWith(e))) return 'deckcraft'
  if (IDEACRAFT_EXT.some((e) => lower.endsWith(e))) return 'ideacraft'
  return undefined
}

type Features = { wordcraft_web?: boolean; gridcraft_web?: boolean; pdfcraft_web?: boolean; deckcraft_web?: boolean; ideacraft_web?: boolean } | undefined

/** True se il file si puo' modificare in un editor attivo su questo server. */
export function canEdit(filename: string, features: Features): boolean {
  const app = editorFor(filename)
  return !!app && !!features?.[`${app}_web`]
}

/** Id compatibile con quelli del server (32 hex): per i file nuovi lo sceglie il browser. */
export function newFileId(): string {
  const b = new Uint8Array(16)
  crypto.getRandomValues(b)
  return Array.from(b, (x) => x.toString(16).padStart(2, '0')).join('')
}

/** Rotta interna dell'editor per un file esistente. */
export function editorPath(fileId: string, filename?: string): string {
  return `/editor?file=${encodeURIComponent(fileId)}` + (filename ? `&name=${encodeURIComponent(filename)}` : '')
}

/** Rotta interna dell'editor per un file nuovo (documento o foglio), collegato a una chat e/o a un progetto. */
export function newFilePath(app: ArtApp, opts: { conversationId?: string | null; projectId?: string | null; folderId?: string | null; name?: string } = {}): string {
  const q = new URLSearchParams({ file: newFileId(), new: '1', app })
  if (opts.conversationId) q.set('conversation_id', opts.conversationId)
  if (opts.projectId) q.set('project_id', opts.projectId)
  if (opts.folderId) q.set('folder_id', opts.folderId)
  q.set('name', opts.name || (app === 'gridcraft' ? 'foglio.xlsx' : app === 'deckcraft' ? 'presentazione.pptx' : app === 'ideacraft' ? 'mappa.ideacraft.json' : 'documento.docx'))
  return `/editor?${q.toString()}`
}
