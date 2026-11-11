# T-19b code re-review (@44d499b): changes needed. All round-1 code items fixed.

## Blocker
- frontend/src/components/ui.jsx:56-63 DemoBanner: the delta removed the span "Demo mode: simulated data and stand-ins" (id demo-stand-ins, class demo-panel-title), which was the only place the banner had that exact string. That breaks D-9 and tests/e2e/test_t15_demo_mode.py:147. Fix: the banner must contain "Demo mode: simulated data and stand-ins" exactly once AND not repeat "Demo mode: simulated data" (test_co_demo_banner_not_repeating_text). Make the collapsed and expanded headings one element whose text is the D-9 string, and drop the second heading line. Keep the edit inside DemoBanner.

## Minor
- Run.jsx:228: the span-missing notice is never cleared; clear it on the next navigation.
- EvidencePack.jsx:31: smooth scroll ignores prefers-reduced-motion; use 'auto' when the user prefers reduced motion.
- EvidencePack.jsx:32 / agentui.jsx:67: make sure .ag-card shows a visible focus ring on programmatic focus.
- agentui.jsx:94: keep one persistent live region for "Copied"/"Copy failed".
- Run.jsx:259-261: memoize clean(fmtJson(...)) for large payloads.
- .rt-flash outline lasts about 2.6s; keep a static focus outline while the li has focus.
