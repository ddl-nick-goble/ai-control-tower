// Design-system building blocks (styles live in ../design.css). Reused by every page.
import React from 'react'
import { Link } from 'react-router-dom'

const cx = (...a) => a.filter(Boolean).join(' ')

export function Breadcrumb({ items }) {
  return (
    <nav aria-label="Breadcrumb" className="ds-breadcrumb">
      <ol>
        {items.map((it, i) => (
          <li key={i}>
            {it.to ? <Link to={it.to}>{it.label}</Link> : <span aria-current="page">{it.label}</span>}
          </li>
        ))}
      </ol>
    </nav>
  )
}

export function PageHeader({ breadcrumb, title, description, descriptionTestId, actions }) {
  return (
    <header className="ds-page-header">
      {breadcrumb}
      <div className="ds-page-header-row">
        <div>
          <h1 className="ds-title">{title}</h1>
          {description && <p className="ds-description" data-testid={descriptionTestId}>{description}</p>}
        </div>
        {actions}
      </div>
    </header>
  )
}

export function Button({ variant = 'secondary', busy, children, className, ...rest }) {
  return (
    <button type="button" className={cx('ds-btn', `ds-btn-${variant}`, className)}
            aria-busy={busy ? 'true' : undefined} {...rest}>{children}</button>
  )
}

export function Alert({ tone = 'error', title, children, ...rest }) {
  return (
    <div className={cx('ds-alert', `ds-alert-${tone}`)} role={tone === 'error' ? 'alert' : 'status'} {...rest}>
      <svg className="ds-alert-icon" width="20" height="20" viewBox="0 0 20 20" aria-hidden="true">
        <circle cx="10" cy="10" r="9" fill="none" stroke="currentColor" strokeWidth="1.6" />
        <path d="M10 5.5v5.5M10 13.5v1.2" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
      </svg>
      <div>{title && <strong>{title}</strong>}<p>{children}</p></div>
    </div>
  )
}

export function Section({ number, title, helper, children }) {
  const id = React.useId()
  return (
    <section className="ds-card ds-section" data-testid="register-section" aria-labelledby={id}>
      <header className="ds-section-head">
        <span className="ds-section-num" aria-hidden="true">{number}</span>
        <div>
          <h2 id={id} className="ds-section-title"><span className="ds-sr">{number} </span>{title}</h2>
          <p className="ds-helper" data-testid="section-helper">{helper}</p>
        </div>
      </header>
      <div className="ds-section-body">{children}</div>
    </section>
  )
}

// Labelled field. Children is a function receiving the props to spread onto the control.
export function Field({ label, help, helpTestId, error, errorTestId, children }) {
  const id = React.useId()
  const helpId = `${id}-help`, errId = `${id}-err`
  const props = {
    id, 'aria-invalid': error ? 'true' : undefined,
    'aria-describedby': [help && helpId, error && errId].filter(Boolean).join(' ') || undefined,
  }
  return (
    <div className="ds-field">
      <label htmlFor={id} className="ds-label">{label}</label>
      {children(props)}
      {help && <p id={helpId} className="ds-helper" data-testid={helpTestId}>{help}</p>}
      {error && <p id={errId} className="ds-field-error" data-testid={errorTestId}>{error}</p>}
    </div>
  )
}

// 1..N segmented control (role=radiogroup / radio), arrow-key operable with a roving tabindex.
export function Segmented({ name, question, value, onChange, testid, rowTestId, options = [1, 2, 3, 4, 5], lowLabel = 'Low', highLabel = 'High' }) {
  const id = React.useId()
  const refs = React.useRef([])
  const move = (i) => {
    const j = (i + options.length) % options.length
    onChange(options[j])
    refs.current[j]?.focus()
  }
  const onKey = (e, i) => {
    const k = e.key
    if (k === 'ArrowRight' || k === 'ArrowDown') { e.preventDefault(); move(i + 1) }
    else if (k === 'ArrowLeft' || k === 'ArrowUp') { e.preventDefault(); move(i - 1) }
    else if (k === 'Home') { e.preventDefault(); move(0) }
    else if (k === 'End') { e.preventDefault(); move(options.length - 1) }
  }
  return (
    <div className="ds-criterion" data-testid={rowTestId}>
      <div className="ds-criterion-text">
        <span id={id} className="ds-label">{name}</span>
        <p className="ds-helper" data-testid="criterion-question">{question}</p>
      </div>
      <div className="ds-criterion-control">
        <span className="ds-segmented-end" aria-hidden="true">{lowLabel}</span>
        <div role="radiogroup" aria-labelledby={id} className="ds-segmented" data-testid={testid}>
          {options.map((n, i) => (
            <button key={n} type="button" role="radio" aria-checked={n === value} ref={(el) => { refs.current[i] = el }}
                    aria-describedby={i === 0 ? `${id}-lo` : i === options.length - 1 ? `${id}-hi` : undefined}
                    tabIndex={n === value ? 0 : -1} className="ds-segment" onClick={() => onChange(n)}
                    onKeyDown={(e) => onKey(e, i)}>{n}</button>
          ))}
        </div>
        <span className="ds-segmented-end" aria-hidden="true">{highLabel}</span>
        <span id={`${id}-lo`} className="ds-sr">{lowLabel}</span>
        <span id={`${id}-hi`} className="ds-sr">{highLabel}</span>
      </div>
    </div>
  )
}

// Token input: Enter or comma adds, Backspace on empty removes the last, duplicates ignored.
export function ChipInput({ label, help, value, onChange, suggestions = [], placeholder, testid }) {
  const [text, setText] = React.useState('')
  const inputRef = React.useRef(null)
  const id = React.useId()
  const add = (raw) => {
    const next = [...value]
    String(raw).split(',').map((s) => s.trim()).filter(Boolean).forEach((s) => { if (!next.includes(s)) next.push(s) })
    if (next.length !== value.length) onChange(next)
  }
  const onKey = (e) => {
    if (e.key === 'Enter') { e.preventDefault(); add(text); setText('') }
    else if (e.key === 'Backspace' && !text && value.length) onChange(value.slice(0, -1))
  }
  const onText = (e) => {
    const v = e.target.value
    if (v.includes(',')) {
      const parts = v.split(',')
      add(parts.slice(0, -1).join(','))
      setText(parts[parts.length - 1])
    } else setText(v)
  }
  const [live, setLive] = React.useState('')
  const prev = React.useRef(value)
  React.useEffect(() => {
    const before = prev.current
    const added = value.filter((x) => !before.includes(x)), removed = before.filter((x) => !value.includes(x))
    prev.current = value
    if (added.length) setLive(`Added scope ${added.join(', ')}. ${value.length} in total.`)
    else if (removed.length) setLive(`Removed scope ${removed.join(', ')}. ${value.length} in total.`)
  }, [value])
  const open = suggestions.filter((s) => !value.includes(s))
  return (
    <div className="ds-field">
      <label htmlFor={id} className="ds-label">{label}</label>
      <div className="ds-chip-box" onClick={() => inputRef.current?.focus()}>
        {value.map((s) => (
          <span key={s} className="ds-chip" data-testid="scope-chip">
            {s}
            <button type="button" aria-label={`Remove ${s}`} onClick={() => onChange(value.filter((x) => x !== s))}>
              <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden="true"><path d="M2 2l6 6M8 2L2 8" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" /></svg>
            </button>
          </span>
        ))}
        <input id={id} ref={inputRef} type="text" className="ds-chip-input" data-testid={testid} value={text}
               onChange={onText} onKeyDown={onKey} onBlur={() => { if (text.trim()) { add(text); setText('') } }}
               placeholder={value.length ? '' : placeholder} autoComplete="off" />
      </div>
      {help && <p className="ds-helper">{help}</p>}
      <div className="ds-sr" role="status" aria-live="polite" aria-atomic="true">{live}</div>
      {open.length > 0 && (
        <div className="ds-suggest" role="group" aria-label="Suggested scopes">
          <span className="ds-suggest-label">Suggestions</span>
          {open.map((s) => (
            <button key={s} type="button" className="ds-suggestion" data-testid="scope-suggestion" onClick={() => add(s)}>
              <span aria-hidden="true">+</span> {s}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

export function Summary({ title, testid, children }) {
  return (
    <aside className="ds-card ds-summary" data-testid={testid} aria-label={title}>
      <h2 className="ds-summary-title">{title}</h2>
      {children}
    </aside>
  )
}
