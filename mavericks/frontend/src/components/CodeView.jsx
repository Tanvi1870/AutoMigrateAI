import { useMemo } from 'react'

const PYTHON = new RegExp(
  [
    /(#[^\n]*)/.source,
    /("""[\s\S]*?"""|'''[\s\S]*?'''|[rbfRBF]?"(?:\\.|[^"\\\n])*"|[rbfRBF]?'(?:\\.|[^'\\\n])*')/.source,
    /(@[\w.]+)/.source,
    /\b(def|class|import|from|return|if|elif|else|for|while|with|as|try|except|finally|raise|pass|in|not|and|or|is|lambda|yield|assert|async|await)\b/.source,
    /\b(None|True|False|self)\b/.source,
    /\b(\d+(?:\.\d+)?)\b/.source,
  ].join('|'),
  'g',
)
const JSON_LIKE = /("(?:\\.|[^"\\\n])*"(?=\s*:))|("(?:\\.|[^"\\\n])*")|\b(true|false|null)\b|(-?\b\d+(?:\.\d+)?\b)|(^\s*[#;][^\n]*)/gm

const PY_CLASSES = ['tk-comment', 'tk-string', 'tk-decorator', 'tk-keyword', 'tk-const', 'tk-number']
const JSON_CLASSES = ['tk-key', 'tk-string', 'tk-const', 'tk-number', 'tk-comment']

function tokenize(text, pattern, classes) {
  const tokens = []
  let last = 0
  for (const match of text.matchAll(pattern)) {
    if (match.index > last) tokens.push({ text: text.slice(last, match.index) })
    const group = match.slice(1).findIndex((value) => value !== undefined)
    tokens.push({ text: match[0], cls: classes[group] })
    last = match.index + match[0].length
  }
  if (last < text.length) tokens.push({ text: text.slice(last) })
  return tokens
}

// Split the token stream into lines so multi-line strings keep their colour on every line.
function toLines(tokens) {
  const lines = [[]]
  for (const token of tokens) {
    token.text.split('\n').forEach((part, index) => {
      if (index > 0) lines.push([])
      if (part) lines[lines.length - 1].push({ text: part, cls: token.cls })
    })
  }
  return lines
}

export function highlight(content, path) {
  if (path.endsWith('.py')) return toLines(tokenize(content, PYTHON, PY_CLASSES))
  if (/\.(json|ini|cfg|toml|txt)$/.test(path)) return toLines(tokenize(content, JSON_LIKE, JSON_CLASSES))
  return toLines([{ text: content }])
}

/** Read-only code view. `markers` maps a line number to a label, e.g. {21: 'Step 1'}. */
export default function CodeView({ content, path = '', markers = {}, maxHeight }) {
  const lines = useMemo(() => highlight(content ?? '', path), [content, path])
  return (
    <div className="code" style={maxHeight ? { maxHeight } : undefined}>
      <table>
        <tbody>
          {lines.map((tokens, index) => {
            const number = index + 1
            const marker = markers[number]
            return (
              <tr key={number} className={marker ? 'code__line is-marked' : 'code__line'}>
                <td className="code__gutter">{number}</td>
                <td className="code__marker">{marker && <span title={marker}>{marker}</span>}</td>
                <td className="code__text">
                  {tokens.length === 0
                    ? '\u00a0'
                    : tokens.map((token, i) => (
                        <span key={i} className={token.cls}>
                          {token.text}
                        </span>
                      ))}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
