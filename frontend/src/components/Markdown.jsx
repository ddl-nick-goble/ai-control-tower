import React from 'react'

// Minimal safe markdown renderer (no raw HTML): paragraphs, lists, headings, bold, italic, code, links.
function inline(text, key) {
  const out = []
  const re = /(\*\*[^*]+\*\*|`[^`]+`|\*[^*\s][^*]*\*|_[^_\s][^_]*_|\[[^\]]+\]\(https?:[^)\s]+\))/g
  let last = 0, m, i = 0
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(text.slice(last, m.index))
    const t = m[0]
    if (t.startsWith('**')) out.push(<strong key={`${key}-${i}`}>{t.slice(2, -2)}</strong>)
    else if (t.startsWith('`')) out.push(<code key={`${key}-${i}`}>{t.slice(1, -1)}</code>)
    else if (t.startsWith('[')) {
      const mm = /\[([^\]]+)\]\(([^)]+)\)/.exec(t)
      out.push(<a key={`${key}-${i}`} href={mm[2]} target="_blank" rel="noreferrer">{mm[1]}</a>)
    } else out.push(<em key={`${key}-${i}`}>{t.slice(1, -1)}</em>)
    last = m.index + t.length
    i++
  }
  if (last < text.length) out.push(text.slice(last))
  return out
}

export default function Markdown({ text }) {
  if (!text) return null
  const lines = String(text).split(/\r?\n/)
  const blocks = []
  let para = [], list = null
  const flushPara = () => { if (para.length) { blocks.push({ t: 'p', v: para.join(' ') }); para = [] } }
  const flushList = () => { if (list) { blocks.push(list); list = null } }
  for (const line of lines) {
    let m
    if (!line.trim()) { flushPara(); flushList() }
    else if ((m = /^(#{1,4})\s+(.*)/.exec(line))) { flushPara(); flushList(); blocks.push({ t: 'h', v: m[2] }) }
    else if ((m = /^\s*([-*]|\d+\.)\s+(.*)/.exec(line))) {
      flushPara()
      const ordered = /\d/.test(m[1])
      if (!list || list.ordered !== ordered) { flushList(); list = { t: 'l', ordered, items: [] } }
      list.items.push(m[2])
    } else { flushList(); para.push(line.trim()) }
  }
  flushPara(); flushList()
  return (
    <div className="markdown">
      {blocks.map((b, i) => {
        if (b.t === 'p') return <p key={i}>{inline(b.v, i)}</p>
        if (b.t === 'h') return <h4 key={i}>{inline(b.v, i)}</h4>
        const Tag = b.ordered ? 'ol' : 'ul'
        return <Tag key={i}>{b.items.map((it, j) => <li key={j}>{inline(it, `${i}-${j}`)}</li>)}</Tag>
      })}
    </div>
  )
}
