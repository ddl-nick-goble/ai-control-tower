# T-19b final code review (@8e10657): approve with follow-ups, no blockers
r3 follow-ups 1-5 done (2: counts done, optional ?span= links not). dist matches sources. No Claude mention.
## Major follow-up
- design.css:444 `.ds-table-wrap { min-height: 100vh }` (not calc as reported): adds >= one viewport of blank space under short Fleet tables and seems to exist only to let test_s2_table_sticky_header_hover_and_cells scroll a short table (sticky th is bounded by the table, so it doesn't extend the pinned range). Fix: drop it; have the test seed enough rows or use a smaller viewport. Raise with test owner.
## Minors
- design.css:442 `.ds-table th:nth-child(2) { min-width:180px }` is a no-op under table-layout:fixed and also hits Agent Runs/Approvals tables; `.c-name-h` is dead. Scope to col.c-name.
- design.css:443 global `.ds-table th` box-shadow hits the non-pinned Agent tables; scope to `.ds-table-card > .ds-table th`.
- design.css:254 `.ag-ph { display:block }` purposeless; old `.ag-cols` rules layered instead of deleted.
- Agent.jsx PageSkel hard-coded skeleton heights are fragile.
