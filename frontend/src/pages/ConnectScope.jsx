// Step 3: Choose scope. The tree is built from discovery and everything with agents is selected by default.
import React from 'react'
import { Navigate, useNavigate, useParams } from 'react-router-dom'
import { connectionsApi } from '../api'
import { Alert } from '../components/form'
import WizardFrame from '../components/connect/WizardFrame'
import ScopeTree, { agentsOf, allLeaves, selectedIds } from '../components/connect/ScopeTree'
import { ErrorPanel, SampleBanner } from '../components/connect/bits'
import { PROVIDERS } from '../components/connect/providers'

const TREE_LABEL = {
  aws: 'AWS accounts and Regions', google: 'Google Cloud organization, folders and projects',
  microsoft: 'Microsoft subscriptions and Foundry projects', databricks: 'Databricks workspaces and catalogs',
  snowflake: 'Snowflake databases and schemas',
}
const hasPending = (nodes) => nodes.some((n) => n.pending || hasPending(n.children || []))
const PANEL = { aws: 'Accounts and Regions', google: 'Projects', microsoft: 'Subscriptions and projects', databricks: 'Workspaces and catalogs', snowflake: 'Databases and schemas' }

export default function ConnectScope() {
  const { provider, connId } = useParams()
  const p = PROVIDERS[provider]
  const nav = useNavigate()
  const [tree, setTree] = React.useState(null)
  const [error, setError] = React.useState(false)
  const [rev, setRev] = React.useState(0)
  const [sel, setSel] = React.useState(() => new Set())
  const [sample, setSample] = React.useState(false)
  const [saving, setSaving] = React.useState(false)
  const [saveError, setSaveError] = React.useState(false)

  React.useEffect(() => {
    let live = true
    setTree(null); setError(false)
    connectionsApi.scopes(connId).then(
      (r) => {
        if (!live) return
        const t = r.tree || []
        setTree(t)
        const flagged = new Set()
        const walk = (n) => { (n.children || []).forEach(walk); if (n.selected !== false) flagged.add(n.id) }
        t.forEach(walk)
        setSel(new Set(allLeaves(t).filter((l) => flagged.has(l.id)).map((l) => l.id)))
      },
      () => { if (live) setError(true) })
    connectionsApi.get(connId).then((c) => { if (live) setSample(!!c.sample) }, () => {})
    return () => { live = false }
  }, [connId, rev])

  if (!p) return <Navigate to="/connections" replace />
  const leaves = tree ? allLeaves(tree) : []
  const chosen = leaves.filter((l) => sel.has(l.id))
  const noun = p.leaf
  const plural = `${noun}s`
  const agentsChosen = chosen.reduce((s, l) => s + (l.agents || 0), 0)
  const empty = tree && (tree.length === 0 || (leaves.length === 0 && !hasPending(tree)))
  const allOn = leaves.length > 0 && chosen.length === leaves.length
  const next = `/connect/${provider}/${connId}/scan`

  const save = async () => {
    setSaving(true); setSaveError(false)
    try { await connectionsApi.saveScopes(connId, selectedIds(tree, sel)); nav(next) } catch { setSaveError(true); setSaving(false) }
  }

  return (
    <WizardFrame provider={provider} connId={connId} step={3} title="Choose scope"
                 description="ACT found these places to look. Everything with agents is selected. Counts come from a quick preview; the scan reads each agent in full.">
      {sample && <SampleBanner provider={p.name} />}
      {error && (
        <ErrorPanel title="ACT couldn't load the scope" onRetry={() => setRev((r) => r + 1)}>
          This is on ACT's side, not {p.name}'s. Try again in a moment.
        </ErrorPanel>
      )}
      {!error && !tree && (
        <section className="ds-card po-panel" aria-busy="true" data-testid="scope-loading">
          <div className="po-panel-head"><h2>Finding your {p.leaf === 'Region' ? 'accounts and Regions' : 'scopes'}…</h2></div>
          <div className="po-panel-body">{[0, 1, 2, 3].map((i) => <span key={i} className="ds-skel po-skel-row" />)}</div>
        </section>
      )}
      {empty && !error && (
        <div className="ds-card ds-empty" data-testid="scope-empty">
          <h2 className="ds-empty-title">No agents found in the accounts ACT can see</h2>
          <p className="ds-helper">If you expected some, check that the access you set up reaches those accounts, then check access again. You can also scan anyway: ACT will pick up agents that appear later.</p>
          <div className="po-empty-actions">
            <button type="button" className="ds-btn" onClick={() => nav(`/connect/${provider}/${connId}/access`)}>Check access</button>
            <button type="button" className="ds-btn ds-btn-ghost" onClick={() => nav(next)}>Scan anyway</button>
          </div>
        </div>
      )}
      {tree && !empty && !error && (
        <>
          <section className="ds-card po-panel" aria-labelledby="sc-title">
            <div className="po-panel-head">
              <h2 id="sc-title">{PANEL[provider]}</h2>
              <span className="ds-helper po-flat" data-testid="scope-summary" role="status" aria-live="polite">
                {chosen.length} of {leaves.length} {plural} selected · {agentsChosen} {agentsChosen === 1 ? 'agent' : 'agents'}
              </span>
            </div>
            <div className="po-panel-body">
              <div className="po-tree-bar">
                <span className="ds-helper po-flat">Unticking a scope skips it for this scan and for daily sync.</span>
                <button type="button" className="ds-btn ds-btn-ghost ds-btn-sm" data-testid="scope-select-all"
                        onClick={() => setSel(allOn ? new Set() : new Set(leaves.map((l) => l.id)))}>
                  {allOn ? 'Clear selection' : 'Select all with agents'}
                </button>
              </div>
              <ScopeTree tree={tree} sel={sel} setSel={setSel} label={TREE_LABEL[provider]} noun={plural} />
            </div>
          </section>
          {saveError && <Alert title="ACT couldn't save your selection">Nothing was lost. Try Continue again.</Alert>}
          <div className="po-footer">
            <div className="po-footer-l"><span className="ds-helper po-flat" id="sc-foot">{chosen.length ? 'The scan runs as a Domino Job. You can close the page while it runs.' : `Select a ${noun} to scan.`}</span></div>
            <div className="po-footer-r">
              <button type="button" className="ds-btn" onClick={() => nav(`/connect/${provider}/${connId}/access`)}>Back</button>
              <button type="button" className="ds-btn ds-btn-primary" data-testid="scope-continue" disabled={!chosen.length || saving}
                      aria-describedby="sc-foot" aria-busy={saving ? 'true' : undefined} onClick={save}>
                {chosen.length ? `Scan ${chosen.length} ${chosen.length === 1 ? noun : plural}` : `Select a ${noun} to scan`}
              </button>
            </div>
          </div>
        </>
      )}
    </WizardFrame>
  )
}
