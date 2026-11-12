// Shell for every wizard step: breadcrumb, h1 (focus target on step change), step rail and the trust aside.
import React from 'react'
import { Link } from 'react-router-dom'
import { Breadcrumb } from '../form'
import { IconCheck, IconX } from './bits'
import { PROVIDERS, STEPS } from './providers'

export function StepRail({ provider, connId, step, hrefs }) {
  const hrefFor = { 3: connId && `/connect/${provider}/${connId}/scope`, ...hrefs }
  return (
    <ol className="po-rail" aria-label="Setup steps" data-testid="step-rail">
      {STEPS.map((label, i) => {
        const n = i + 1, done = n < step, cur = n === step
        const inner = (
          <>
            <span className="po-rail-num">{done ? <IconCheck /> : n}</span>
            <span className="po-rail-label">{label}</span>
            {done && <span className="ds-sr"> (done)</span>}
          </>
        )
        const href = done ? hrefFor[n] : null
        return (
          <li key={label} data-testid={`step-rail-item-${n}`}>
            {href
              ? <Link className="po-rail-item is-done" to={href}>{inner}</Link>
              : <span className={`po-rail-item${done ? ' is-done' : ''}`} aria-current={cur ? 'step' : undefined}>{inner}</span>}
          </li>
        )
      })}
    </ol>
  )
}

export function TrustAside({ provider }) {
  const p = PROVIDERS[provider]
  return (
    <aside className="po-aside" aria-label="About this connection">
      <div className="ds-card"><div className="po-panel-body">
        <h2 className="po-aside-h">ACT will be able to read</h2>
        <ul className="po-list po-yes">{p.can.map((c) => <li key={c}><IconCheck /><span>{c}</span></li>)}</ul>
        <h2 className="po-aside-h">ACT can't</h2>
        <ul className="po-list po-no">
          <li><IconX /><span>Run, change or delete agents</span></li>
          <li><IconX /><span>{p.cant}</span></li>
          <li><IconX /><span>Write anything in {p.name}</span></li>
        </ul>
        <h2 className="po-aside-h">What ACT stores</h2>
        <p className="ds-helper po-flat">{p.stores} No passwords, keys or tokens. {p.name} trusts this Domino deployment's sign-in instead.</p>
        <h2 className="po-aside-h">Who can do this</h2>
        <p className="ds-helper po-flat">{p.who}. Not you? Use <strong>Send to my cloud admin</strong>; this page updates when they finish.</p>
      </div></div>
    </aside>
  )
}

export default function WizardFrame({ provider, connId, step, title, description, actions, children, hideAside, crumbs, hrefs }) {
  const p = PROVIDERS[provider]
  const h1 = React.useRef(null)
  // Focus the step heading on every step change (each step mounts its own frame) and start at the top.
  React.useEffect(() => {
    window.scrollTo(0, 0)
    h1.current?.focus({ preventScroll: true })
  }, [])
  return (
    <div className="ds-page ds-container po-page" data-testid={`wizard-${provider}`}>
      <header className="ds-page-header">
        <Breadcrumb items={crumbs || [{ label: 'Connections', to: '/connections' }, { label: `Connect ${p.name}` }]} />
        <div className="ds-page-header-row">
          <div className="po-head-text">
            <h1 className="ds-title po-title" ref={h1} tabIndex={-1} data-testid="step-heading">{title}</h1>
            {description && <p className="ds-description">{description}<span className="ds-sr"> Step {step} of 6.</span></p>}
          </div>
          {actions}
        </div>
      </header>
      <StepRail provider={provider} connId={connId} step={step} hrefs={hrefs} />
      <div className={hideAside ? 'po-wiz po-wiz-solo' : 'po-wiz'}>
        <div className="po-main">{children}</div>
        {!hideAside && <TrustAside provider={provider} />}
      </div>
    </div>
  )
}
