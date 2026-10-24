import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import remarkMath from 'remark-math'
import rehypeKatex from 'rehype-katex'
import 'katex/dist/katex.min.css'

/** Simboli LaTeX "da chat" (frecce, per, ecc.) resi come caratteri Unicode: piu' leggibili e niente formule inutili. */
const SIMPLE: [RegExp, string][] = [
  [/\$\\(?:right|long)?arrow\$/g, '→'], [/\$\\Rightarrow\$/g, '⇒'], [/\$\\leftarrow\$/g, '←'], [/\$\\leftrightarrow\$/g, '↔'],
  [/\$\\times\$/g, '×'], [/\$\\div\$/g, '÷'], [/\$\\pm\$/g, '±'], [/\$\\approx\$/g, '≈'], [/\$\\neq\$/g, '≠'],
  [/\$\\leq\$/g, '≤'], [/\$\\geq\$/g, '≥'], [/\$\\infty\$/g, '∞'], [/\$\\degree\$/g, '°'], [/\$\\%\$/g, '%'],
  [/\$\\cdot\$/g, '·'], [/\$\\bullet\$/g, '•'], [/\$\\checkmark\$/g, '✓'], [/\$\\star\$/g, '★'],
  [/\$(\d+(?:[.,]\d+)?)\s*\\%\$/g, '$1%'],                  // $30\%$ -> 30%
  [/\$(\d+(?:[.,]\d+)?)\^\\circ\$/g, '$1°'],               // $25^\circ$ -> 25°
]

export function tidyMath(text: string): string {
  let t = text
  for (const [re, rep] of SIMPLE) t = t.replace(re, rep)
  return t
}

export default function Markdown({ children, className }: { children: string; className?: string }) {
  return (
    <div className={className ?? 'markdown'}>
      <ReactMarkdown remarkPlugins={[remarkGfm, remarkMath]} rehypePlugins={[[rehypeKatex, { throwOnError: false, strict: false }]]}>
        {tidyMath(children)}
      </ReactMarkdown>
    </div>
  )
}
