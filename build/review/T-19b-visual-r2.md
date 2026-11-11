# T-19b design review, round 2 (captures in /mnt/data/ai-control-tower/qa/T-19b-run2/captures/): changes needed
Fixed: 6, 8, 9, 10, 12, 14. Mostly fixed: 1. Partly fixed: 2. Not fixed: 3. Loading/error/long-payload states now exist and are mostly good.

## Major
1. run-default-1440, run-long-payload-collapsed-1440: both violations still show "View finding →" but the agent has only one finding (transfer_funds), so the list_credentials violation at 02:18:40 links to a missing anchor. Check the real data: compare the run's timeline violation events with GET findings for the agent, and find out how the violation is built. Render the link only when a finding matches that violation (span/finding id); otherwise show a muted "No finding raised".
2. agent-tier1-findings-1440: Runs table: STARTED is truncated ("2026-11-07 01:38 …") at 1440 although there's room, while Run takes ~500px. Use fixed widths: Run sized to the truncated id with copy, Started ds-nowrap with the full timestamp, Spans right-aligned. Never ellipsize a timestamp.

## Minor
3. agent-card-error-1024: "Try again" is bold text ~4px below the message baseline. Make it a small ds-btn ghost, vertically centred. Show "–" in the Findings count badge in the error state.
4. run-loading-1024: generic grey bars. Shape the skeleton like the run page (breadcrumb, title, 4-cell meta row, filter chips, timeline rows with time gutter and icon rail), with an sr-only "Loading run…".
5. agent-loading-1440: the Validation/Dependencies skeletons are taller than their loaded forms (~70px shift). Match the loaded heights.
6. agent-empty-1440: the left column still ends ~140px above the right. Rebalance (e.g. move Evidence pack right or Identity binding left). Consider merging the empty Approvals and Validation cards into one card with two rows.
7. Overview card: the label column is wider than needed and the owner email is truncated. Use a ~96px label column, with title/copy on truncated values.
8. Findings row: a bordered card inside a titled card is a double frame. Drop the inner border; keep the severity bar plus a row divider.
9. Fleet rows are ~56px, so tighten to ~44px to meet the dense bar. Make sure the demo banner, when present, doesn't overlap the pinned thead.
