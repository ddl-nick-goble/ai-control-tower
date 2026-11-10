import React from 'react'
import { useNavigate } from 'react-router-dom'
import { registerExternal, previewTier } from '../api'
import { TierBadge } from '../components/ui'
import { Alert, Breadcrumb, Button, ChipInput, Field, PageHeader, Section, Segmented, Summary } from '../components/form'

// No GET /api/tiering/criteria route exists, so the seven names from config/tiering.yaml are listed here.
const CRITERIA = [
  ['decision_impact', 'Decision impact', 'How material are the decisions this agent makes or drives?'],
  ['financial_exposure', 'Financial exposure', 'How much money can it move or commit?'],
  ['customer_impact', 'Customer impact', 'How strongly can it affect customers?'],
  ['autonomy', 'Autonomy', 'How much does it act without human review?'],
  ['data_sensitivity', 'Data sensitivity', 'How sensitive is the data it can reach?'],
  ['reversibility', 'Reversibility', 'How hard is it to undo what it does?'],
  ['regulatory_exposure', 'Regulatory exposure', 'How much regulatory or legal exposure does it carry?'],
]
const SCOPE_SUGGESTIONS = ['accounts:read', 'accounts:write', 'payments:read', 'payments:write', 'customers:read', 'ledger:read']
const TIER_MEANING = {
  1: 'Highest consequence', 2: 'High consequence', 3: 'Moderate consequence', 4: 'Lowest consequence',
}

function parseUrl(v) {
  try {
    const u = new URL(v.trim())
    return (u.protocol === 'http:' || u.protocol === 'https:') && u.hostname ? u : null
  } catch { return null }
}

// Server errors in plain language; never raw JSON or status codes.
export function describeError(err) { return describeErrorInfo(err).message }

// urlField is true when the failure is about the address the user typed (so the field can be marked invalid).
export function describeErrorInfo(err) {
  const msg = (message, urlField = true) => ({ message, urlField })
  const m = String(err?.message || '').toLowerCase()
  const st = err?.status
  if (st === 401 || st === 403) return msg('You need to be signed in with permission to register agents.', false)
  if (m.includes('not allowed')) return msg('That address is not allowed. Agent cards must be served from an approved, publicly reachable host.')
  if (st === 504 || m.includes('timed out')) return msg('The agent card server took too long to respond. Check the address and try again.')
  if (m.includes('signature')) return msg('The agent card signature could not be verified, so the agent was not registered.')
  if (m.includes('http or https') || m.includes('invalid port')) return msg('That is not a valid agent card URL. Enter a full address starting with http:// or https://.')
  if (m.includes('a2a') || m.includes('json') || m.includes('1 mb') || m.includes('agent card does not contain')) {
    return msg('The document at that address is not a valid agent card. Check that the URL points to the agent card JSON.')
  }
  if (st === 502) return msg('We could not fetch an agent card from that address. Check the URL and that the server is reachable.')
  return msg('Registration did not complete. Please try again, and contact an administrator if it keeps happening.', false)
}

export default function Register() {
  const nav = useNavigate()
  const [url, setUrl] = React.useState('')
  const [urlError, setUrlError] = React.useState('')
  const [vals, setVals] = React.useState(() => Object.fromEntries(CRITERIA.map(([k]) => [k, 3])))
  const [scopes, setScopes] = React.useState([])
  const [busy, setBusy] = React.useState(false)
  const [error, setError] = React.useState(null)
  const [serverUrlError, setServerUrlError] = React.useState('')
  const [preview, setPreview] = React.useState(null)
  const [previewState, setPreviewState] = React.useState('loading') // loading | ready | error
  const inflight = React.useRef(false)

  const parsed = parseUrl(url)
  const host = parsed ? parsed.host : ''

  // Debounced, read-only tier preview; stale responses are ignored.
  React.useEffect(() => {
    let live = true
    setPreviewState((st) => (st === 'error' ? 'loading' : st === 'ready' ? 'stale' : st))
    const t = setTimeout(() => {
      previewTier(vals).then(
        (r) => { if (live) { setPreview(r); setPreviewState('ready') } },
        () => { if (live) { setPreview(null); setPreviewState('error') } })
    }, 250)
    return () => { live = false; clearTimeout(t) }
  }, [vals])

  const validate = () => {
    if (!url.trim()) { setUrlError('Enter the agent card URL.'); return }
    setUrlError(parsed ? '' : 'Enter a valid http:// or https:// URL, for example https://agent.example.com/.well-known/agent-card.json.')
  }

  const submit = async (e) => {
    e.preventDefault()
    if (!parsed || inflight.current) return
    inflight.current = true
    setBusy(true); setError(null); setServerUrlError('')
    try {
      const agent = await registerExternal({ card_url: url.trim(), criteria: { ...vals, data_scopes: scopes } })
      nav(`/agents/${encodeURIComponent(agent.id)}`)
    } catch (err) {
      const info = describeErrorInfo(err)
      setError(info.message)
      if (info.urlField) setServerUrlError(info.message)
      setBusy(false); inflight.current = false
    }
  }

  return (
    <div data-testid="register-page" className="ds-page ds-container">
      <PageHeader
        breadcrumb={<Breadcrumb items={[{ label: 'Fleet', to: '/' }, { label: 'Register external agent' }]} />}
        title="Register external agent"
        description="Registering fetches the agent's A2A agent card, checks its signature and creates a record in Pending review."
        descriptionTestId="register-description" />
      <div className="ds-layout">
        <form className="ds-form" aria-label="Register external agent" onSubmit={submit} noValidate>
          {error && <Alert title="Registration failed" data-testid="register-error">{error}</Alert>}

          <Section number={1} title="Agent card" helper="Where the agent publishes its A2A agent card. We read it once to build the record.">
            <Field label="Agent card URL" help="Use the full address of the card, usually ending in /.well-known/agent-card.json."
                   helpTestId="card-url-help" error={urlError || serverUrlError} errorTestId="card-url-error">
              {(p) => (
                <input {...p} type="url" className="ds-input" data-testid="card-url" value={url} inputMode="url"
                       spellCheck={false} autoComplete="off"
                       placeholder="https://agent.example.com/.well-known/agent-card.json"
                       onChange={(e) => { setUrl(e.target.value); setServerUrlError(''); if (urlError && parseUrl(e.target.value)) setUrlError('') }}
                       onBlur={validate} />
              )}
            </Field>
          </Section>

          <Section number={2} title="Consequence assessment" helper="Score what could go wrong if this agent misbehaves, from 1 (low) to 5 (high). Only consequences count, not test results.">
            <div className="ds-criteria">
              {CRITERIA.map(([k, name, q]) => (
                <Segmented key={k} name={name} question={q} value={vals[k]} testid={`criterion-${k}`}
                           rowTestId={`criterion-row-${k}`} onChange={(n) => setVals((v) => ({ ...v, [k]: n }))} />
              ))}
            </div>
          </Section>

          <Section number={3} title="Data access" helper="List the data scopes this agent can reach. Reviewers see them on the agent's record.">
            <ChipInput label="Data scopes" testid="data-scopes" value={scopes} onChange={setScopes}
                       suggestions={SCOPE_SUGGESTIONS} placeholder="Type a scope and press Enter"
                       help="Press Enter or comma to add a scope. Optional." />
          </Section>

          <footer className="ds-form-footer">
            <Button variant="secondary" disabled={busy} onClick={() => nav('/')}>Cancel</Button>
            <Button type="submit" variant="primary" busy={busy} disabled={!parsed || busy}>
              {busy ? 'Registering…' : 'Register agent'}
            </Button>
          </footer>
        </form>

        <div className="ds-side">
          <Summary title="Summary" testid="register-summary">
            <div className={`ds-summary-tier${previewState === 'stale' ? ' ds-dim' : ''}`} aria-busy={previewState === 'stale' ? 'true' : undefined}>
              {preview ? <TierBadge tier={preview.tier} /> : <span className="ds-skeleton" aria-hidden="true" />}
              <span className="ds-summary-tier-text">
                {preview ? TIER_MEANING[preview.tier] : previewState === 'error' ? 'Preview unavailable' : 'Calculating…'}
              </span>
            </div>
            <dl className="ds-summary-list">
              <div><dt>Weighted score</dt><dd data-testid="summary-score" className={previewState === 'stale' ? 'ds-dim' : ''}>{preview ? `${preview.score.toFixed(2)} / 5` : '–'}</dd></div>
              <div><dt>Card host</dt><dd data-testid="summary-host" className={host ? '' : 'ds-faint'}>{host || 'Not set'}</dd></div>
              <div><dt>Data scopes</dt><dd data-testid="summary-scopes-count">{scopes.length}</dd></div>
            </dl>
            <p className="ds-helper">The tier is a preview. It is confirmed when the agent is registered, and the agent starts in Pending review.</p>
          </Summary>
        </div>
      </div>
    </div>
  )
}
