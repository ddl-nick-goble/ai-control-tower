# Design spec: Connect a provider, then intake agents

Status: design for build. Owner: product design. Date: 2026-11-12.
Prototype: `build/design/provider-onboarding.html` (static, no network; open the file and use the hashes below).
Sources: `build/research/providers/*.md` (AWS, Google Vertex, Google agents, Microsoft, Databricks, Snowflake), `build/import-wizard-plan.md` §2, `frontend/src/design.css`.

## Prototype hashes (screenshot by URL)

| Hash | Screen |
|---|---|
| `#gallery` | Connections gallery: Domino strip plus 6 cards in every state (not connected, needs attention, setup pending, sample data, other) |
| `#aws-1` | AWS step 1, Connect: coverage choice and handshake panel, idle |
| `#aws-1-waiting` | AWS step 1, live listener. Detects the role after about 4 s, then moves to step 2 on its own |
| `#aws-1-failed` | AWS step 1, stack failed (`iam:CreateRole` denied) |
| `#aws-2` | Check access: checks tick in one at a time, all pass |
| `#aws-2-partial` | Check access with one optional permission missing, plus its fix |
| `#aws-3` | Choose scope: account and Region tree, one account still waiting for its role |
| `#aws-4` | Scan: streaming counters, scope list and agent feed (Domino Job) |
| `#aws-5` | Intake review table |
| `#aws-5-why` | Review table with one row expanded: per-criterion "why", diff, tier override |
| `#aws-6` | Import summary and sync schedule |
| `#google-1` | Google Cloud handshake (Cloud Shell command) |
| `#microsoft-1`, `#databricks-1`, `#snowflake-1` | Handshake step for the other providers |
| `#other` | Add one agent by A2A card URL (today's flow, step 1) |
| `#connection-aws` | Connection detail, healthy, with the Awaiting import queue |
| `#connection-google` | Connection detail, needs attention |
| `#sample-review` | Review table on the sample tenant, labelled Sample data |
| `#fleet` | Fleet header with the new primary action and the awaiting-import banner |

---

## 1. Principles

1. **No typing where ACT can work it out.** The only typed field in the whole feature is the A2A URL under Other. Everything else is one click, one paste, or a pick from a list ACT built.
2. **ACT watches; the user doesn't report back.** Wherever ACT can poll, it polls. No "I'm done" buttons.
3. **Say what will be read, and what won't, before asking for trust.** Every handshake step shows "ACT will be able to read / ACT can't / What ACT stores / Who can do this".
4. **Secretless first, secrets last and labelled.** Key or password options sit under "Other ways to connect" and say "Not recommended" with the reason.
5. **Nothing enters the record without a person.** Scans stage results. Import is explicit. Sync puts new agents in a queue.
6. **Sample data never looks live.** A striped amber "Sample data" badge appears wherever sample data does.

---

## 2. Information architecture and routes

The app uses hash routing behind Domino's proxy (see the `domino-apps` constraints). All routes below sit after `#`.

| Route | Screen | Notes |
|---|---|---|
| `/connections` | Gallery (Connections home) | New top-nav item **Connections**, between Fleet and Settings |
| `/connect/:provider` | Wizard, step 1 Connect | `provider` ∈ `aws, google, microsoft, databricks, snowflake` |
| `/connect/:provider/:connId/access` | Step 2 Check access | `connId` is created as a draft connection when step 1 starts (it holds the nonce) |
| `/connect/:provider/:connId/scope` | Step 3 Choose scope | |
| `/connections/:connId/scans/:scanId` | Step 4 Scan (live view) | Same URL whether you reach it from the wizard or the detail page |
| `/connections/:connId/scans/:scanId/review` | Step 5 Review | |
| `/connections/:connId/scans/:scanId/summary` | Step 6 Import summary | |
| `/connect/:provider/callback?state=…` | Return-link landing | AWS `ReturnToACT`, Google and Snowflake "Finish in ACT", Microsoft "Return to ACT". Hash route, so the proxy never sees an unknown path |
| `/connections/:connId` | Connection detail | Status, Awaiting import queue, access, scope, history, disconnect |
| `/connections/:connId/intake/:sourceId` | Single-agent intake | Full-page form of the review row's detail panel, for one queued agent |
| `/connections/add-by-url` | Other: add by A2A URL | Today's `Register.jsx`, unchanged after step 1 |
| `/register` | Redirect | Redirects to `/connections/add-by-url` |

**Wizard rail.** The rail always has six steps: Connect · Check access · Choose scope · Scan · Review · Import. A reconnect starts at step 1 and skips straight to step 2 once trust is detected. "Scan now" on a connection starts at step 4.

**Navigation changes**
- Top nav: **Fleet · Connections · Settings**. Remove "Register external agent" from the nav.
- Fleet header: the primary action becomes **Add agents** (goes to `/connections`). A secondary button, **Add by URL**, goes to `/connections/add-by-url`. Keep the old `data-testid="register-external-button"` on **Add by URL** for one release, then retire it.
- Fleet empty state: title "No agents yet". Body: "Connect a platform and ACT finds the agents running there." Button: **Add agents**.
- Fleet banner, when any connection has queued agents: "3 agents found by AWS sync are awaiting import. They aren't in Fleet until someone reviews them." Button: **Review**.
- Settings → Credentials: becomes a link to Connections ("Connections and their trust settings moved to Connections"). Existing secret fields stay only for the key or password fallbacks.
- Agent record: the source chip ("AWS") links to its connection. A "Source disconnected" or "Missing at source" pill shows when either applies.

**How "Register external agent" maps across.** It becomes the **Other** card ("Add an agent by URL"). The URL field and the seven-question assessment stay as they are. Step 1 gains a nudge: "Is this agent on AWS, Google Cloud, Microsoft, Databricks or Snowflake? Connect the platform instead." Paste-anything on any provider page sends an agent-card URL here.

---

## 3. Screens and states

### 3.1 Gallery (`/connections`)
- **Header.** Title "Connections". Description: "Connect the platforms where your agents run. ACT finds the agents, suggests a tier for each, and you choose what to import. ACT only reads: it never runs or changes an agent."
- **Domino strip** (full width, above the grid). Title "Domino (this deployment)". Text: "Connected automatically through ACT's own Domino identity. Nothing to set up." Status "Connected · N agents". Button: **Open**. It is never "Not connected". If the Domino API fails, show "Needs attention" with "ACT can't read Domino projects right now. It retries every 5 minutes."
- **Cloud grid.** Three columns at 900 px and wider, two below that. Order: AWS, Google Cloud, Microsoft, Databricks, Snowflake, Other. Section label: "Cloud platforms". Aside: "Each setup uses a one-time trust: no passwords or keys are stored."

**Card states**

| State | Status pill | Meta lines | Primary | Secondary |
|---|---|---|---|---|
| Not connected | grey "Not connected" | Setup: method + time · Needs: who | **Connect {Provider}** | **Explore sample data** (ghost) |
| Connecting (draft exists) | purple spinner "Waiting for {Provider}" or "Waiting for connection code" | Started: time, by whom · Next: what's left | **Resume setup** | Discard setup (ghost, confirms) |
| Connected, healthy | green "Connected · N agents" | Last scan: time · N awaiting import · Sync: schedule | **Open connection** (secondary style) | none |
| Needs attention | amber "Needs attention" | Problem: one line · Last good scan | **Fix connection** | none |
| Failed (never verified) | red "Setup failed" | Problem: one line | **Try again** | none |
| Sample data | striped "Sample data" | Source: "Fictional sample tenant, not read from {Provider}" · Agents | **Connect real {Provider}** | **Remove sample data** |
| Multiple connections | "2 connections · N agents" | List of names | **Open connections** | **Add another** |

- **Loading.** Six skeleton cards with the existing `.ds-skel` shimmer and `aria-busy="true"` on the grid.
- **Error.** The page-level `.ds-empty-error` with title "Connections didn't load". Body: "ACT couldn't reach its database. Try again in a moment." Button: **Try again**.
- **Not an admin.** Cards still show, but Connect buttons are disabled. Tooltip and `aria-describedby`: "Only ACT admins can connect platforms. Ask {owner}." Sample data stays available.

**Google: one card or two.** Recommendation: **one card**, titled "Google Cloud", with products "Vertex AI Agent Runtime · Gemini Enterprise · Agent Registry". Research shows one trust and one role cover all three, and the step 1 info box spells this out. *Alternative for Nick:* two cards, "Google Vertex AI" and "Gemini Enterprise". They share one connection. Connecting either connects both, and the second card then reads "Connected through Google Cloud". This helps people who scan for the product name, but it risks people thinking two setups are needed.

**Microsoft card.** Title "Microsoft". Products "Azure AI Foundry · Entra Agent ID · Agent 365". The Agent 365 catalog is licence-gated, so its check is optional (§5.3).

### 3.2 Step 1, Connect (handshake)
Layout: page header (breadcrumb Connections / Connect {Provider}), then the step rail. Below that, the main column (cards stacked) and a 340 px sticky aside from 1200 px wide. Below 1200 px the aside moves under the main column.

**States of the handshake panel**

| State | What shows | Live region |
|---|---|---|
| Idle | Numbered steps, primary button, "Send to my cloud admin", listener in grey: "ACT starts watching for the new role when you open the AWS console." | none |
| Waiting | Listener in purple with a pulse and an elapsed timer (`aria-hidden`). "Waiting for AWS… Finish the stack in the AWS tab, then click ReturnToACT on the Outputs tab. This page continues on its own." | Polite, once: "Waiting for AWS. This page continues on its own when the role is ready." |
| Detected | Listener in green: "Role detected. ACT-ReadOnly in account 1234 5678 9012. Checking access…" Moves to step 2 after 1.5 s | Polite: "AWS role detected in account …. Moving to Check access." |
| Still waiting (after 10 min) | Listener text adds: "Haven't seen it yet. Check the AWS tab for a red status, or paste the role ARN below." Polling continues | Polite, once |
| Failed | `.ds-alert-error`: what happened, why, and the fix (tables in §5), with action buttons | `role="alert"` |
| Expired (24 h) | "This setup link expired. Start again to get a fresh one." Button: **Start again** (new nonce) | `role="alert"` |

**Detection mechanics (builder note).** Step 1 creates a draft connection holding a single-use `state` nonce. The return link (`#/connect/:provider/callback?state=…&…`) can open in any tab. That tab posts the payload to the backend, which verifies the nonce. The wizard tab polls `GET /api/connections/:connId` every 3 s while visible, and every 15 s when hidden (`document.visibilityState`). When the draft becomes `trusted`, the wizard advances. The callback tab shows the same wizard at step 2. If both tabs are open, the older one shows "Setup continued in another tab." with a **Continue here** button. Databricks has no return link, so its handshake has no listener: the code is pasted, or the user signs in with Microsoft on Azure.

**Paste-anything field.** One field under an "or paste" divider. ACT parses the input as the user types (250 ms debounce) and never needs a submit button.

| Input | Recognized as | Result |
|---|---|---|
| `arn:aws:iam::123456789012:role/ACT-ReadOnly` | AWS role | "Recognized: role ACT-ReadOnly in AWS account 1234 5678 9012." Button: **Use this** |
| URL containing `connect/aws/callback` or `role=arn` | ReturnToACT link | Same |
| `…/connect/google/finish?cfg=…` | Google finish link | "Recognized: Finish in ACT link from Cloud Shell." |
| `…/connect/microsoft/callback?tenant=…` | Microsoft return link | |
| base64 JSON with `account_id` | Databricks connection code | "Databricks account …a1f on AWS, 4 workspaces" |
| `https://*.cloud.databricks.com`, `*.azuredatabricks.net`, `*.gcp.databricks.com` | Databricks workspace URL | Then offers **Sign in with Databricks** |
| `app.snowflake.com/{org}/{account}`, `{org}-{account}.snowflakecomputing.com`, `ORG.ACCOUNT` | Snowflake account | |
| 12 digits | Partial (amber hint) | "That looks like an AWS account ID. ACT needs the role too: paste the role ARN from the stack's Outputs tab." |
| `…agent-card.json` | Wrong place (amber hint) | "That is an A2A agent card URL. Add it from Other: add an agent by URL." (link) |
| Another provider's value | Wrong provider (amber hint) | "That looks like a Google Cloud finish link. Connect Google Cloud instead." (link) |
| Anything else | Error (red) | "ACT couldn't recognize that. Paste the role ARN (arn:aws:iam::…:role/ACT-ReadOnly), a Finish or Return link, a connection code, or an account URL." |

The result region is `aria-live="polite"`. The field gets `aria-invalid="true"` only for the red error.

**"Other ways to connect"** is a `<details>` disclosure, closed by default. It holds the provider's fallbacks in rank order. Secret-storing options end with "Not recommended: ACT then stores a long-lived secret."

**Aside (every provider)**: the headings "ACT will be able to read", "ACT can't", "What ACT stores" and "Who can do this". The last always ends: "Not you? Use Send to my cloud admin; this page updates when they finish."

**Send to my cloud admin** copies a link to the pre-filled console page or command. The link carries no secret. It is a button, labelled "Send to my cloud admin", that confirms with "Copied". It does not send email (no outbound mail in v1).

### 3.3 Step 2, Check access
- One card, "Permissions", with a summary in the head ("Checking…", then "All 6 checks passed" or "5 of 6 passed · 1 optional missing").
- Each check row: an icon (spinner, tick, cross, amber triangle or dash), a bold label, a **Required** tag where it applies, the state text (Checking… / Passed / Missing / Not requested), and a muted line naming the API calls. Missing items expand a fix box (copy in §5) with action buttons.
- Checks resolve one by one as the API answers (no fake delay in the product). Each tick pops in over 200 ms.
- Footer: **Check again** (secondary) and **Continue to scope** (primary). The primary is disabled until every Required check passes, with a reason: "Required checks must pass before you continue." If only optional checks are missing, the label becomes **Continue without {capability}**, and the fix box states what will be missing.
- Error loading checks (network or 5xx): "ACT couldn't run the checks. This is on ACT's side, not {Provider}'s. Try again." Button: **Check again**.
- Required checks fail: footer text "Fix the required items above, then check again." ACT re-runs the checks automatically every 30 s for 10 min, because cloud IAM changes take time to propagate. The text says "ACT re-checks every 30 seconds."

### 3.4 Step 3, Choose scope
- A tree built from discovery, with nothing typed. Levels per provider: AWS Organization › account › Region. Google org › folder › project (› Gemini app). Microsoft tenant › subscription › Foundry resource › project, plus "Entra-only agents (M)" and "Agent 365 catalog (K)". Databricks account › workspace › catalog. Snowflake account › database › schema.
- Every node with agents more than 0 is ticked by default. Empty leaves fold into one dim row: "15 Regions with no agents. Not scanned. New agents there are picked up by daily sync."
- Pending nodes (AWS StackSet not yet in an account) show a disabled checkbox, a spinner and "Waiting for the role to reach this account. Retrying every 5 minutes."
- Use native checkboxes in nested lists, not `role="tree"`. Parents show `indeterminate` when partly selected. Disclosure buttons carry `aria-expanded` and `aria-controls`.
- The head summary updates live: "6 of 6 Regions selected · 3 accounts". The primary label follows the selection: **Scan 6 Regions**. It is disabled at 0 selected, with the label "Select a Region to scan".
- Loading: "Finding your accounts and Regions…" with 4 skeleton rows. ACT shows a preview count from the Agent Registry or Cloud Asset Inventory as soon as it has one.
- Empty (no agents anywhere): title "No agents found in the accounts ACT can see". Body: "ACT can see 4 accounts and 22 Regions, but none have AgentCore, Bedrock or Registry agents. If you expected some, check that the role reached those accounts." Buttons: **Scan anyway** (sets up sync) and **Check access**.

### 3.5 Step 4, Scan
- Top card: the scope title, a progress bar (`role="progressbar"`, `aria-valuenow` = scopes done), and a status line ("Scanning retail-prod / us-east-1 · 2 of 6 done").
- Six counters: Agents found, New, Changed, Unchanged, Missing, Skipped scopes. At 1100 px and below they wrap to three columns.
- Two columns (5:7). **Scopes**: queued (clock), scanning (spinner), done ("Done · 6 agents"), or skipped with reason ("Skipped: Region disabled in this account"). **Agents found**: newest first. Each row fades in from 4 px above over 200 ms.
- Job strip: "Running as Domino Job #4821. You can close this page; ACT keeps scanning and the result waits on the AWS connection." Buttons: **Leave and notify me** (back to the connection; the scan row there shows progress) and **Review agents** (disabled until done, reason given in text).
- Done: "Scan complete in 1 min 12 s. 19 found, 1 missing, 1 scope skipped." The primary becomes **Review 20 agents**.
- Partial failure: per-scope errors never stop the scan. A skipped scope shows its plain-language reason and a **Retry this scope** link once the scan ends.
- Job failed: `.ds-alert-error` "The scan stopped before it finished. Domino Job #4821 failed: out of memory. Agents found so far are kept." Buttons: **Retry scan** and **View job log**.
- Throttling: an amber line under the status, "AWS asked ACT to slow down; this scan will take a bit longer." Never shown as an error.

### 3.6 Step 5, Review (intake review table)
See the component spec in §4.8. Header description: "Scan finished: 19 agents found in 6 Regions. New and changed agents are selected. Check owners and tiers, then import. Imported agents start in Pending review."
- Empty filter: "No agents match. Try another filter or clear the search." Button: **Show all agents**.
- Nothing new: "Nothing new to import. All 19 agents match what's in ACT." Button: **Back to the connection**.
- Import error: `.ds-alert-error` above the table: "Import didn't finish. 12 of 16 agents were imported; 4 weren't because the database timed out. Selected rows still waiting are kept." Button: **Import remaining 4**. Import is idempotent per `sourceId`.

### 3.7 Step 6, Import summary
- Hero: a green tick and "16 agents imported from AWS". Sub line: "4 are Tier 1. Reviewers see them first in Fleet."
- Four figures: New (Created in Pending review) · Updated (N went up in risk and are back in review) · Need an owner (Assign before approval) · Missing at source (Finding raised; record kept).
- **Next** card: **Open imported agents in Fleet** (Fleet filtered to this scan), **Assign N missing owners** (back to review, filter Needs attention), and **View the AWS connection**.
- **Keep AWS in sync** card: radios Off / Daily at 02:00 / Hourly, defaulting to Daily (visible and changeable, no pre-hidden opt-in). Note: "Runs as a scheduled Domino Job. New agents wait in Awaiting import; ACT never imports them on its own. Changes and disappearances become findings." Button: **Save schedule**. Status (polite): "Saved. Next scan tomorrow at 02:00."

### 3.8 Connection detail (`/connections/:connId`)
- Header: mark and name ("AWS · Acme Bank Organization"). Actions: **Scan now** and **Check access**.
- Meta line: status pill · "No secrets stored: IAM role trusts Domino sign-in" · "Connected 3 Nov 2026 by m.chen" · "Sync: daily at 02:00".
- Four figures: Agents in ACT · Awaiting import · Last scan · Next scan.
- **Awaiting import** table: Agent, Where, Suggested tier, Found, and a **Review** button. The header has **Review all 3**. Hidden when empty.
- **Access** card: trust details and "7 of 7 checks passed".
- **Scope** card: summary and **Edit scope** (opens step 3 in edit mode; the primary becomes **Save scope**).
- **Scan history** table: Started, Trigger, Found, New, Changed, Missing, Result (pill plus Job link).
- **Disconnect** section. The button opens a native `<dialog>`. Title: "Disconnect AWS?" Body: "ACT stops reading from AWS and cancels the daily sync. The 19 agents already in ACT stay, with their history, and are marked Source disconnected." Note: "The ACT-ReadOnly role stays in your AWS accounts until you delete the ACT-ReadOnly stack in CloudFormation." Buttons: **Keep connection** and **Disconnect AWS** (danger).

**Status model**

| Status | Trigger | Banner copy pattern |
|---|---|---|
| Healthy | Last check and scan ok | none |
| Needs attention | Auth fails, a required permission is lost, or the template is outdated | "{Provider} rejected ACT's sign-in, so scans are paused." + cause + fix + "Agents already in ACT are not affected." |
| Degraded | Optional permission missing, or some scopes skipped | Amber info: "ACT can't read the Agent Registry, so names and owners may be missing." |
| Paused | The user turned sync off | Grey: "Sync is off. Scan now or turn on a schedule." |
| Disconnected | User action | The record stays in the list as "Disconnected", with **Reconnect** |

### 3.9 Single-agent intake (`/connections/:connId/intake/:sourceId`)
- The review row's detail panel, full page: agent header (name, kind, location, "Found by daily sync, today 02:04"), the seven criteria with scores and "why", tools, data scopes, identity, owner select, tier with **Change tier**, and the diff if Changed.
- Footer: **Import agent** (primary) and **Dismiss** (secondary). Dismiss requires a reason, and the dismissal is audited. A dismissed agent stops showing in the queue until it changes.

### 3.10 Sample data
- Entry: **Explore sample data** on any not-connected card. This creates a connection flagged `sample=true` that runs the existing fixtures.
- Everywhere it appears: the striped **Sample data** badge on the card, the connection header, every review row, and every imported Fleet row. Above the review table goes the banner: "Sample data. These agents are fictional and were not read from AWS. Imported records are marked Sample data in Fleet and are excluded from evidence packs."
- The import button reads **Import N agents as sample data**.
- A sample connection skips steps 1 to 3 and shows no "Connected" status. Evidence packs refuse sample records.
- Removing sample data deletes its records. The confirmation states the count.

---

## 4. Components

New classes use the `po-` prefix in a new `frontend/src/connect.css`. They are built only on `design.css` tokens and `ds-*` classes. React components live in `frontend/src/components/connect/`.

### 4.1 ProviderMark
- A 40 px (48 px in headers) rounded square with `aria-hidden="true"`. The provider name is always written in text next to it.
- Product: the official mark, from an asset file, no wider than 28 px inside the square (see §10).
- Prototype: a neutral grey square with a text monogram (AWS, GC, MS, DB, SF, DOM, A2A).

### 4.2 ProviderCard
`<article aria-labelledby>`. It holds the mark, a title (h3), the products line, a status pill, a meta `<dl>` with two lines at most, and actions. The card itself is not clickable, so buttons are never nested. On hover the border darkens to `--ds-line-strong`, with a 2 px shadow.

### 4.3 StepRail
- `<ol aria-label="Setup steps">` with six equal columns. Each item has a 22 px number circle and a label.
- Current item: `aria-current="step"`, accent colour, 2 px accent underline.
- Done items: green tick and a visually hidden "(done)". They are links when the step can be revisited (scope, review). Connect and Check access are not links once passed; use **Check access** on the detail page instead.
- The page `<h1>` repeats the step name, and a visually hidden "Step n of 6." follows the description.

### 4.4 HandshakePanel
Props: `provider`, `primaryAction` (`{label, href|copyText}`), `steps[]`, `command?`, `script?`, `listener` (`idle|waiting|detected|failed|expired`), `pasteHint`, `fallbacks[]`.
- **Primary.** One button that does the whole job: opens the pre-filled console (AWS) or copies the command and opens the shell (Google, Microsoft, Snowflake). External links say "(opens in a new tab)" visually hidden and show an external-link icon.
- **Command block.** Dark code surface (`--ds-code-bg`), monospace 13/20, at most 104 px tall with scroll, and an inline **Copy** button (copy confirms "Copied" for 1.8 s and announces it). Under it, a caption ending "Contains no secret." A disclosure, **Read what the command does**, shows the decoded script in plain text. Banks will want this.
- **Listener.** A strip with a status dot (pulsing ring only while waiting), the text, and an elapsed time. It never has a button.
- **Paste-anything** (see §3.2).
- **Other ways to connect** disclosure.

### 4.5 PermissionChecklist
`<ul>` of rows with a 3-column grid (24 px icon, label and detail, state). The fix box spans columns 2 and 3: red for a required item, amber for an optional one. Every fix has a primary action that does the fix where possible (Update stack, Re-run setup, Copy grant SQL) and a copy button for the exact permission or role name.

### 4.6 ScopeTree
Nested `<ul>` with native checkboxes, indeterminate parents, disclosure toggles, count pills (`po-count-hi` when above 0), dim rows for empty or pending nodes, and a toolbar with **Select all with agents**.

### 4.7 ScanView
Progress card, counter tiles (`po-stat`), scope list, agent feed, and job strip. The counters must reflect server truth: poll `GET /api/scans/:id` every 2 s, or use SSE if the proxy allows it. Unchanged counters do not re-render.

### 4.8 IntakeReviewTable
- **Toolbar.** A segmented filter (`aria-pressed` buttons) with counts: All · New · Changed · Needs attention · Unchanged · Missing. Then a search field: "Search name, account or type".
- **Columns** (fixed layout): checkbox 44 · Agent (name bold, second line "kind · account · Region", both truncated with `title`) · Change 128 · Owner 168 · Tier 168 · Risk flags 25% · Why 76. At 1100 px and below: Owner 148, Tier 150, Flags 21%.
- **Selection.** Only New and Changed rows have a checkbox; they are selected by default. Unchanged rows have nothing to do. Missing rows can't be selected: their flag cell says "Not found in AWS. On import ACT raises a finding; the record stays." The header checkbox selects every visible selectable row.
- **Owner.** A native `<select>` holding Domino users and "No owner". Under it, the source in 11 px: "From tag owner", "From creator (CloudTrail)", "From Agent Registry record", or "Set by you". "No owner" is styled red and adds the **No owner** flag.
- **Tier.** The badge, plus a delta for Changed rows ("↑ from Tier 2" in red when risk went up). Note under it: "Suggested", "Current tier", or "Set by you · suggested Tier 1".
- **Risk flags** (fixed vocabulary): Moves money, No human approval, Writes customer data, Shared identity, No owner, Customer-facing, PII access, Risk went up. The first four plus "Risk went up" use the red variant. Two show, then "+n" with a `title` listing the rest.
- **Risk edge.** A 3 px red inset on the first cell for Tier 1, no owner, or shared identity. It is never the only signal: a flag chip always accompanies it.
- **Why** opens an inline detail row (`aria-expanded`, `aria-controls`). Its left side, "Why Tier N is suggested", lists the seven criteria, each with its score and one evidence line (for example, "Financial exposure · 5 · Tool payments.issue_credit can move or commit funds"). Scores of 4 or more show in red. Its right side has the diff for Changed rows (add green, remove red, modify amber, monospace), followed by the Tier block.
- **Change tier.** A tier picker (radiogroup with roving tabindex and arrow keys), plus a required Reason field: at least 10 characters, shown to the reviewer and audited. Inline error: "Enter a reason of at least 10 characters. Reviewers see it with the tier." Lowering a tier below the suggestion adds the helper "Lowering below the suggestion is flagged to the reviewer."
- **Sticky footer bulk bar.** Shows "{n} selected", the **Assign owner…** select, **Clear selection**, a context line ("2 without an owner will import as Needs owner. 1 missing agent raises a finding."), and the primary **Import {n} agents**. With nothing selected, the primary is disabled with the reason "Select at least one agent to import."
- **Density.** Rows are 56 px (two-line cells). The table stays within the container; nothing scrolls horizontally.

### 4.9 ImportSummary
Hero, four-figure `<dl class="po-kv">`, Next actions, and the sync schedule card (§3.7).

### 4.10 SampleBadge and SampleBanner
- Badge: 22 px pill with a striped dot, text "Sample data" in `#5c3f00` on `#fff7e6`, border `#b07a00`.
- Banner: same colours plus a 6 px striped left edge, `role="note"`.
- The text "Sample data" is always present. Never use colour alone.

---

## 5. Per-provider steps and microcopy

Shared labels: the paste divider reads "or paste". Fallbacks sit under the disclosure "Other ways to connect". The admin button reads "Send to my cloud admin". Step 2's primary reads "Continue to scope". The scan start reads "Scan {n} {scope noun}".

### 5.1 AWS (Bedrock AgentCore, classic Bedrock Agents, Agent Registry)
**Card.** Products: "Bedrock AgentCore · Bedrock Agents · Agent Registry". Setup: "CloudFormation stack, about 2 minutes". Needs: "An AWS admin who can create IAM roles".

**Step 1.** Title "Connect AWS". Description: "ACT reads Bedrock AgentCore runtimes and harnesses, classic Bedrock agents and the Agent Registry through a read-only role in your account."
- **What should ACT read?** Two choices:
  - **This AWS account**: "The account you sign in to."
  - **My whole AWS Organization**: "Sign in to the management account. Covers every account, including new ones." This switches the link to the StackSet template.
- Checkbox **Also read traces from CloudWatch**, off by default. Helper: "Lets ACT confirm which models and tools agents actually use. Adds read access to logs and X-Ray. You can turn this on later."
- Panel title "Create the ACT read-only role", aside "About 2 minutes in AWS". Steps:
  1. "Open the AWS console. The CloudFormation stack is already filled in; sign in if AWS asks."
  2. "At the bottom, tick **I acknowledge that AWS CloudFormation might create IAM resources with custom names**, then click **Create stack**."
  3. "When the stack shows CREATE_COMPLETE (about a minute), open the **Outputs** tab and click **ReturnToACT**."
- Primary **Open AWS console** (a quick-create URL with every parameter pre-filled). Paste label: "Already created the role? Paste it here". Helper: "Paste the role ARN or the ReturnToACT link from the stack's Outputs tab. ACT works out the rest."
- Other ways to connect:
  - "Download the template or the Terraform module for change review. Both create the same read-only role."
  - "Use External ID trust if your security team doesn't allow trust in Domino's sign-in service."
  - "Use an access key. Not recommended: ACT then stores a long-lived secret."
  - Collapsed policy summary (the read actions, plus "Excluded: memory records, API keys and OAuth tokens, InvokeAgent and every write action.").
- Aside "can't" line: "Read memory records, secrets or API keys".

**Step 2 checks.** Signed in to AWS (Required) · Read AgentCore runtimes and harnesses (Required) · Read classic Bedrock agents (Required) · Read the Agent Registry · Read IAM policies on agent roles · Read AWS Organization accounts (Organization mode only) · Read CloudWatch traces ("Not requested" unless traces are on).

**Step 3.** Organization › account › Region (§3.4). **Step 4** status: "Scanning {account} / {Region} · n of N done".

**Failures (step and copy)**

| When | Copy | Action |
|---|---|---|
| Stack: capability box not ticked | "The stack needs the IAM acknowledgement. Tick the box at the bottom of the AWS page, then click Create stack." | none (listener keeps waiting) |
| Stack: OIDC provider already exists | "Your account already trusts this Domino deployment. Use this link instead; it reuses the existing trust." | **Open AWS console** (with `CreateOidcProvider=false`) |
| Stack: `AccessDenied` on `iam:CreateRole` | "AWS couldn't create the ACT role. Your AWS user isn't allowed to create IAM roles. Someone with IAM admin rights needs to create the stack. Send them the link; this page updates when they finish." | **Copy link for my cloud admin**, **Open AWS console again** |
| Verify: keys unreachable | "AWS can't reach this Domino deployment's sign-in keys. Ask your Domino admin whether /auth/realms/DominoRealm is reachable from the internet, or use External ID trust." | **Use External ID trust** |
| Verify: wrong audience | "The role expects a different Domino audience. Update the stack to refresh it." | **Update stack in AWS** |
| Verify: wrong subject | "The role trusts a different Domino identity. ACT must run as its service account (act-svc). Re-run setup from this page." | **Start again** |
| Check: missing action | "The ACT role is missing {action}. Your stack uses template v{n}. Update the stack to v{m} in AWS (one click, about a minute), then check again." | **Update stack in AWS**, **Copy permission name** |
| Org: not the management account | "This account isn't your Organization's management or delegated admin account. ACT connected this single account instead." (amber) | **Switch to the management account** |
| Scan: Region disabled | "Skipped: Region disabled in this account" | none |
| Scan: StackSet not in account | "Waiting for the role to reach this account. Retrying every 5 minutes." | none |
| Scan: throttled | "AWS asked ACT to slow down; this scan will take a bit longer." | none |
| Any: temporary sign-in failure | "Domino sign-in is temporarily unavailable. ACT is retrying." | none |

### 5.2 Google Cloud (Vertex AI Agent Runtime, Gemini Enterprise, Agent Registry)
**Card.** Products: "Vertex AI Agent Runtime · Gemini Enterprise · Agent Registry". Setup: "Cloud Shell command, about 1 minute". Needs: "A Google Cloud admin who can grant IAM roles".

**Step 1.** Description: "One connection covers Vertex AI Agent Runtime, Gemini Enterprise and the Agent Registry. A short command in Cloud Shell sets up a keyless trust between Google Cloud and this Domino deployment."
- Info box: "**Vertex AI and Gemini Enterprise share this connection.** Agent Runtime (formerly Vertex AI Agent Engine), Gemini Enterprise (formerly Agentspace) and the Agent Registry are read with one trust and one role. Each gets its own access check."
- Panel title "Run the setup command in Cloud Shell". Steps:
  1. "Click **Copy command and open Cloud Shell**. Cloud Shell opens in a new tab."
  2. "Paste the command (Ctrl+V or ⌘V) and press Enter. If Google asks, click **Authorize**."
  3. "Pick the organization or projects ACT should read and type **Y**."
  4. "Click the **Finish in ACT** link the script prints."
- Command caption: "Self-contained, idempotent and safe to re-run. Contains no secret." Disclosure: **Read what the command does**.
- Listener: "Waiting for Google Cloud… Click Finish in ACT in Cloud Shell when the script ends. This page continues on its own."
- Other ways to connect:
  - "Sign in with Google instead. Quicker, but ACT sees only what you can see, and your org may require you to sign in again every few hours." Shown only when an OAuth client is configured.
  - "Terraform module for teams that require infrastructure as code."
  - "Upload a service account key. Not recommended; most banks block key creation."
- Aside "can't" line: "Query or invoke agents". The custom role excludes `reasoningEngines.query`.

**Step 2 checks.** Google accepted ACT's sign-in (Required) · Organization visible · Read Agent Runtime (Required, unless Registry or Gemini passes) · Read the Agent Registry · Read Gemini Enterprise · Read audit logs · Read traces.

**Step 3.** Org › folder › project, with count pills. Projects where the API is off show "API off, no agents" in dim text, not as an error.

**Failures**

| When | Copy | Action |
|---|---|---|
| Org policy blocks the issuer | "Your organization only allows approved identity providers. A Google Cloud org admin needs to add this Domino deployment's sign-in address to that policy, or you can sign in with Google instead." | **Copy sign-in address**, **Sign in with Google** |
| Can't create the pool | "You need to be an IAM admin in the host project. Pick another project, or send the command to your cloud team." | **Send to my cloud admin** |
| Audience mismatch | "ACT's Domino identity changed since setup. Re-run the command; ACT has updated it." | **Copy command and open Cloud Shell** |
| Attribute condition rejected (publisher changed) | "Google Cloud rejected ACT's sign-in, so scans are paused. The trust was set up for ACT's previous publisher. This app was republished on {date} by a different user, so Google no longer recognizes it. Re-run the setup command to trust the current publisher." | **Re-run setup**, **Copy error details** |
| Keys unreachable | "Google can't reach this Domino deployment's sign-in keys. Re-run setup with key upload." | **Re-run setup** |
| Pool deleted | "The trust in Google Cloud was removed. Reconnect." | **Re-run setup** |
| Permission missing | "ACT can't read {product} in {project}. Add the role {role}; the command line is below." | **Copy command line** |
| API off | (info) "Gemini Enterprise isn't turned on in {project}. Skipped." | none |
| VPC Service Controls | "A VPC Service Controls perimeter blocks reads from outside. An admin must add an ingress rule for ACT." | **Copy principal** |
| Key upload blocked | "Key files are blocked in your organization, which is good practice. Use Cloud Shell setup." | **Use Cloud Shell setup** |

### 5.3 Microsoft (Azure AI Foundry, Entra Agent ID, Agent 365)
**Card.** Products: "Azure AI Foundry · Entra Agent ID · Agent 365". Setup: "Azure Cloud Shell command, about 2 minutes". Needs: "An Azure admin; a Privileged Role Admin for Entra".

**Step 1.**
- Choice:
  - **One subscription**
  - **A management group**: "Covers every subscription under it."
- Toggles: Agent 365 catalog (on; helper "Needs an Agent 365 licence") and traces (off).
- Panel "Run the setup command in Azure Cloud Shell". Steps:
  1. "Click **Copy command and open Azure Cloud Shell**."
  2. "Paste the command and press Enter. A Privileged Role Administrator must run it for Entra and Agent 365 access."
  3. "When the deployment finishes, click **Return to ACT** in the output."
- Caption: "Creates a managed identity, a read-only custom role and Graph read permissions. Contains no secret."
- Fallbacks:
  - "Just sign in with Microsoft instead. Good for a pilot; the connection stops when your sign-in expires."
  - "Download the Bicep template for change review."

**Step 2 checks.** Microsoft accepted ACT's sign-in (Required) · Read subscriptions (Required) · Read Azure AI Foundry agents · Read Entra agent identities · Read Agent 365 catalog (optional, licence-gated) · Read traces.

**Failures**

| When | Copy | Action |
|---|---|---|
| AADSTS70021 | "Azure hasn't finished saving the trust yet. ACT keeps retrying for 5 minutes." (amber) | none |
| AADSTS700211/12/13 | "The trust points at a different Domino deployment or identity. Re-run the command, and check that ACT runs as its service account." | **Copy command and open Azure Cloud Shell** |
| AADSTS65001 / 90094 | "An admin must approve ACT once. Send them this consent link." | **Copy consent link** |
| AADSTS53003 / 50076 | "Your organization's sign-in policy blocked this. Use the setup command instead." | **Use the setup command** |
| ARM `AuthorizationFailed` | "ACT can't read subscription {name}. Assign Reader there; the command does this." | **Copy command** |
| Foundry data action missing | "ACT can see the Foundry resource but not its agents. Assign ACT Foundry Inventory Reader on it." | **Copy command line** |
| Foundry private network | (amber) "This Foundry resource is private. ACT lists it from Azure metadata only." | none |
| Graph 403 | "Directory read wasn't granted. A Privileged Role Admin must run the Graph part of the command." | **Send to my cloud admin** |
| Agent 365 not licensed | (amber, optional) "The Agent 365 catalog needs an Agent 365 licence. Foundry and Entra still work." | **Continue without Agent 365** |

### 5.4 Databricks
**Card.** Products: "Agent endpoints · Apps · Agent Bricks · Unity Catalog". Setup: "Setup script and connection code, about 3 minutes". Needs: "A Databricks account admin".

**Step 1.**
- Cloud choice: AWS / Azure / Google Cloud, auto-selected when a pasted code or URL reveals it.
- **On Azure**, the primary becomes **Sign in with Microsoft** ("ACT finds your workspaces with no code"), and the script becomes the alternative.
- Otherwise, panel "Run the setup script". Steps:
  1. "Click **Copy setup script**, or send it to your Databricks account admin."
  2. "Run it in a terminal with the Databricks CLI (version 0.250 or later). It asks which workspaces ACT should read."
  3. "Paste the connection code it prints below."
- Caption: "The code it prints holds the account ID and workspace list. It is not a secret."
- There is no listener. The paste field is the main input. Label "Connection code"; helper "Paste the code the script printed, or any Databricks URL from your browser."
- Fallbacks:
  - "Sign in with Databricks. ACT sees only what you can see."
  - "Terraform module."
  - "OAuth client secret. Not recommended."

**Step 2 checks.** Databricks accepted ACT's sign-in, per workspace (Required) · Read serving endpoints (Required) · Read Apps · Read Agent Bricks · Read Unity Catalog metadata · Read agent services (Beta, optional) · Read traces (optional).

**Failures**

| When | Copy | Action |
|---|---|---|
| No matching federation policy | "Databricks doesn't trust this Domino identity yet. Re-run the setup script." | **Copy setup script** |
| Subject or audience mismatch | "The policy is pinned to a different Domino identity. ACT runs as act-svc; update the policy subject." | **Copy fix command** |
| Unknown `client_id` | "The service principal isn't in workspace {name}. Assign act-reader to it." | **Copy fix command** |
| 403 on endpoints | "Grant CAN_VIEW on serving endpoints to the group act-readers." | **Copy fix command** |
| Account list 403 | (info) "Only account admins can list workspaces; ACT used the workspaces from your connection code." | none |
| Agent services off | (optional) "Agent services (Beta) aren't turned on. Skipped." | none |
| Agent Bricks unavailable | (info) "Agent Bricks isn't available in this workspace or region. Skipped." | none |

### 5.5 Snowflake (Cortex Agents)
**Card.** Products: "Cortex Agents · Snowflake Intelligence". Setup: "SQL worksheet, about 1 minute". Needs: "A Snowflake ACCOUNTADMIN".

**Step 1.**
- Panel "Run the setup worksheet in Snowsight". Steps:
  1. "Click **Copy SQL and open Snowsight**."
  2. "Paste into a new SQL worksheet and click **Run All**."
  3. "Click the link in the last result row, **Finish in ACT**."
- Caption: "Grants MONITOR on agents (read spec, cannot run them). Contains no secret."
- Listener: "Waiting for Snowflake… Click Finish in ACT in the last result row."
- Paste label: "Have the Finish link or account URL? Paste it here."
- Fallbacks:
  - "Sign in with Snowflake (OAuth). Requires re-consent every 90 days."
  - "Use a key pair for Domino deployments Snowflake can't reach."

**Step 2 checks.** Signed in as ACT_SVC (Required) · Role ACT_READER (Required) · Agents visible (Required) · Usage history · Traces (redacted or full) · Grants view.

**Step 3** adds a gap warning: "3 agents in 2 schemas are used but ACT can't read them." Button: **Copy grant SQL**. If the worksheet ran in the organization account, a "Connect 4 more accounts" prompt also appears.

**Failures**

| When | Copy | Action |
|---|---|---|
| Insufficient privileges | "Run this as ACCOUNTADMIN, or send it to your Snowflake admin." | **Send to my cloud admin** |
| Duplicate issuer and subject | "Another Snowflake user already trusts ACT. Re-run the worksheet; it replaces ACT_SVC." | **Copy SQL** |
| WIF rejected | "Snowflake didn't accept ACT's Domino identity. ACT must run as the service account act-svc. Copy the SQL again." | **Copy SQL** |
| Network policy | "Your account restricts IP addresses. Add Domino's egress addresses to a network policy on ACT_SVC." | **Copy addresses** |
| Nonce mismatch | "This link is from an older setup. Copy the SQL again." | **Copy SQL and open Snowsight** |
| 0 agents, but usage exists | "ACT can't see your agents yet. Run the grant snippet." | **Copy grant SQL** |
| Secure agent | (amber) "Secure agent: its tools are hidden from ACT. The record will be partial." | none |

### 5.6 Domino
Nothing to enter. There is no wizard: **Open** goes to the connection detail. **Scan now** starts at step 4. Scope = projects the ACT identity can see.

### 5.7 Other (A2A URL)
Today's Register flow, renamed **Add an agent by URL**. Step 1 is one field with today's copy and validation, plus the "Connect the platform instead" info box. Step 2 is today's seven-question assessment. A suggested tier from the card's skills is out of scope for v1.

---

## 6. Layout, tokens and motion

**Grid.**
- Pages sit inside `.ds-container` (1232 max, inside `.content`'s 24 px padding).
- Wizard: `minmax(0,1fr) 340px` from 1200 px wide; a single column below that.
- Gallery: 3 columns, or 2 below 900 px.
- Scan counters: 6 columns, or 3 at 1100 px and below.
- Review table fixed widths are in §4.8. At 1024 px the Agent column keeps about 225 px.
- No horizontal page scroll at 1024 or 1440. Long IDs wrap (`overflow-wrap:anywhere`) or truncate with `title`.

**Reused tokens.** All `--ds-*` in `design.css`: accent, ink, text, muted, bg, surface, line, danger, spacing, radius, shadow and font.

**New tokens** (add to `:root` in `design.css`):

| Token | Value | Use |
|---|---|---|
| `--ds-ok`, `--ds-ok-bg`, `--ds-ok-line` | `#067647`, `#ecfdf3`, `#abefc6` | Passed checks, healthy pills, diff add (`#067647` is already used in settings.css) |
| `--ds-warn`, `--ds-warn-bg`, `--ds-warn-line` | `#6b4a00`, `#fff7e6`, `#f0c36d` | Needs attention, optional missing (values already used by the demo banner) |
| `--ds-info`, `--ds-info-bg`, `--ds-info-line` | `#175cd3`, `#eff8ff`, `#b2ddff` | Info alerts |
| `--ds-tier-1..4` | `#c62828`, `#8a5300`, `#0d47a1`, `#2e7d32` | Promote today's `.tier-n` colours from styles.css to tokens |
| `--ds-mono` | `ui-monospace, SFMono-Regular, Menlo, Consolas, monospace` | Commands, ARNs, diff |
| `--ds-code-bg`, `--ds-code-text` | `#161b26`, `#e4e7ec` | Command block |
| `--ds-sample-stripe` | `repeating-linear-gradient(135deg, #f0c36d 0 6px, #fff7e6 6px 12px)` | Sample data marker |
| `--ds-dur-1`, `--ds-dur-2`, `--ds-dur-3` | `120ms`, `200ms`, `320ms` | Hover, entry, progress |
| `--ds-ease` | `cubic-bezier(.2,0,0,1)` | All motion |

**Motion** (all of it is subtle and functional):
- Listener pulse: a 1.8 s ring, only while waiting.
- Check tick: scale from .6 to 1 over 200 ms.
- Feed rows: fade and 4 px slide over 200 ms.
- Progress bar width: 320 ms.
- Card hover: 120 ms.

Under `prefers-reduced-motion: reduce`, every animation and transition is set to 0 and the pulse ring is removed. State still changes, so text carries the meaning. Nothing auto-scrolls except the jump to the top on a step change.

---

## 7. Accessibility

**Keyboard flow**
- Step 1: coverage radios (arrow keys inside the group) → traces checkbox → primary → Send to admin → paste field → **Use this** (appears after a match) → Other ways disclosure → Cancel.
- Review: filter buttons → search → header checkbox → per row: checkbox → owner select → Why → (expanded: Change tier → tier radios with arrows → Reason → Save tier / Cancel) → footer: bulk owner → Clear → Import.
- Disabled "links" (Continue before the checks finish, Review before the scan finishes) must be `<button disabled>` in the build, with the reason in adjacent text that `aria-describedby` points to. The prototype fakes them with `aria-disabled`.

**Focus management**
- On every step change, focus the page `<h1>` (`tabindex="-1"`, no visible ring) and scroll to the top. The polite live region announces "Step n of 6: {name}".
- Auto-advance after detection waits 1.5 s, announces before it moves, and then focuses the new `<h1>`.
- Opening **Why** keeps focus on the button. **Change tier** moves focus to the checked tier radio. **Save** and **Cancel** return focus to **Why** or **Change tier**.
- Re-rendering the table preserves focus by element id.
- Dialogs use native `<dialog>.showModal()`: focus goes to the first button, Esc closes, and focus returns to the trigger.

**Live regions** (one app-level `aria-live="polite"` region; errors use `role="alert"`)
- Listener: one message on start, one on detection. The ticking elapsed time is `aria-hidden`.
- Checks: one message when they start ("Checking access. 7 checks.") and one summary at the end. Per-row ticks are not announced.
- Scan: at most one message every 5 s ("12 agents found so far."), plus a completion summary with all counts.
- Copy: "Copied to clipboard", or "Copy failed. Select the text and copy it."
- Paste result: its own `aria-live="polite"` container.

**Semantics**
- Rail `aria-current="step"`. Filter buttons `aria-pressed`. Why buttons `aria-expanded`/`aria-controls`, with hidden text "{name} got Tier {n}".
- Every select and checkbox has a label: row checkbox "Import {name}"; owner select "Owner for {name}".
- The table has a caption: "Agents found in AWS. N shown. Rows with a red edge need attention."
- Status pills always contain text. Icons are `aria-hidden`.

**Contrast.** All text pairs meet 4.5:1, including the muted `#596273` on white (6.4:1), `#6b4a00` on `#fff4d6`, `#3b2ba8` on `#efedfc`, and `#5c3f00` on `#fff7e6`. The focus ring is the 2 px accent outline from `design.css`.

**Zoom.** At 200% (720 px effective) the wizard is one column, the gallery has 2 columns and the counters 3. The review table keeps its fixed layout; flags wrap and names truncate with `title`.

**Self-review** (`design:accessibility-review`, WCAG 2.1 AA) found three issues:
- (Major, fixed in the prototype) The tier picker needed roving tabindex and arrow keys.
- (Minor, build note) Fake-disabled anchors must be real disabled buttons.
- (Minor) `ds-btn-sm` targets are 32 px: they pass 2.5.8 (24 px) but not the AAA 44 px.

Manual NVDA and VoiceOver passes are still needed on the built pages.

---

## 8. data-testid list

**Shell and Fleet:** `nav-fleet`, `nav-connections`, `nav-settings`, `fleet-add-agents`, `fleet-add-by-url` (keeps `register-external-button` as an alias for one release), `fleet-awaiting-banner`.

**Gallery:**
- `provider-gallery`, `domino-connection-card`, `domino-connection-status`, `domino-connection-open`
- `provider-card-{aws|google|microsoft|databricks|snowflake|other}`, `provider-card-{id}-status`, `provider-card-{id}-connect`, `provider-card-{id}-sample`, `provider-card-{id}-sample-remove`
- `sample-badge`, `sample-banner`

**Wizard shell:** `wizard-{provider}`, `step-rail`, `step-rail-item-{1..6}`, `step-heading`, `wizard-cancel`.

**Step 1:**
- `aws-mode-account`, `aws-mode-org`, `aws-traces`, `ms-scope-subscription`, `ms-scope-mg`, `ms-agent365`, `dbx-cloud-{aws|azure|gcp}`
- `handshake-panel`, `handshake-primary`, `handshake-send-admin`, `handshake-command`, `handshake-copy`, `handshake-script-toggle`, `handshake-listener`, `handshake-listener-status`, `handshake-error`, `handshake-retry`
- `paste-anything-input`, `paste-anything-result`, `paste-anything-use`, `paste-anything-error`
- `other-ways`, `other-ways-toggle`, `signin-fallback`, `signin-fallback-databricks`, `fallback-externalid`, `fallback-key`, `template-download`, `template-terraform`

**Step 2:** `permission-checklist`, `permission-summary`, `permission-item-{key}`, `permission-item-{key}-status`, `permission-fix-{key}`, `permission-fix-{key}-action`, `permission-recheck`, `permission-continue`.

**Step 3:** `scope-tree`, `scope-summary`, `scope-select-all`, `scope-checkbox-{nodeId}`, `scope-count-{nodeId}`, `scope-node-{nodeId}`, `scope-continue`.

**Step 4:**
- `scan-view`, `scan-status`, `scan-progress`, `scan-counter-{found|new|changed|unchanged|missing|skipped}`
- `scan-scope-row-{scopeId}`, `scan-feed`, `scan-agent-row-{sourceId}`
- `scan-job`, `scan-job-link`, `scan-leave`, `scan-review`, `scan-error`, `scan-retry`

**Step 5:**
- `review-table`, `review-filter-{all|new|changed|attention|unchanged|missing}`, `review-search`, `review-empty`
- `review-select-all`, `review-row-{id}`, `review-select-{id}`, `review-change-{id}`, `review-owner-{id}`, `review-tier-{id}`, `review-flag-{id}`, `review-why-{id}`, `review-diff-{id}`
- `intake-panel-{id}`, `intake-criterion-{criterionKey}`, `intake-override-open`, `intake-override`, `intake-override-tier-{1..4}`, `intake-override-reason`, `intake-save`
- `review-bulk-bar`, `review-selected-count`, `review-bulk-owner`, `review-bulk-exclude`, `review-import`, `review-import-error`

**Step 6:**
- `import-summary`, `import-summary-count-{new|updated|no-owner|missing}`
- `summary-open-fleet`, `summary-assign-owners`, `summary-open-connection`
- `sync-schedule-{off|daily|hourly}`, `sync-save`

**Connection detail:**
- `connection-detail`, `connection-status`, `connection-attention`, `connection-scan-now`, `connection-check-access`, `connection-edit-scope`, `connection-reconnect`, `connection-copy-error`
- `connection-awaiting-count`, `awaiting-import-queue`, `awaiting-review-all`, `awaiting-row-{n}`, `awaiting-review-{n}`
- `sync-history`
- `connection-disconnect`, `disconnect-dialog`, `disconnect-cancel`, `disconnect-confirm`

**Single-agent intake:** `intake-page`, `intake-import`, `intake-dismiss`, `intake-dismiss-reason`.

**Other:** `card-url`, `card-url-continue` (the rest unchanged from Register.jsx).

---

## 9. Acceptance checks (from screenshots)

1. `#gallery` at 1440: the Domino strip sits full width above a 3×2 grid. Every card has a mark, name, products line and a text status pill. No card shows only a logo.
2. `#gallery` at 1024: still 3 columns, no horizontal scroll, and no card text overlaps.
3. The Snowflake card shows the striped "Sample data" badge, and its primary reads "Connect real Snowflake". No card in sample state says "Connected".
4. The Google card names Vertex AI Agent Runtime, Gemini Enterprise and Agent Registry.
5. The top nav reads Fleet · Connections · Settings, and "Register external agent" is gone from the nav.
6. `#aws-1`: the rail shows step 1 current and steps 2 to 6 grey. The header describes what ACT reads.
7. `#aws-1`: the aside lists "ACT will be able to read", "ACT can't", "What ACT stores" (saying no passwords, keys or tokens) and "Who can do this".
8. `#aws-1`: there is exactly one primary button in the handshake panel, and no "I'm done" or "Verify" button anywhere.
9. `#aws-1-waiting`: the listener is purple with a dot and an elapsed time. Within about 6 s it turns green with "Role detected" and the page moves to Check access.
10. `#aws-1-failed`: the red alert names what happened, why, and the fix, and offers "Copy link for my cloud admin".
11. `#aws-1`: typing `arn:aws:iam::123456789012:role/ACT-ReadOnly` shows a green "Recognized" card with the spaced account number and **Use this**. Typing `123456789012` shows the amber account-ID hint. Typing nonsense shows the red message.
12. "Other ways to connect" is collapsed by default, and the access-key option says "Not recommended".
13. `#aws-2`: the checks tick in one at a time. Required items carry a "Required" tag. Traces reads "Not requested", not "Missing".
14. `#aws-2-partial`: the Registry row is red with a fix box naming `agent-registry:ListRegistries`, with **Update stack in AWS**. The primary reads "Continue without Registry".
15. `#aws-3`: every Region with agents is ticked. Empty Regions are one dim row. "sandbox" shows a spinner and "Waiting for the role…" with its checkbox disabled. The primary reads "Scan 6 Regions".
16. `#aws-3`: unticking every Region changes the primary to "Select a Region to scan" and disables it.
17. `#aws-4`: six counters increase as rows stream in. The scope list shows Queued, Reading and Done states. The Job strip says the page can be closed.
18. `#aws-4` when finished: the status line is green, "Scan complete…", and **Review 20 agents** is enabled.
19. `#aws-5` at 1024 and 1440: the table fits with no horizontal scroll. Names truncate with an ellipsis, not by wrapping into the next column.
20. `#aws-5`: New and Changed rows are selected by default. Unchanged and Missing rows have no checkbox. The Missing row explains that a finding will be raised.
21. `#aws-5`: kyc-document-reviewer and model-doc-writer show a red "No owner" select and a "No owner" flag.
22. `#aws-5`: treasury-fx-quote and credit-memo-assistant show "↑ from Tier n" in red.
23. Rows with a red left edge always also show a red flag chip.
24. `#aws-5-why`: the expanded row lists all seven criteria with a score and one evidence sentence each. The diff shows a green "+ tool fx.execute_trade" and a red "− guardrail". The tier picker shows Tier 2 selected and a required Reason field.
25. Saving an override with an empty reason shows the inline red error. Saving with a reason shows "Set by you · suggested Tier 1".
26. The sticky footer shows the selected count, Assign owner, and **Import n agents**. With 0 selected, Import is disabled.
27. `#aws-6`: the hero states how many agents were imported, and the four figures add up to the selection. The schedule shows Off / Daily / Hourly with Daily selected and the "never imports on its own" sentence.
28. `#google-1`: the info box says one connection covers Vertex AI and Gemini Enterprise. There is a dark command block with **Copy**, "Contains no secret", a **Read what the command does** disclosure, and a listener.
29. `#databricks-1`: there is no listener. The paste field is labelled "Connection code".
30. `#connection-aws`: a green "Healthy" pill, "No secrets stored", four figures, an Awaiting import table with 3 rows, Access and Scope cards, Scan history, and a Disconnect section.
31. `#connection-aws`: **Disconnect AWS** opens a modal that states that agents are kept, and names both "Keep connection" and "Disconnect AWS".
32. `#connection-google`: an amber "Needs attention" pill, an alert explaining the publisher change with **Re-run setup**, and **Scan now** disabled.
33. `#sample-review`: the striped banner sits above the table, every row carries "Sample data", and Import reads "Import n agents as sample data".
34. With reduced motion on, no pulse ring, shimmer or slide animation plays, and every state is still readable from text.
35. Every page has one `<h1>`. All UI text is in sentence case, and there are no exclamation marks.

---

## 10. Provider logos (trademark note)

- AWS, Google Cloud, Microsoft (Azure), Databricks and Snowflake marks are third-party trademarks. Use them only to identify the integration (nominative use), never to suggest endorsement or partnership.
- Source each mark from the vendor's official brand or press kit (AWS Architecture Icons and co-marketing guidelines, Google Cloud brand guidelines, Microsoft Trademark and Brand Guidelines and Azure icons, Databricks brand assets, Snowflake brand guidelines). Store them as SVG under `frontend/src/assets/providers/`, with a `LICENSES.md` recording the source URL and date.
- Do not recolour, crop, rotate, stretch, outline or combine marks. Keep the vendor's minimum clear space. Inside the 40 px tile, the mark is no larger than 28 px.
- Monochrome option: where a vendor offers an official one-colour or reversed mark, use it in dense lists (connection detail, Fleet source chips), rendered in `--ds-ink`. Use full colour only on gallery cards. If a vendor has no approved monochrome version, show the product name in text instead of tinting their mark.
- Always write the product name in text next to the mark. The mark is `aria-hidden`.
- Prototype: neutral grey tiles with text monograms (AWS, GC, MS, DB, SF, DOM, A2A). No vendor artwork is included.
- Legal review: get sign-off from Domino legal before shipping marks in the product.

---

## 11. Open decisions for Nick

1. **Google: one card or two.** The spec uses one "Google Cloud" card naming Vertex AI and Gemini Enterprise. The alternative is two cards sharing one connection (§3.1).
2. **Default sync schedule.** The spec preselects Daily at 02:00, visibly. Off is safer for compute; Daily is more useful.
3. **Auto-advance after detection.** The spec moves from Connect to Check access 1.5 s after detection, with an announcement. Check access to Scope stays manual, so the user reads the results.
4. **Sample imports.** Should sample agents be importable into Fleet at all (labelled and excluded from evidence packs, as specified), or stay in the review screen only?
