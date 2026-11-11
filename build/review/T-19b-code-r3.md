# T-19b code re-review r3 (@da5ab4b): approve with follow-ups, no blockers
Follow-ups (fold into the final round only if one is needed for visual reasons):
1. design.css:97 `margin:-16px` on .ag-findings: use `.ag-card-body:has(> .ag-findings)` padding instead.
2. Agent.jsx:222: de-duplicate tool names with counts ("list_credentials ×2"); optionally link each violation via ?span=.
3. Run.jsx:308: onBlur bubbles, so guard the rt-landed ring with !e.currentTarget.contains(e.relatedTarget).
4. Run.jsx:264-265: fall back to violations[0].span_id when f.span_id is missing (as Agent.jsx does).
5. design.css:148: the banner toggle reads as an underlined link; restore the bold "Demo mode:" emphasis and button styling.
6. Check in the capture that the demo banner doesn't overlap the pinned Fleet thead.
