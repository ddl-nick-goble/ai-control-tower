# T-39 design review, round 2 (captures in /mnt/data/ai-control-tower/qa/T-39-run2/): changes needed
Round 1 fixed: nesting, Save buttons, validation, repeated "Stored encrypted" line, ACT_SECRET_KEY code style. Partly fixed: height (still ~2,430px at 1440; target 1,600-1,900), missing states, placeholders, Demo row. Not fixed: helper text size.

## Major
1. Loading state: the capture shows the loaded page. Make sure a real skeleton exists (cards matching the sections, reserved min-height) and shows while /api/settings is pending.
2. The Platform credentials grid leaves about 230px of dead space under Google (1 field) beside AWS (4 fields), and every platform field is a full-width ~72px stacked row. Pair fields two per row (role ARN + external ID, key ID + secret; Microsoft/Databricks client ID + secret) so card heights even out.

## Minor
1. "Set · changed by <user> on <date>" wraps with an orphan leading dot under the badge, cramped beside Clear/Cancel. Put it in the hint line under the label as one non-wrapping line, using that exact ST-2 wording.
2. Demo mode card: the title and row label both say "Demo mode", and "Off" shows both beside the switch and as a badge. Drop the duplicates.
3. Inconsistent section styles: Platform credentials is a bare heading with sub-cards; platform titles are uppercase and letter-spaced while the others are sentence case. Use one title style.
4. Integrations rows: ~325px inputs leave 100px+ gaps before the badges; the 240px label column forces 3-line hints, so rows are 72-88px. Widen the inputs and rebalance the columns to hit 56-64px rows.
5. At 1024 the sticky chip row must stay pinned under the header with an opaque background and must not cover fields.
6. The load-error and no-access panels need an icon (the load error with danger treatment); fix the orphan "a change.".
7. Helper/hint text is still ~11-12px grey; use 13px with the helper token, AA on #fff.
