import React from 'react'

// Minimal safe markdown renderer (no raw HTML): paragraphs, nested lists, headings, bold, italic, code, links,
// backslash escapes and the &lt; &gt; entities the evidence pack uses for untrusted text.
function inline(text, key) {
  const out = []
  const re = /(\\[\\!\[\]()`*_#|&<>]|&(?:lt|gt|amp);|\*\*[^*]+\*\*|`[^`]+`|\*[^*\s][^*]*\*|(?<![A-Za-z0-9])_[^_\s][^_]*_(?![A-Za-z0-9])|\[[^\]]+\]\(https?:[^)\s]+\))/g
  let last = 0, m, i = 0
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(text.slice(last, m.index))
    const t = m[0]
    if (t.startsWith('\\')) out.push(t.slice(1))
    else if (t.startsWith('&')) out.push({ '&lt;': '<', '&gt;': '>', '&amp;': '&' }[t])
    else if (t.startsWith('**')) out.push(<strong key={`${key}-${i}`}>{t.slice(2, -2)}</strong>)
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

// Flat items with indents -> nested tree.
function tree(items) {
  const root = { children: [] }
  const stack = [{ indent: -1, node: root }]
  for (const it of items) {
    const node = { text: it.text, ordered: it.ordered, children: [] }
    while (stack.length > 1 && stack[stack.length - 1].indent >= it.indent) stack.pop()
    stack[stack.length - 1].node.children.push(node)
    stack.push({ indent: it.indent, node })
  }
  return root.children
}

function List({ nodes, k }) {
  const Tag = nodes[0].ordered ? 'ol' : 'ul'
  return (
    <Tag>
      {nodes.map((n, j) => (
        <li key={j}>{inline(n.text, `${k}-${j}`)}{n.children.length > 0 && <List nodes={n.children} k={`${k}-${j}`} />}</li>
      ))}
    </Tag>
  )
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
      if (!list) list = { t: 'l', items: [] }
      list.items.push({ indent: /^\s*/.exec(line)[0].length, ordered, text: m[2] })
    } else { flushList(); para.push(line.trim()) }
  }
  flushPara(); flushList()
  return (
    <div className="markdown">
      {blocks.map((b, i) => {
        if (b.t === 'p') return <p key={i}>{inline(b.v, i)}</p>
        if (b.t === 'h') return <h4 key={i}>{inline(b.v, i)}</h4>
        return <List key={i} nodes={tree(b.items)} k={String(i)} />
      })}
    </div>
  )
}
