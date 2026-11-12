// HandshakePanel (design spec 3.2, 4.4): one primary action, a copyable artifact, a live listener (no "I'm done"
// button), a paste-anything field and the "Other ways to connect" disclosure.
import React from 'react'
import { Link } from 'react-router-dom'
import { connectionsApi } from '../../api'
import { IconAlert, IconCopy, IconExt, IconInfo, IconCheck, Rich, useCopy, useInterval } from './bits'
import { PROVIDERS, spaced } from './providers'

function fmtElapsed(s) { return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}` }

export function Listener({ provider, status, since, found }) {
  const p = PROVIDERS[provider]
  const [now, setNow] = React.useState(() => Date.now())
  useInterval(() => setNow(Date.now()), status === 'waiting' ? 1000 : null)
  const elapsed = since ? Math.max(0, Math.round((now - since) / 1000)) : 0
  const long = status === 'waiting' && elapsed > 600
  let body
  if (status === 'detected') {
    body = found?.role
      ? <><strong>Role detected.</strong> {found.role}{found.account ? ` in account ${spaced(found.account)}` : ''}. Checking access…</>
      : <strong>{p.detected}</strong>
  }
  else if (status === 'waiting') {
    body = <><strong>{p.waiting[0]}</strong> {p.waiting[1]}{long ? ` Haven't seen it yet. Check the ${p.name} tab for a red status, or paste the value below.` : ''}</>
  } else body = p.idle
  return (
    <div className={`po-listen is-${status === 'waiting' ? 'live' : status === 'detected' ? 'ok' : 'idle'}`} data-testid="handshake-listener">
      <span className="po-pulse" aria-hidden="true" />
      <div className="po-listen-text" data-testid="handshake-listener-status" aria-live="polite" aria-atomic="true">{body}</div>
      {status === 'waiting' && <span className="po-listen-time" aria-hidden="true">{fmtElapsed(elapsed)}</span>}
    </div>
  )
}

// Paste-anything: the API classifies as the user types (250 ms debounce); no submit button.
function PasteAnything({ provider, connId, onHold, onUse }) {
  const p = PROVIDERS[provider]
  const cfg = p.paste
  const [text, setText] = React.useState('')
  const [res, setRes] = React.useState(null)
  const seq = React.useRef(0)
  const helpId = React.useId(), resId = React.useId()
  React.useEffect(() => {
    const mine = ++seq.current
    if (!text.trim()) { setRes(null); onHold(false); return undefined }
    const t = setTimeout(() => {
      connectionsApi.paste(connId, text).then(
        (r) => { if (mine === seq.current) { setRes(r); onHold(!!r.recognised) } },
        () => { if (mine === seq.current) { setRes({ failed: true }); onHold(false) } })
    }, 250)
    return () => clearTimeout(t)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [text, connId])

  let view = null, invalid = false
  if (res) {
    if (res.failed) {
      invalid = true
      view = <p className="po-parse-err" data-testid="paste-anything-error">ACT couldn't check that just now. Try pasting it again.</p>
    } else if (res.recognised) {
      const aws = res.kind === 'aws_role_arn' && res.account
      const role = aws ? res.value.split('/').pop() : null
      view = (
        <div className="po-parse-card">
          <IconCheck />
          <span className="po-parse-text"><strong>Recognized:</strong> {aws ? <>role <strong>{role}</strong> in AWS account <strong>{spaced(res.account)}</strong>.</> : String(res.message || '').replace(/^Recognized:\s*/, '')}</span>
          <button type="button" className="ds-btn ds-btn-primary ds-btn-sm" data-testid="paste-anything-use" onClick={onUse}>Use this</button>
        </div>
      )
    } else if (res.hint === 'use_other') {
      view = <p className="po-parse-hint">That is an A2A agent card URL. <Link to="/connections/add-by-url">Add it from Other: add an agent by URL.</Link></p>
    } else if (res.partial === 'aws_account_id') {
      view = <p className="po-parse-hint">That looks like an AWS account ID. ACT needs the role too: paste the role ARN from the stack's Outputs tab.</p>
    } else if (res.wrong_provider) {
      const other = PROVIDERS[res.provider]
      view = <p className="po-parse-hint">That looks like a {other ? other.name : 'different provider'} value. {other ? <Link to={`/connect/${res.provider}`}>Connect {other.name} instead.</Link> : null}</p>
    } else {
      invalid = true
      view = <p className="po-parse-err" data-testid="paste-anything-error">ACT couldn't recognize that. Paste the role ARN (arn:aws:iam::…:role/ACT-ReadOnly), a Finish or Return link, a connection code, or an account URL.</p>
    }
  }
  return (
    <>
      <div className="po-divider" aria-hidden="true">or paste</div>
      <label className="ds-label" htmlFor={`${helpId}-in`}>{cfg.label}</label>
      <p className="ds-helper po-flat" id={helpId}>{cfg.help}</p>
      <input id={`${helpId}-in`} className="ds-input po-paste" data-testid="paste-anything-input" autoComplete="off" spellCheck={false}
             placeholder={cfg.ph} value={text} onChange={(e) => setText(e.target.value)}
             aria-invalid={invalid ? 'true' : undefined} aria-describedby={`${helpId} ${resId}`} />
      <div className="po-parse" id={resId} data-testid="paste-anything-result" aria-live="polite">{view}</div>
    </>
  )
}

function OtherWays({ provider, artifact, conn }) {
  const p = PROVIDERS[provider]
  const fileName = p.templateName || `act-${provider}-setup.txt`
  const href = React.useMemo(() => {
    if (!artifact) return null
    if (provider === 'aws' && conn?.state_token) return `api/connections/template?state=${encodeURIComponent(conn.state_token)}`
    return `data:text/plain;charset=utf-8,${encodeURIComponent(artifact.text)}`
  }, [artifact, conn, provider])
  return (
    <details className="po-more" data-testid="other-ways">
      <summary data-testid="other-ways-toggle">Other ways to connect</summary>
      <ul className="po-list po-other">
        {href && (
          <li><IconInfo /><span><a href={href} download={fileName} data-testid="template-download">Download the {provider === 'aws' ? 'template' : 'setup script'}</a> to review it before anyone runs it. It sets up the same read-only access.</span></li>
        )}
        {p.other.map((o) => (
          <li key={o.id} data-testid={o.id} className={o.secret ? 'is-secret' : undefined}>
            {o.secret ? <IconAlert /> : <IconInfo />}
            <span>{o.text}{o.secret && <> <strong className="po-notrec">Not recommended</strong>: {o.reason}</>}</span>
          </li>
        ))}
      </ul>
      {provider === 'aws' && <pre className="po-policy" aria-label="Permissions the role grants">{p.policy}</pre>}
    </details>
  )
}

export default function HandshakePanel({ provider, conn, found, listener, since, onPasteHold, onUse }) {
  const p = PROVIDERS[provider]
  const artifact = conn?.artifact
  const { copy, note, last } = useCopy()
  const [opened, setOpened] = React.useState(false)
  const text = artifact?.text || ''
  const adminText = provider === 'aws' && artifact?.open_url ? artifact.open_url : text
  const openAndCopy = async () => {
    if (!p.openOnly) await copy(text, 'primary')
    if (artifact?.open_url && !p.openOnly) window.open(artifact.open_url, '_blank', 'noopener')
    setOpened(true)
  }
  return (
    <section className="ds-card po-panel" aria-labelledby="hs-title" data-testid="handshake-panel">
      <div className="po-panel-head"><h2 id="hs-title">{p.panel}</h2><span className="ds-helper po-flat">{p.time}</span></div>
      <div className="po-panel-body">
        <ol className="po-hs-steps">{p.steps.map((s) => <li key={s}><span><Rich text={s} /></span></li>)}</ol>
        <div className="po-hs-actions">
          {p.openOnly ? (
            <a className="ds-btn ds-btn-primary" href={artifact?.open_url} target="_blank" rel="noopener noreferrer"
               data-testid="handshake-primary" onClick={() => setOpened(true)}>
              {p.primary}<IconExt /><span className="ds-sr">(opens in a new tab)</span>
            </a>
          ) : (
            <button type="button" className="ds-btn ds-btn-primary" data-testid="handshake-primary" onClick={openAndCopy}>
              {p.primary}{artifact?.open_url && <IconExt />}{artifact?.open_url && <span className="ds-sr">(opens in a new tab)</span>}
            </button>
          )}
          <button type="button" className="ds-btn" data-testid="handshake-send-admin" onClick={() => copy(adminText, 'admin')}>
            <IconCopy />{last === 'admin' ? 'Copied' : 'Send to my cloud admin'}
          </button>
        </div>
        <div className="po-cmd">
          <pre data-testid="handshake-command" aria-label="Setup artifact" tabIndex={0}>{text}</pre>
          <button type="button" className="ds-btn po-copy" data-testid="handshake-copy" onClick={() => copy(text, 'cmd')}>
            <IconCopy />{last === 'cmd' ? 'Copied' : 'Copy'}
          </button>
        </div>
        <p className="po-cmd-cap">{p.caption}</p>
        {artifact?.note && provider === 'aws' && <p className="po-cmd-cap">{artifact.note}</p>}
        {p.script && (
          <details className="po-more">
            <summary data-testid="handshake-script-toggle">Read what the {provider === 'snowflake' ? 'worksheet' : provider === 'databricks' ? 'script' : 'command'} does</summary>
            <pre>{p.script}</pre>
          </details>
        )}
        {!p.noListener && <Listener provider={provider} status={opened && listener === 'idle' ? 'waiting' : listener} since={since} found={found} />}
        <PasteAnything provider={provider} connId={conn.id} onHold={onPasteHold} onUse={onUse} />
        <OtherWays provider={provider} artifact={artifact} conn={conn} />
        <div className="ds-sr" role="status" aria-live="polite">{note}</div>
      </div>
    </section>
  )
}
