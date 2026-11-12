// "Keep {provider} in sync": Off or Daily at 02:00. The server stores "off" until a schedule is saved, so an unsaved
// connection shows Daily preselected (decision D-4); nothing runs until Save.
import React from 'react'
import { connectionsApi } from '../../api'

export const SCHEDULE_TIME = '02:00'

export function useSchedule(connId) {
  const [s, setS] = React.useState({ loading: true, error: false, schedule: 'off', saved: false, time: SCHEDULE_TIME })
  React.useEffect(() => {
    let live = true
    connectionsApi.schedule(connId).then(
      (r) => live && setS({ loading: false, error: false, schedule: r.schedule || 'off', saved: !!r.saved, time: r.time || SCHEDULE_TIME }),
      () => live && setS((x) => ({ ...x, loading: false, error: true })))
    return () => { live = false }
  }, [connId])
  return [s, setS]
}

export function scheduleText(s) {
  if (!s || s.loading) return '…'
  if (s.saved && s.schedule === 'daily') return `Daily at ${s.time}`
  if (s.saved) return 'Off'
  return 'Not scheduled'
}

export default function ScheduleCard({ connId, provider, sample, state, onSaved, suggestDaily = false, titleId = 'sy-h', className = '' }) {
  const [s, setS] = state
  const [pick, setPick] = React.useState(null)
  const [busy, setBusy] = React.useState(false)
  const [note, setNote] = React.useState('')
  const [err, setErr] = React.useState('')
  const value = pick || (s.saved ? s.schedule : suggestDaily ? 'daily' : null)
  const save = async () => {
    setBusy(true); setErr(''); setNote('')
    try {
      const r = await connectionsApi.saveSchedule(connId, value, s.time || SCHEDULE_TIME)
      setS({ ...s, schedule: r.schedule, saved: true, time: r.time, error: false })
      setPick(null)
      setNote(r.schedule === 'daily' ? `Saved. Next scan tomorrow at ${r.time}.` : 'Saved. Sync is off; scan whenever you like.')
      onSaved && onSaved(r)
    } catch (e) { setErr(e.message || "ACT couldn't save the schedule.") } finally { setBusy(false) }
  }
  return (
    <section className={`ds-card ${className}`} aria-labelledby={titleId}>
      <div className="po-panel-head"><h2 id={titleId}>Keep {provider} in sync</h2></div>
      <div className="po-panel-body">
        <fieldset className="po-fs" disabled={s.loading}>
          <legend className="ds-label">Scan for changes</legend>
          <div className="po-radios">
            {[['off', 'Off'], ['daily', `Daily at ${s.time || SCHEDULE_TIME}`]].map(([k, name]) => (
              <label key={k}>
                <input type="radio" name={`sync-${connId}`} value={k} checked={value === k} data-testid={`sync-schedule-${k}`}
                       onChange={() => { setPick(k); setNote('') }} />{name}
              </label>
            ))}
          </div>
        </fieldset>
        <p className="ds-helper po-sync-note">
          Runs as a scheduled Domino Job. New agents wait in <strong>Awaiting import</strong>; ACT never imports them on its own. Changes and disappearances become findings.
          {!s.saved && !s.loading && !suggestDaily && ' Not scheduled yet: pick Off or Daily, then save.'}
          {sample && ' This is sample data, so ACT stores your choice but no Job is created.'}
        </p>
        {s.error && <p className="po-fielderr" role="alert">ACT couldn't read the saved schedule. You can still save a new one.</p>}
        {err && <p className="po-fielderr" role="alert">{err}</p>}
        <div className="po-sync-row">
          <button type="button" className="ds-btn" data-testid="sync-save" disabled={busy || s.loading || !value} aria-busy={busy ? 'true' : undefined} onClick={save}>Save schedule</button>
          <span className="ds-helper po-flat" role="status" aria-live="polite">{note}</span>
        </div>
      </div>
    </section>
  )
}
