// Pieces shared by the review table and the single-agent intake page: criteria with their "why", the tier override,
// risk flags, the field diff and the owner select.
import React from 'react'
import { scansApi } from '../../api'
import { TierBadge } from '../ui'
import { IconUp } from './bits'

export const CRITERIA = [
  ['decision_impact', 'Decision impact'], ['financial_exposure', 'Financial exposure'], ['customer_impact', 'Customer impact'],
  ['autonomy', 'Autonomy'], ['data_sensitivity', 'Data sensitivity'], ['reversibility', 'Reversibility'],
  ['regulatory_exposure', 'Regulatory exposure'],
]

export const tierOf = (row) => row?.suggestion?.tier ?? null
export const isSelectable = (row) => row.class === 'new' || row.class === 'changed'
export const wentUp = (row) => row.current_tier != null && tierOf(row) != null && tierOf(row) < row.current_tier
export const tierSet = (row) => row.overrides?.tier || null

// ---- risk flags: a fixed vocabulary. The server sends codes (tier_1) or labels; tier and owner are derived live
// so a changed tier or owner updates the chips at once.
const norm = (f) => String(f).toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '')
const LABEL = {
  no_owner: 'No owner', risk_went_up: 'Risk went up', tier_1: 'Tier 1', no_distinct_identity: 'No distinct identity',
  write_scopes: 'Write scopes', redacted_secrets_found: 'Redacted secrets found', redacted_secrets: 'Redacted secrets found',
}
const HIGH = new Set(['no_owner', 'risk_went_up', 'tier_1', 'no_distinct_identity', 'write_scopes'])
const ORDER = ['no_owner', 'risk_went_up', 'tier_1', 'no_distinct_identity', 'write_scopes', 'redacted_secrets_found']

export function rowFlags(row) {
  const keys = new Set((row.flags || []).map(norm).map((k) => (k === 'redacted_secrets' ? 'redacted_secrets_found' : k)))
  keys.delete('tier_1'); keys.delete('no_owner')
  if (tierOf(row) === 1) keys.add('tier_1')
  if (!row.owner && row.class !== 'missing') keys.add('no_owner')
  if (wentUp(row)) keys.add('risk_went_up')
  const list = [...keys].sort((a, b) => (ORDER.indexOf(a) + 1 || 99) - (ORDER.indexOf(b) + 1 || 99))
  return list.map((k) => ({ key: k, label: LABEL[k] || String(k).replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase()), high: HIGH.has(k) }))
}
export const isRisky = (row) => rowFlags(row).some((f) => ['tier_1', 'no_owner', 'no_distinct_identity'].includes(f.key))

// ---- diff lines for a Changed row. The server sends {field: {before, after}}; a list of {field, op, before, after} also works.
const val = (v) => {
  if (v == null) return 'none'
  if (Array.isArray(v)) return v.map(val).join(', ') || 'none'
  if (typeof v === 'object') return v.name || v.id || JSON.stringify(v)
  return String(v)
}
export function diffLines(diff) {
  if (!diff) return []
  if (Array.isArray(diff)) {
    return diff.map((d) => (d.op === 'add' ? { type: 'add', text: `+ ${d.field} ${val(d.after)}` }
      : d.op === 'remove' ? { type: 'del', text: `− ${d.field} ${val(d.before)}` }
      : { type: 'mod', text: `~ ${d.field}: ${val(d.before)} → ${val(d.after)}` }))
  }
  const out = []
  Object.entries(diff).forEach(([field, d]) => {
    const { before, after } = d || {}
    if (Array.isArray(before) && Array.isArray(after)) {
      const b = before.map(val), a = after.map(val)
      a.filter((x) => !b.includes(x)).forEach((x) => out.push({ type: 'add', text: `+ ${field} ${x}` }))
      b.filter((x) => !a.includes(x)).forEach((x) => out.push({ type: 'del', text: `− ${field} ${x}` }))
    } else out.push({ type: 'mod', text: `~ ${field}: ${val(before)} → ${val(after)}` })
  })
  return out
}
export function DiffList({ row, testid }) {
  const lines = diffLines(row.diff)
  if (!lines.length) return null
  return (
    <>
      <h4>What changed since the last scan</h4>
      <ul className="po-diff" data-testid={testid}>{lines.map((l, i) => <li key={i} className={l.type}>{l.text}</li>)}</ul>
      {wentUp(row) && <p className="ds-helper">Risk went up, so this agent goes back to Pending review on import.</p>}
    </>
  )
}

// ---- the seven criteria with a score and one evidence line each
export function CriteriaList({ row }) {
  const sug = row.suggestion?.suggestions || {}
  return (
    <ul className="po-crit">
      {CRITERIA.filter(([k]) => sug[k]).map(([k, name]) => (
        <li key={k} data-testid={`intake-criterion-${k}`}>
          <span className="k">{name}</span>
          <span className={`v${sug[k].score >= 4 ? ' hi' : ''}`}>{sug[k].score}</span>
          <span className="why">{sug[k].reason}</span>
        </li>
      ))}
    </ul>
  )
}

// ---- tier with an override that needs a reason
export function TierBlock({ row, canEdit, onUpdated }) {
  const tier = tierOf(row)
  const set = tierSet(row)
  const [open, setOpen] = React.useState(false)
  const [pick, setPick] = React.useState(tier || 3)
  const [reason, setReason] = React.useState('')
  const [err, setErr] = React.useState(false)
  const [busy, setBusy] = React.useState(false)
  const [saveErr, setSaveErr] = React.useState('')
  const openBtn = React.useRef(null)
  const radios = React.useRef({})
  const errId = `rsn-err-${row.id}`

  React.useEffect(() => { if (open) radios.current[pick]?.focus() }, [open]) // eslint-disable-line react-hooks/exhaustive-deps
  const close = () => { setOpen(false); setErr(false); setSaveErr(''); requestAnimationFrame(() => openBtn.current?.focus()) }
  const arrow = (e) => {
    const d = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[e.key]
    if (!d) return
    e.preventDefault()
    const n = ((pick - 1 + d + 4) % 4) + 1
    setPick(n); requestAnimationFrame(() => radios.current[n]?.focus())
  }
  const save = async () => {
    if (reason.trim().length < 10) { setErr(true); return }
    setBusy(true); setErr(false); setSaveErr('')
    try {
      const r = await scansApi.patch(row.id, { overrides: { tier: { score: pick, reason: reason.trim() } } })
      onUpdated(r); setReason(''); close()
    } catch (e) { setSaveErr(e.message || "ACT couldn't save the tier.") } finally { setBusy(false) }
  }
  return (
    <>
      <h4>Tier</h4>
      {!open && (
        <p className="po-tier-line">
          <TierBadge tier={tier} />
          <span className="ds-helper po-flat">{set ? `Set by you: "${set.reason}"` : 'Suggested by ACT'}</span>
        </p>
      )}
      {!open && canEdit && (
        <button type="button" className="ds-btn ds-btn-sm" ref={openBtn} data-testid="intake-override-open" aria-expanded="false"
                onClick={() => { setPick(tier || 3); setOpen(true) }}>Change tier</button>
      )}
      {open && (
        <div className="po-ovr" role="group" aria-labelledby={`tp-l-${row.id}`} data-testid="intake-override">
          <span className="ds-label" id={`tp-l-${row.id}`}>Set tier</span>
          <div className="po-tierpick" role="radiogroup" aria-labelledby={`tp-l-${row.id}`} onKeyDown={arrow}>
            {[1, 2, 3, 4].map((n) => (
              <button type="button" key={n} role="radio" aria-checked={pick === n} tabIndex={pick === n ? 0 : -1}
                      ref={(el) => { radios.current[n] = el }} data-testid={`intake-override-tier-${n}`} onClick={() => setPick(n)}>Tier {n}</button>
            ))}
          </div>
          {tier != null && pick > tier && <p className="ds-helper po-flat">Lowering below the suggestion is flagged to the reviewer.</p>}
          <label className="ds-label" htmlFor={`rsn-${row.id}`}>Reason <span className="po-opt">(required, shown to the reviewer)</span></label>
          <input className="ds-input" id={`rsn-${row.id}`} data-testid="intake-override-reason" value={reason} autoComplete="off"
                 placeholder="For example: payments tool is disabled in production" aria-invalid={err ? 'true' : undefined}
                 aria-describedby={errId} onChange={(e) => { setReason(e.target.value); if (err) setErr(false) }}
                 onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); save() } }} />
          <p className="po-fielderr" id={errId} role={err ? 'alert' : undefined}>{err ? 'Enter a reason of at least 10 characters. Reviewers see it with the tier.' : ''}</p>
          {saveErr && <p className="po-fielderr" role="alert">{saveErr}</p>}
          <div className="po-ovr-actions">
            <button type="button" className="ds-btn ds-btn-primary ds-btn-sm" data-testid="intake-save" disabled={busy} aria-busy={busy ? 'true' : undefined} onClick={save}>Save tier</button>
            <button type="button" className="ds-btn ds-btn-sm" onClick={close}>Cancel</button>
          </div>
        </div>
      )}
    </>
  )
}

// ---- tier cell text: badge, delta, and where the tier came from
export function TierCell({ row }) {
  const t = tierOf(row)
  const set = tierSet(row)
  const live = row.class === 'new' || row.class === 'changed'
  return (
    <>
      <div className="po-tiercell">
        {t == null ? <span className="ds-helper po-flat">Not scored</span> : <TierBadge tier={t} />}
        {wentUp(row) && !set && <span className="po-tierdelta up"><IconUp /> from Tier {row.current_tier}</span>}
      </div>
      <span className="po-tiernote">
        {set ? `Set by you · suggested Tier ${row.suggestion.suggested_tier ?? t}` : live ? 'Suggested' : 'Current tier'}
      </span>
    </>
  )
}

// ---- owner select. options: names of people already owning agents in ACT.
export function OwnerSelect({ row, owners, onChange, label, disabled, setByYou }) {
  const list = React.useMemo(() => [...new Set([...(owners || []), ...(row.owner ? [row.owner] : [])])].sort((a, b) => a.localeCompare(b)), [owners, row.owner])
  return (
    <>
      <select className={`po-own${row.owner ? '' : ' is-none'}`} aria-label={label} value={row.owner || ''} disabled={disabled}
              onChange={(e) => onChange(e.target.value)}>
        <option value="">No owner</option>
        {list.map((o) => <option key={o} value={o}>{o}</option>)}
      </select>
      <span className="po-own-src">{!row.owner ? 'Imports as Needs owner' : setByYou ? 'Set by you' : 'Found at the source'}</span>
    </>
  )
}
