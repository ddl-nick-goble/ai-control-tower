# T-39 design review, round 1 (screenshots in /mnt/data/ai-control-tower/qa/T-39-run1/): changes needed

The page is a single 760px column with every field stacked. It is about 3,630px tall at both 1440 and 1024. It doesn't meet the density and completeness bar.

## Layout direction (do this)
- Full shell width, like Fleet (about 1232px at 1440). Drop the `.st-stack` 760px cap.
- A sticky left section nav of about 200px, with anchors for Demo mode, Platform credentials (AWS, Google, Microsoft, Databricks), Integrations and Access. At 1024 it collapses to a horizontal chip row above the content.
- Each section is a `ds-card` laid out as a settings table: the left column (about 240px) holds the label and hint, the right column the control plus a status badge. Target 56-64px per row, giving a page of about 1,600-1,900px.
- One card per platform, with a header row: platform name on the left, a "Configured"/"Not configured" badge on the right. No nested borders (no `.st-block` and no default fieldset legends). Pair related credentials, such as client ID and secret, in a two-column grid. Short Integrations fields go in a two-column grid.

## Major
1. Dead space: about 470px on the right at 1440 and about 240px at 1024. Each field is an ~85px label/input/hint stack.
2. Triple nesting in Platform credentials: card, then .st-block, then field, with fieldset legends sitting on the border line.
3. 22 Save buttons, enabled even on empty fields. Show Save only when the field is dirty, as `ds-btn primary`; otherwise disabled or hidden. Alternatively, one Save per card for its plain fields.
4. Status is inconsistent. Move it to the label row, right-aligned, as one badge: grey "Not set", green "Set" with "changed by … on …" in the hint, and neutral lock-icon "Set by project variable". The control row has the same width in every state, including locked fields.
5. Loading is three thin bars, then a jump to about 3,600px. Use skeleton cards matching the real sections (a title plus 3 field rows each) with a reserved min-height.
6. Validation error is a small raw lowercase server string under the hint. Add a danger border and aria-invalid, an inline icon, and a sentence-case message that replaces the hint, linked by aria-describedby.
7. Missing states: a load-failure panel with Retry; a Replace flow with Cancel; Clear must confirm through the designed ConfirmDialog.

## Minor
1. Replace and Clear look inconsistent. Make Replace a `ds-btn` secondary and Clear a danger-text/ghost button.
2. "Stored encrypted. Replace or clear it later." repeats on 7 fields. Say it once in the section subtitle.
3. Placeholders ("gateway", "ann, carl", "cards.example.com") look like real values and are low contrast. Use "Default: gateway"-style hints and AA-passing placeholders, applied consistently.
4. Demo mode: the title, switch label and two description lines all say the same thing. Make one row, "Demo mode", with the switch and an On/Off label and one description line, and drop the card subtitle.
5. ACT_SECRET_KEY in the warning: put it in `<code>` mono and keep the warning to one line where possible.
6. Demo dialog: confirm the scrim covers the viewport when scrolled, focus is trapped, and Esc closes it.
7. Helper text is about 11-12px grey, which is borderline AA. Use 13px with the helper token, checked against #fff.
