# T-19b code review, round 1 (@061ab81): changes needed

## Blocker
- frontend/src/pages/Run.jsx:~120 and ~215 (`tl.error && !tl.data`): useAsync keeps the previous data when a request fails, and Run has no key={runId}. If run B fails to load after run A, the page shows run A's events under run B's URL. The retry path (setRev) has the same problem. Fix: render ErrorPage on tl.error alone, or mount `<Run key={runId}>` as Agent does.

## Major
- Agent.jsx:~79 (Manifest value limits, nf(v.amount)/nf(v)): nf goes through Number(), so strings over 15 digits lose precision, trailing zeros drop ("1,234.50" becomes "1,234.5") and non-numeric strings show "NaN". Fix: move Run.jsx's string-based num() to format.js and use it here.
- EvidencePack hook: the refactor is sound, but the result lands in the side column, off-screen from the header button. The only announcement is the Skel role=status, which unmounts when generation finishes. Fix: on success, scroll the card into view or focus it, and add a persistent aria-live "Evidence pack ready" message.

## Minor
- agentui.jsx:~15 writeClipboard: the execCommand fallback takes focus and doesn't restore it, and a failed copy gives no feedback. Restore document.activeElement and show "Copy failed".
- Agent.jsx:~320 #finding- effect: the hash is never cleared, so a retry scrolls and flashes again. After the flash, call navigate({hash:''},{replace:true}).
- Run.jsx:~140 ?span=: when the span isn't found the param is dropped silently; show a notice. setParams({}) clears every other query param; remove only span. Programmatic focus on li tabIndex=-1 may not show a focus ring after mouse navigation; ensure a visible highlight.
- Run.jsx:~75 Payload: a single huge wrapped line never collapses; also collapse when text.length > ~400. The copy button should copy the full payload, not the cleanText-truncated one. Add aria-controls to the toggle.
- Run.jsx:~190 isoTime: show "UTC" (in the column header or the meta strip) so bare HH:MM:SS is unambiguous.
- Agent.jsx:~310: a failed refetch with existing data is swallowed silently; show an inline error.
- Dead legacy CSS in styles.css (.tl-event, .timeline, .findings, .wrap-id, .kv): remove what these pages no longer use (styles.css is now allowed for removals). Passport still uses .panel, so move it to ds-card.
- .ag-copy hover #eceff3: use a token.
