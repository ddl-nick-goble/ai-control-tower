// ScopeTree (design spec 3.4, 4.6): nested lists with native checkboxes, indeterminate parents, disclosure buttons
// (aria-expanded + aria-controls), count pills and one dim row for empty leaves. Selection lives on leaves.
import React from 'react'
import { IconChev, Spinner } from './bits'

const isPending = (n) => !!n.pending
const kids = (n) => n.children || []
const isLeaf = (n) => kids(n).length === 0

// Selectable leaves under a node: those with agents (or an unknown count) that are not waiting on a role.
export function leavesOf(n) {
  if (isLeaf(n)) return n.agents === 0 || isPending(n) ? [] : [n]
  return kids(n).flatMap(leavesOf)
}
export const agentsOf = (n) => (isLeaf(n) ? n.agents || 0 : kids(n).reduce((s, c) => s + agentsOf(c), 0))

export function allLeaves(tree) { return tree.flatMap(leavesOf) }

// Ids to send to the API: every selected leaf, plus each parent whose selectable leaves are all selected.
export function selectedIds(tree, sel) {
  const out = []
  const walk = (n) => {
    kids(n).forEach(walk)
    const ls = leavesOf(n)
    if (ls.length && ls.every((l) => sel.has(l.id))) out.push(n.id)
  }
  tree.forEach(walk)
  return out
}

function Check({ checked, mixed, ...rest }) {
  const ref = React.useRef(null)
  React.useEffect(() => { if (ref.current) ref.current.indeterminate = !!mixed && !checked }, [mixed, checked])
  return <input ref={ref} type="checkbox" checked={checked} {...rest} />
}

function Node({ node, sel, setMany, uid, nounPlural }) {
  const [open, setOpen] = React.useState(true)
  const ls = leavesOf(node)
  const n = ls.filter((l) => sel.has(l.id)).length
  const checked = ls.length > 0 && n === ls.length
  const mixed = n > 0 && n < ls.length
  const children = kids(node)
  const active = children.filter((c) => leavesOf(c).length > 0 || isPending(c))
  const empty = children.filter((c) => isLeaf(c) && c.agents === 0)
  const cid = `${uid}-${node.id.replace(/[^a-zA-Z0-9]/g, '_')}`
  const count = agentsOf(node)
  const hasCount = typeof node.agents === 'number' || children.length > 0
  const pending = isPending(node)
  const toggle = (on) => setMany(ls.map((l) => l.id), on)
  return (
    <li data-testid={`scope-node-${node.id}`}>
      <div className={`po-node${pending || (isLeaf(node) && node.agents === 0) ? ' is-dim' : ''}`}>
        {children.length > 0 ? (
          <button type="button" className="po-toggle" aria-expanded={open} aria-controls={`${cid}-kids`}
                  aria-label={`${open ? 'Collapse' : 'Expand'} ${node.label}`} onClick={() => setOpen((o) => !o)}><IconChev /></button>
        ) : <span className="po-toggle-gap" />}
        <Check id={cid} checked={checked} mixed={mixed} disabled={pending || ls.length === 0}
               data-testid={`scope-checkbox-${node.id}`} onChange={(e) => toggle(e.target.checked)} aria-describedby={pending ? `${cid}-note` : undefined} />
        <label htmlFor={cid}><span className="po-node-name">{node.label}</span></label>
        {pending && <span className="po-node-sub po-pending" id={`${cid}-note`}><Spinner />Waiting for the role to reach this account. Retrying every 5 minutes.</span>}
        {!pending && hasCount && <span className={`po-count${count > 0 ? ' po-count-hi' : ''}`} data-testid={`scope-count-${node.id}`}>{count} {count === 1 ? 'agent' : 'agents'}</span>}
      </div>
      {children.length > 0 && (
        <ul id={`${cid}-kids`} hidden={!open}>
          {active.map((c) => <Node key={c.id} node={c} sel={sel} setMany={setMany} uid={uid} nounPlural={nounPlural} />)}
          {empty.length > 0 && (
            <li><div className="po-node is-dim">
              <span className="po-toggle-gap" /><span className="po-check-gap" />
              <span className="po-node-name po-grow">{empty.length} {empty.length === 1 ? nounPlural.replace(/s$/, '') : nounPlural} with no agents</span>
              <span className="po-node-sub">Not scanned. New agents there are picked up by daily sync.</span>
            </div></li>
          )}
        </ul>
      )}
    </li>
  )
}

export default function ScopeTree({ tree, sel, setSel, label, noun }) {
  const uid = React.useId().replace(/:/g, '')
  const setMany = React.useCallback((ids, on) => {
    setSel((cur) => { const next = new Set(cur); ids.forEach((id) => (on ? next.add(id) : next.delete(id))); return next })
  }, [setSel])
  return (
    <ul className="po-tree" data-testid="scope-tree" aria-label={label}>
      {tree.map((n) => <Node key={n.id} node={n} sel={sel} setMany={setMany} uid={uid} nounPlural={noun} />)}
    </ul>
  )
}
