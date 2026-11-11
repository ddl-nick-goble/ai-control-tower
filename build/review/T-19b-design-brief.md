# T-19b design brief: Agent page and Run timeline (visual-reviewer, 2026-11-11)

Design source: the shipped system in frontend/src/design.css plus the approved Fleet page. Use only the `--ds-*` tokens and `ds-*` classes, with no new palette or radii. Rebuild Agent.jsx and Run.jsx on `ds-*` and retire the legacy Panel/.kv/.btn/.chip/.alert/.grid usage on these pages.

## Shared rules
- Shell: `ds-page` > `ds-breadcrumb` > `ds-page-header`; title `ds-title` (28/36); 1232px container.
- Spacing: 16px between cards, 12px inside lists, 8px between chips; `--ds-s*` only.
- Card: `ds-card` (12px radius, `--ds-shadow`). New compact head: 12px 16px padding, 15px/600 ds-ink title, 1px `--ds-line` divider, 16px body. Not `ds-section-head` (24px).
- Key/value: `dl` like `ds-summary-list`: 13px muted label in a 128px column, 14px/600 value, 8px row padding (~32px rows), hairline between rows, none after the last.
- Long IDs/ARNs: `ds-trunc` mono with `title`=full value + 24px ghost copy button (aria-label e.g. "Copy agent ID", check icon 1.5s on success, `ds-sr` role=status "Copied").
- Components: TierBadge, StatusPill, `ds-chip-tag` (source), `ds-btn`/`ds-btn-primary` (40px; 32px in card heads and rows), `ds-btn-ghost`, `ds-alert ds-alert-error`, `ds-skel`, `ds-empty`/`ds-empty-error`.
- Type: 14px body, 13px secondary, 12px uppercase labels (ds-table th style), tabular numerals for times and counts.
- A11y: `:focus-visible` 2px accent, AA contrast, no horizontal scroll at 1024/1440, `overflow-wrap:anywhere` for long text. Never mention Claude.
- Keep data-testids (S-6): tier-badge, agent-status, tier-card, criteria-item, tier-reasoning, identity-panel, manifest-tool, dependency-*, validation-card, bundle-id, policy-name, approvals-panel, approve-*, finding-item, finding-timeline-link, run-item, timeline-page, timeline-event* and data-* attributes; #evidence-pack-slot; button names "Approve" and "Approve as demo approver".

## Agent page
Header (`ds-page-header-row`):
- Left: `ds-title` name; meta row 8px below: source `ds-chip-tag`, TierBadge, StatusPill (testid agent-status), then 13px muted "Owner x · Last seen y".
- Right, one 8px-gap group of 32px buttons: Approve (`ds-btn-primary`), Generate evidence pack (`ds-btn`), demo actions (Approve as demo approver, Run recon agent) only when present.
- Move Approve/demo-approve out of the Approvals card; Approvals becomes read-only history; success/error alerts show under the header.
- Under 900px buttons wrap below the title. External agents: passport card full width right under the header.

Layout: two independent flex columns (not a row grid, so no height gaps).
- Left `minmax(0,1fr)`, right fixed 380px (360px at 1024), 16px gap.
- Left: Findings, Runs, Approvals, Validation, Governance.
- Right: Overview (Agent ID copyable, Source, Owner, Origin, Last seen), Tier and criteria, Identity binding, Permitted actions, Dependencies, Evidence pack.
- <900px single column: Overview, Findings, Tier, Runs, then the rest.
- Above the fold at 1440x900: header, Findings + Runs left, Overview + Tier right. No tabs.

Cards:
- Tier and criteria: badge + "OCC Bulletin 2026-54" on one line; 7 criteria rows at 28px (label, 56px 5-segment bar, number); score formula as a 12px muted caption inside a "How the score is computed" disclosure, closed by default (keep testid tier-reasoning on the text).
- Findings: list rows ≥48px with a 3px severity-coloured left rule. Line 1: severity pill, type chip, bold tool, status pill, right-aligned time (`ds-nowrap`). Line 2: 13px reason, clamped to 2 lines. Right edge: ghost "View run timeline →" Link (existing testid). "template text (no model)" becomes a small muted tag. Rows carry `id="finding-{id}"`.
- Runs: ds-table, columns Run (mono, truncated, copy), Started, Spans (right-aligned), chevron; 44px rows; whole row is the link (keep run-item).
- Dependencies/Governance/Validation: dl rows ("Models — name chip"); "none" inline muted; no subheadings for empty groups. Validation: 2-column stat grid (Mean, p5, p95, Pass rate) then dl rows Sample, Judge, Errors. Governance: policies as a bulleted list under a 12px label.
- Approvals: compact ds-table, 40px rows, or a one-line empty state.
- Evidence pack: keep EvidencePack in its slot; 1-line description + generated state.

States (each card loads independently):
- Loading: `ds-skel` lines (14px, 3 per card; 40px block for the header); no bare "Loading...".
- Empty: muted 14px sentence + hint line; no dashed boxes; 16px padding.
- Error: inline `ds-alert-error` in the card with a ghost "Retry". If the agent fetch fails: full-page `ds-empty-error` with a back link to Fleet.

## Run timeline
Header: breadcrumb (Fleet / Agent / Run xxxxxxxx), `ds-title` "Run timeline", 4-cell meta strip (12px uppercase label over 14px value, 24px gap):
- RUN: truncated id with the copy button hugging the text.
- AGENT: link.
- TIME RANGE: "2026-11-07 01:38 to 03:06 UTC · 1h 28m".
- EVENTS: "40 events · 2 violations" (violation count in danger text).
- 2x2 at 1024. Drop the redundant "Back to agent" link.

Toolbar: sticky (top:0, `--ds-bg` backdrop) segmented filter All / Input / Model call / Tool call / Violation, each with a count; `ds-segmented` look at 32px, active segment accent-filled; "Showing n of N" right-aligned (`ds-count`).

Timeline list:
- Grid: 72px timestamp column (mono 12px, right-aligned, tabular, muted) | 24px rail | content.
- Rail: 1px `--ds-line` line with a 20px type-icon node (white fill, 1px border): input neutral, model call accent, tool call slate, violation danger filled with a warning icon.
- No card per event: flat rows, 1px divider, 8px vertical padding; model-call rows 28px.
- Model call: one line "Model call · frontier model via gateway" (keep label() text); no duplicate "LLM call" chip.
- Tool call: line 1 type chip + bold tool name; line 2 payload.
- Payload: mono 12/18 on #f8f9fb, 1px `--ds-line` box, 6px radius; collapsed by default beyond 6 lines (max-height + bottom fade + ghost "Show N more lines", aria-expanded); 24px copy icon button in the box's top-right corner; ≤3-line payloads always open.
- Violation: `--ds-danger-bg` fill, 3px `--ds-danger` left rule, reason badge ("Violation: Over limit"), note ("Observed 4,750,000, limit 250,000"), "View finding" link to /agents/{id}#finding-{id}.
- Arriving with `?span=` (or from a finding) scrolls to the event, focuses it and flashes a highlight (reduced-motion safe). The finding's "View run timeline" link appends `?span={span_id}`.

States: loading = skeleton toolbar + 8 `ds-skel` rows; empty run = `ds-empty` "No events recorded for this run"; empty filter = "No {type} events in this run" + ghost "Clear filter"; error = `ds-empty-error` with retry and link back to the agent; findings failure keeps the inline `ds-alert-error` (findings-error) above the list.

## Defects in the current screenshots (must fix)
1. 2-up grid leaves large dead zones; page ~2230px tall at 1440.
2. Header: no owner or last-seen; actions not grouped; Approve duplicated in Approvals card.
3. Principal ARN cut off with no way to read it all.
4. Tier formula is a raw snake_case paragraph (+90px); 36px criteria rows too tall.
5. Dependencies: extra top whitespace, "none" takes whole rows, inconsistent uppercase subheadings.
6. Validation: bare empty line; populated card too tall.
7. Approvals and Evidence pack are full-width cards for one line of text.
8. Findings: timeline link is a tiny footer link; time far from reason; four chips in one line so severity is not dominant.
9. Runs: full 32-char id, no truncation/copy; squeezed date and span count.
10. Run header: copy icon floats away from the id; range repeats the date and has no duration; no violation count; strip not designed for 1024.
11. Payloads: 40-line payload expanded by default; copy button on its own row (~30px wasted); stranded unlabeled checkmark.
12. Run page ~5580px for 40 events (bordered card + copy row each); "Model call" chip repeated 15 times; violation has no link to its finding; arriving from a finding doesn't scroll to it.
13. Rail nodes are identical hollow circles; model call and tool call both accent purple.
14. Type filter has no counts and isn't sticky.
15. Bare "Loading ..." text on both pages; undesigned errors and page-level error.
