// PermissionChecklist (design spec 3.3, 4.5): one row per check, a fix box for each missing item, re-check and continue.
import React from 'react'
import { IconAlert, IconCheck, IconCopy, IconDash, Spinner, useCopy } from './bits'
import { slug } from './providers'

function Row({ item, copy, last }) {
  const key = item.key
  const state = item.ok ? 'ok' : item.required ? 'miss' : 'warn'
  const label = item.ok ? 'Passed' : 'Missing'
  return (
    <li className={`is-${state}`} data-testid={`permission-item-${key}`}>
      <span className="po-ico">{item.ok ? <IconCheck /> : item.required ? <IconDash /> : <IconAlert />}</span>
      <span className="po-check-label">{item.name}{item.required && <span className="po-req">Required</span>}</span>
      <span className="po-check-state" data-testid={`permission-item-${key}-status`}>{label}</span>
      {item.detail && <span className="po-check-detail">{item.detail}</span>}
      {!item.ok && item.fix && (
        <div className={`po-check-fix${item.required ? '' : ' warn'}`} data-testid={`permission-fix-${key}`}>
          <strong>{item.required ? 'How to fix this' : 'Optional. How to add it'}</strong>
          <p>{item.fix}</p>
          <div className="po-fix-actions">
            <button type="button" className="ds-btn ds-btn-sm" data-testid={`permission-fix-${key}-action`} onClick={() => copy(item.fix, key)}>
              <IconCopy />{last === key ? 'Copied' : 'Copy the fix'}
            </button>
          </div>
        </div>
      )}
    </li>
  )
}

export function SkeletonChecks() {
  return (
    <ul className="po-checks" aria-hidden="true">
      {[0, 1, 2, 3].map((i) => (
        <li key={i}><span className="po-ico"><Spinner /></span><span className="ds-skel po-skel-label" /><span className="po-check-state">Checking…</span></li>
      ))}
    </ul>
  )
}

export function summarize(items) {
  const total = items.length, passed = items.filter((i) => i.ok).length
  const reqMissing = items.filter((i) => !i.ok && i.required)
  const optMissing = items.filter((i) => !i.ok && !i.required)
  let text
  if (passed === total) text = `All ${total} checks passed`
  else text = `${passed} of ${total} passed` + (reqMissing.length ? ` · ${reqMissing.length} required missing` : '') + (optMissing.length ? ` · ${optMissing.length} optional missing` : '')
  return { text, reqMissing, optMissing }
}

export function withKeys(perms) {
  const seen = {}
  return perms.map((p) => {
    let k = slug(p.name) || 'check'
    seen[k] = (seen[k] || 0) + 1
    if (seen[k] > 1) k += `-${seen[k]}`
    return { ...p, key: k }
  })
}

export default function PermissionChecklist({ items }) {
  const { copy, note, last } = useCopy()
  return (
    <>
      <ul className="po-checks" data-testid="permission-checklist">
        {items.map((it) => <Row key={it.key} item={it} copy={copy} last={last} />)}
      </ul>
      <div className="ds-sr" role="status" aria-live="polite">{note}</div>
    </>
  )
}
