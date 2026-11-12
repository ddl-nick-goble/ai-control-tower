# T-19b design review r4 (final, pre-merge) @a5b5d20
Verdict: approve with follow-ups + 1 blocker. S-3 pass, S-4 pass (multi-line payload collapse not shown in captures), S-5 fails on the blocker only.
Blocker: Runs table at 1024: "40 spans" collides with the row chevron ("40 spans›") - qa/T-19b-run4/captures/agent-tier1-findings-1024.png. Needs right padding on Spans or a fixed chevron column.
Follow-ups: (1) highlighted violation row (02:14) lacks the red left bar - timeline capture; (2) empty agent page columns unbalanced (~190px) - agent-empty-1440.png; (3) Runs thead fill not uniform x=610-890 - agent-tier1-findings-1440.png; (4) card-level "Try again" should be a ghost button - agent-card-error-1440.png; (5) confirm focus rings on filter chips/copy buttons.
r3 #1 (skeleton heights) resolved; r3 #2-#5 covered by tests, not visible in these captures.
