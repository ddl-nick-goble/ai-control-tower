# T-19b design review, round 1 (captures in /mnt/data/ai-control-tower/qa/T-19b-run1/captures/): changes needed

Brief defects fixed: 2, 3, 4, 6, 10, 13, 14. Mostly fixed: 7, 12. Partly fixed: 1, 5, 8, 9. Not shown in the captures (QA will re-capture): 11, 15.

## Major (builder)
1. Defect 1, columns unbalanced (agent-tier1-findings-1440, agent-empty-1440): the left column ends about 630-700px above the right, leaving a dead block under Governance. Rebalance (e.g. Dependencies and Evidence pack go left under Governance, or Governance and Validation go right) so both columns end within about 100px of each other, for both a busy agent and an empty one.
2. Defect 9, Runs table: the cells collide ("01:38 UTC40 spans"), the Run column is too wide and Spans is left-aligned. Use fixed column widths, right-align Spans with 16px+ padding, and size Run to the truncated id.
3. Run page: 2 violations each have a "View finding" link, but the agent lists only 1 finding, so one link points at a missing #finding- anchor. Show "View finding" only when a finding exists for that violation (match on span/finding id).

## Minor (builder)
6. Findings row: the time sits mid-row. Put it right-aligned in a ds-nowrap cell; "View run timeline →" becomes a ds-btn-ghost at the right edge.
7. At 1024 the 2-line clamp cuts the key number ("limit 25000…"). Allow 3 lines at narrow widths.
8. Dependencies: three "none" rows (~140px). Show one muted "None declared" line or inline dl rows. "Permitted actions" mixes an uppercase TOOLS subheading with dl rows; use one label style.
9. Long values break mid-string at 1024: Data scopes ("treasury.nostro_balanc / es", orphaned comma) and the owner email. Render scopes as chips or one per line; use ds-trunc + title for the email.
10. At 1024 the header meta line "Owner … Last seen" wraps indented about 4px off the chips. Align it to the title edge.
11. Dependencies Models row: the chip and "registry version 5 · champion" wrap unevenly. Put them on two deliberate lines.
12. Tool-call payloads of 3 lines or fewer still render as 3-line pretty JSON boxes (~83px/row). Render short payloads as compact single-line JSON. Drop the stray leading "·" on model-call rows.
13. Sticky toolbar: rows show through underneath. Give it a solid --ds-bg background and a 1px --ds-line bottom border.
14. Empty values render as "-". Use a muted em dash.

## For QA re-capture (not builder)
4. Loading/error states weren't captured (the "loading" capture shows a loaded page). Re-capture with a delayed and a failed request.
5. No payload longer than 6 lines was captured. Capture one collapsed and expanded.
