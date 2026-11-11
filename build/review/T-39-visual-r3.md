# T-39 design review r3 (captures in /mnt/data/ai-control-tower/qa/T-39-run3/): changes needed
## Must-fix
1. Loading (ST-7): settings-loading-1440.png is identical to the loaded page, even with /api/settings delayed ~3s. Make sure a skeleton (cards matching Demo mode / Platform credentials / Integrations, reserved min-height) renders while the request is pending, and verify it yourself with Playwright on a private port, holding the route open.
2. ST-2 text truncated: "Set · changed by owner.user on Nov …" is ellipsized inline beside Clear/Cancel (default-1024, replace-1440, saved-1440, clear-confirm-1440). Move it into the hint line under the label as one complete line; Replace/Clear/Cancel/Saved keep the input column.
## Polish
- Integrations: hints should fit on one line (rows 56-64px); badge right-aligned next to the input, not ~200px away.
- Google card: about 60px of dead space; even out its height.
- Sign-in state: add an action (e.g. a "Sign in" link to the Domino login, or a reload button) and 13px body text.
- Clear dialog: rename the red "Confirm" to "Clear key" (or "Clear").
