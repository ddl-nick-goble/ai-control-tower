# T-19b design review r3 (captures in /mnt/data/ai-control-tower/qa/T-19b-run3/captures/): changes needed
Fixed: r2 1 (View/Included in finding), 2 (Runs table), 9 (Fleet density, thead pinned under the banner).
## Must-fix (final round)
1. agent-loading-1440: the skeletons are taller than the loaded cards (Validation ~310 vs ~122px, Dependencies ~190 vs ~97px). Size each skeleton to its loaded height.
2. run-loading-1024: the capture showed a loaded page (possibly a capture timing issue; QA used time.sleep, which blocks Playwright's loop). Make sure a run-shaped skeleton renders while the timeline API is pending (breadcrumb, title, 4-cell meta, chips, timeline rows, sr-only "Loading run…"), and show nothing in the agent-name slot until it resolves (no placeholder "Agent").
3. fleet-banner-scrolled-1024: Name truncates to "mock-ag…" while Owner gets ~250px. Give Name min-width ~180px and let Owner shrink.
4. Demo banner toggle: its pill starts at x≈102 and the text at ~108, while the h1 and chips sit at 104 (fails test_t19a_shell_fleet::test_s1_demo_banner_above_and_aligned, 1.5px tolerance). Align the toggle so its left edge is on the content edge.
5. Fleet thead without the banner: test_t19a_shell_fleet::test_s2_table_sticky_header_hover_and_cells reports th at y=248 after scrolling (needs <120). Make sticky work with and without the banner (check the overflow ancestors and `top`).
## Polish
- Finding row: drop the inner border/radius inside the Findings card; keep the severity bar + divider.
- Stop stretching the last cards (align-items:start); balance the columns by card placement instead, or accept a small difference.
- Overview: one value weight; copy button inline at the end of the text so the owner shows more.
- Pinned thead: subtle bottom border/shadow.
