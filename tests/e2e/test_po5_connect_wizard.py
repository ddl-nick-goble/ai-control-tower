"""PO-5 e2e: "Connect a provider" UI part 1 (gallery and wizard steps 1-3). Spec: build/design/provider-onboarding.md
sections 2, 3.1-3.4, 4.1-4.6, 5, 8 (testids), 9; plan.md Wave PC; provider-onboarding-plan.md PO-5.2..PO-5.5.

ACs covered: PO-5.1 (Connections is the gallery, /register redirects), PO-5.2 (gallery cards and states), PO-5.3
(HandshakePanel, live detection, paste-anything, other ways), PO-5.4 (permission checklist, scope tree), PO-5.5
(loading/empty/error states, no horizontal scroll, focus, reduced motion).
"""
import re

import pytest
from playwright.sync_api import expect

from _waits import settle

HEADERS = {"X-Domino-User-Id": "po5-user", "X-Domino-User": "po5-user", "Content-Type": "application/json"}
PROVIDERS = ["aws", "google", "microsoft", "databricks", "snowflake"]


@pytest.fixture(scope="module")
def base_url(apps):
    # sample connections trust themselves after 1 s instead of 3 s
    return apps(extra_env={"ACT_SAMPLE_TRUST_DELAY_S": "1"}).url


@pytest.fixture
def browser_context_args(base_url):
    return {"base_url": base_url, "viewport": {"width": 1440, "height": 900}}


@pytest.fixture(autouse=True)
def authed(context):
    context.set_extra_http_headers({"X-Domino-User-Id": "po5-user", "X-Domino-User": "po5-user"})


# ---- helpers ------------------------------------------------------------------------------------------------------
def _create(context, provider, sample=False):
    r = context.request.post("api/connections", headers=HEADERS, data={"provider": provider, "sample": sample})
    assert r.ok, r.text()
    return r.json()


def _trusted_sample(page, context, provider):
    """A sample connection that has already trusted itself (the page polls the API until it leaves draft/waiting)."""
    c = _create(context, provider, sample=True)
    page.goto("./")
    page.wait_for_function(
        """id => fetch('api/connections/' + id).then(r => r.json()).then(j => !['draft', 'waiting'].includes(j.state))""",
        arg=c["id"], timeout=20000)
    return c


def _no_hscroll(page):
    return page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")


def _heading(page):
    return page.get_by_test_id("step-heading")


def _fix_rows(page):
    return page.get_by_test_id(re.compile(r"^permission-fix-(?!.*-action$)"))


def _item_rows(page):
    return page.get_by_test_id(re.compile(r"^permission-item-(?!.*-status$)"))


# ---- PO-5.1 -------------------------------------------------------------------------------------------------------
def test_po51_nav_unchanged_and_connections_is_gallery(page, shot):
    page.goto("./#/")
    nav = page.get_by_role("navigation", name="Primary")
    names = [t.strip() for t in nav.get_by_role("link").all_inner_texts()]
    assert names[:6] == ["Home", "Fleet", "Findings", "Policies", "Connections", "Audit"], names
    page.get_by_test_id("nav-connections").click()
    expect(page).to_have_url(re.compile(r"#/connections$"))
    expect(page.get_by_test_id("provider-gallery")).to_be_visible()
    expect(page.get_by_test_id("empty-state")).to_have_count(0)
    shot("then")


def test_po51_register_redirects_to_add_by_url(page, shot):
    page.goto("./#/register")
    expect(page).to_have_url(re.compile(r"#/connections/add-by-url$"))
    expect(page.get_by_role("heading", level=1)).to_be_visible()
    expect(page.get_by_test_id("card-url")).to_be_visible()
    shot("then")


def test_po51_register_flow_lives_at_add_by_url(page, shot):
    page.goto("./#/connections/add-by-url")
    expect(page.get_by_test_id("register-page")).to_be_visible()
    expect(page.get_by_role("heading", level=1, name="Register external agent")).to_be_visible()
    expect(page.get_by_test_id("card-url")).to_be_visible()
    shot("then")


def test_po51_other_card_goes_to_add_by_url(page, shot):
    page.goto("./#/connections")
    page.get_by_test_id("provider-card-other-connect").click()
    expect(page).to_have_url(re.compile(r"#/connections/add-by-url$"))
    expect(page.get_by_test_id("card-url")).to_be_visible()
    shot("then")


# ---- PO-5.2 gallery -----------------------------------------------------------------------------------------------
def test_po52_gallery_six_cards_plus_other(page, shot):
    page.goto("./#/connections")
    expect(page.get_by_test_id("provider-gallery")).to_be_visible()
    expect(page.get_by_test_id("domino-connection-card")).to_be_visible()
    expect(page.get_by_test_id("domino-connection-status")).to_contain_text("Connected")
    for p in PROVIDERS + ["other"]:
        card = page.get_by_test_id(f"provider-card-{p}")
        expect(card).to_be_visible()
        expect(card.get_by_role("heading")).to_be_visible()
    for p in PROVIDERS:
        expect(page.get_by_test_id(f"provider-card-{p}-status")).to_have_text(re.compile("Not connected", re.I))
        expect(page.get_by_test_id(f"provider-card-{p}-connect")).to_be_visible()
        expect(page.get_by_test_id(f"provider-card-{p}-sample")).to_be_visible()
    expect(page.get_by_test_id("provider-card-google")).to_contain_text("Vertex AI Agent Runtime")
    expect(page.get_by_test_id("provider-card-google")).to_contain_text("Gemini Enterprise")
    expect(page.get_by_test_id("provider-card-google")).to_contain_text("Agent Registry")
    expect(page.get_by_test_id("provider-card-aws")).to_contain_text("Bedrock AgentCore")
    shot("then")


def test_po52_sample_connection_shows_sample_badge(page, context, shot):
    _create(context, "snowflake", sample=True)
    page.goto("./#/connections")
    card = page.get_by_test_id("provider-card-snowflake")
    expect(card.get_by_test_id("sample-badge")).to_be_visible()
    expect(page.get_by_test_id("provider-card-snowflake-status")).not_to_have_text(re.compile(r"^\s*Connected", re.I))
    expect(page.get_by_test_id("provider-card-snowflake-connect")).to_have_text(re.compile("Connect real Snowflake"))
    expect(page.get_by_test_id("provider-card-snowflake-sample-remove")).to_be_visible()
    shot("then")


def test_po52_live_connected_card_state(page, context, shot):
    """A trusted live connection leaves 'Not connected': the card shows its own state, not the grey default."""
    c = _create(context, "databricks")
    r = context.request.post(f"api/connections/{c['id']}/paste", headers=HEADERS,
                             data={"text": "https://dbc-a1b2c3d4-e5f6.cloud.databricks.com"})
    assert r.ok, r.text()
    page.goto("./#/connections")
    status = page.get_by_test_id("provider-card-databricks-status")
    expect(status).not_to_have_text(re.compile("Not connected", re.I))
    expect(page.get_by_test_id("provider-card-databricks-connect")).not_to_have_text(re.compile(r"^\s*Connect Databricks$"))
    shot("then")


# ---- PO-5.3 / 5.4 AWS sample walk ----------------------------------------------------------------------------------
def test_po53_aws_sample_walk(page, shot):
    page.goto("./#/connections")
    page.get_by_test_id("provider-card-aws-sample").click()

    # step 1
    expect(page.get_by_test_id("wizard-aws")).to_be_visible()
    expect(page.get_by_test_id("step-rail")).to_be_visible()
    expect(_heading(page)).to_contain_text("Connect AWS")
    panel = page.get_by_test_id("handshake-panel")
    expect(panel).to_be_visible()
    expect(page.get_by_test_id("handshake-primary")).to_be_visible()
    expect(page.get_by_test_id("handshake-command")).to_be_visible()
    expect(page.get_by_test_id("handshake-copy")).to_be_visible()
    for heading in ("ACT will be able to read", "ACT can't", "What ACT stores", "Who can do this"):
        expect(page.get_by_text(heading, exact=False).first).to_be_visible()
    expect(page.get_by_role("button", name=re.compile(r"I'm done|Verify", re.I))).to_have_count(0)
    listener = page.get_by_test_id("handshake-listener-status")
    expect(listener).to_have_attribute("aria-live", re.compile("polite|assertive"))
    shot("step1")
    # detection with no click: the listener reaches detected, then the wizard advances to step 2 on its own
    expect(listener).to_contain_text(re.compile("detected", re.I), timeout=20000)
    expect(page).to_have_url(re.compile(r"#/connect/aws/[^/]+/access"), timeout=20000)

    # step 2
    expect(_heading(page)).to_contain_text("Check access")
    expect(_heading(page)).to_be_focused()
    expect(page.get_by_test_id("permission-checklist")).to_be_visible()
    expect(_fix_rows(page)).to_have_count(1)
    expect(page.get_by_test_id(re.compile(r"^permission-fix-.*-action$"))).to_have_count(1)
    expect(page.get_by_test_id("permission-recheck")).to_be_visible()
    shot("step2-missing")
    page.get_by_test_id("permission-recheck").click()
    expect(_fix_rows(page)).to_have_count(0)
    statuses = page.get_by_test_id(re.compile(r"^permission-item-.*-status$"))
    for i in range(statuses.count()):
        expect(statuses.nth(i)).not_to_contain_text(re.compile("Missing", re.I))
    expect(page.get_by_test_id("permission-summary")).to_contain_text(re.compile("passed", re.I))
    page.get_by_test_id("permission-continue").click()

    # step 3
    expect(page).to_have_url(re.compile(r"#/connect/aws/[^/]+/scope"))
    expect(_heading(page)).to_contain_text("Choose scope")
    expect(_heading(page)).to_be_focused()
    tree = page.get_by_test_id("scope-tree")
    expect(tree).to_be_visible()
    boxes = tree.get_by_role("checkbox")
    n = boxes.count()
    assert n >= 3, n
    for i in range(n):
        expect(boxes.nth(i)).to_be_checked()
    expect(page.get_by_test_id("scope-summary")).to_contain_text(re.compile(r"\d+ of \d+"))
    expect(page.get_by_test_id(re.compile(r"^scope-count-"))).not_to_have_count(0)
    expect(page.get_by_test_id("scope-continue")).to_be_enabled()
    # select-all toggles
    page.get_by_test_id("scope-select-all").click()
    for i in range(n):
        expect(boxes.nth(i)).not_to_be_checked()
    expect(page.get_by_test_id("scope-continue")).to_be_disabled()
    page.get_by_test_id("scope-select-all").click()
    for i in range(n):
        expect(boxes.nth(i)).to_be_checked()
    shot("then")
    page.get_by_test_id("scope-continue").click()
    expect(page).not_to_have_url(re.compile(r"/scope"))
    expect(_heading(page)).to_contain_text(re.compile("Scan", re.I))


def test_po54_scope_tree_keyboard(page, context, shot):
    c = _trusted_sample(page, context, "google")
    page.goto(f"./#/connect/google/{c['id']}/scope")
    boxes = page.get_by_test_id("scope-tree").get_by_role("checkbox")
    expect(boxes.first).to_be_visible()
    first = boxes.first
    first.focus()
    expect(first).to_be_focused()
    expect(first).to_be_checked()
    page.keyboard.press("Space")
    expect(first).not_to_be_checked()
    page.keyboard.press("Space")
    expect(first).to_be_checked()
    # a disclosure toggle carries aria-expanded and is reachable by keyboard
    toggle = page.get_by_test_id("scope-tree").locator("[aria-expanded]").first
    toggle.focus()
    before = toggle.get_attribute("aria-expanded")
    page.keyboard.press("Enter")
    expect(toggle).not_to_have_attribute("aria-expanded", before)
    shot("then")


# ---- PO-5.3 paste-anything ----------------------------------------------------------------------------------------
def test_po53_paste_role_arn_recognised_and_moves_on(page, shot):
    page.goto("./#/connect/aws")
    expect(page.get_by_test_id("handshake-panel")).to_be_visible()
    box = page.get_by_test_id("paste-anything-input")
    box.fill("arn:aws:iam::123456789012:role/ACT-ReadOnly")
    result = page.get_by_test_id("paste-anything-result")
    expect(result).to_contain_text("Recognized")
    expect(result).to_contain_text("ACT-ReadOnly")
    expect(result).to_have_attribute("aria-live", "polite")
    page.get_by_test_id("paste-anything-use").click()
    expect(page).to_have_url(re.compile(r"#/connect/aws/[^/]+/access"))
    expect(_heading(page)).to_contain_text("Check access")
    shot("then")


def test_po53_paste_a2a_url_points_to_other(page, shot):
    page.goto("./#/connect/aws")
    page.get_by_test_id("paste-anything-input").fill("https://agents.example.com/.well-known/agent-card.json")
    result = page.get_by_test_id("paste-anything-result")
    expect(result).to_contain_text("A2A")
    expect(result.get_by_role("link", name=re.compile("Other|add an agent by URL", re.I))).to_be_visible()
    expect(page).to_have_url(re.compile(r"#/connect/aws$"))
    shot("then")


def test_po53_paste_nonsense_is_a_plain_error(page, shot):
    page.goto("./#/connect/aws")
    box = page.get_by_test_id("paste-anything-input")
    box.fill("zzz not a thing !!")
    expect(page.get_by_test_id("paste-anything-error")).to_contain_text("couldn't recognize")
    expect(box).to_have_attribute("aria-invalid", "true")
    shot("then")


# ---- callback landing ---------------------------------------------------------------------------------------------
def test_po53_callback_lands_on_wizard_step2(page, context, shot):
    token = _create(context, "aws")["state_token"]
    page.goto(f"./#/connect/aws/callback?state={token}")
    expect(page.get_by_test_id("wizard-aws")).to_be_visible()
    expect(page).to_have_url(re.compile(r"#/connect/aws/[^/]+/access"))
    expect(_heading(page)).to_contain_text("Check access")
    assert not page.locator("body").inner_text().lstrip().startswith("{")
    expect(page.get_by_test_id("permission-checklist")).to_be_visible()
    shot("then")


def test_po53_callback_bad_state_is_plain_language(page, shot):
    page.goto("./#/connect/aws/callback?state=not-a-real-token")
    alert = page.get_by_role("alert")
    expect(alert).to_be_visible()
    expect(alert).to_contain_text(re.compile("start the connection again|recognise|recognize", re.I))
    body = page.locator("body").inner_text()
    assert '"ok"' not in body and '"detail"' not in body and "Traceback" not in body
    shot("then")


# ---- other ways to connect ----------------------------------------------------------------------------------------
def test_po53_other_ways_reveals_fallbacks_and_flags_secrets(page, shot):
    page.goto("./#/connect/aws")
    expect(page.get_by_test_id("handshake-panel")).to_be_visible()
    key = page.get_by_test_id("fallback-key")
    expect(key).not_to_be_visible()  # closed by default
    page.get_by_test_id("other-ways-toggle").click()
    expect(page.get_by_test_id("other-ways")).to_be_visible()
    expect(key).to_be_visible()
    expect(key).to_contain_text("Not recommended")
    expect(key).to_contain_text(re.compile("long-lived secret", re.I))
    expect(page.get_by_test_id("fallback-externalid")).to_be_visible()
    expect(page.get_by_test_id("template-download")).to_be_visible()
    shot("then")


# ---- per-provider step 1 ------------------------------------------------------------------------------------------
@pytest.mark.parametrize("provider,phrases", [
    ("aws", ["CloudFormation", "Open AWS console"]),
    ("google", ["Cloud Shell", "Finish in ACT", "Vertex AI"]),
    ("microsoft", ["Cloud Shell", "Return to ACT"]),
    ("databricks", ["Connection code", "setup script"]),
    ("snowflake", ["Snowsight", "Finish in ACT"]),
])
def test_po53_step1_per_provider(page, shot, provider, phrases):
    page.goto(f"./#/connect/{provider}")
    expect(page.get_by_test_id(f"wizard-{provider}")).to_be_visible()
    expect(page.get_by_test_id("handshake-panel")).to_be_visible()
    expect(page.get_by_role("heading", level=1)).to_have_count(1)
    body = page.get_by_test_id(f"wizard-{provider}")
    for ph in phrases:
        expect(body).to_contain_text(re.compile(re.escape(ph), re.I))
    if provider == "databricks":
        expect(page.get_by_test_id("handshake-listener")).to_have_count(0)
    shot("then")


# ---- PO-5.5 -------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("width,provider", [(1024, "google"), (1440, "microsoft")])
def test_po55_no_horizontal_scroll_gallery_and_steps(page, context, shot, width, provider):
    page.set_viewport_size({"width": width, "height": 800})
    page.goto("./#/connections")
    expect(page.get_by_test_id("provider-gallery")).to_be_visible()
    settle(page)
    assert _no_hscroll(page), "gallery"
    page.goto(f"./#/connect/{provider}")
    expect(page.get_by_test_id("handshake-panel")).to_be_visible()
    settle(page)
    assert _no_hscroll(page), "step 1"
    c = _trusted_sample(page, context, provider)
    page.goto(f"./#/connect/{provider}/{c['id']}/access")
    expect(page.get_by_test_id("permission-checklist")).to_be_visible()
    expect(_item_rows(page).first).to_be_visible()
    settle(page)
    assert _no_hscroll(page), "step 2"
    page.goto(f"./#/connect/{provider}/{c['id']}/scope")
    expect(page.get_by_test_id("scope-tree")).to_be_visible()
    settle(page)
    assert _no_hscroll(page), "step 3"
    shot("then")


def test_po55_focus_moves_to_step_heading_on_step_change(page, context, shot):
    c = _trusted_sample(page, context, "microsoft")
    page.goto(f"./#/connect/microsoft/{c['id']}/access")
    page.get_by_test_id("permission-recheck").click()
    expect(_fix_rows(page)).to_have_count(0)
    page.get_by_test_id("permission-continue").click()
    expect(page).to_have_url(re.compile(r"/scope"))
    expect(_heading(page)).to_be_focused()
    shot("then")


def test_po55_gallery_loading_state(page, shot):
    held = []
    page.route("**/api/connections", lambda route: held.append(route))
    page.goto("./#/connections")
    expect(page.get_by_test_id("provider-gallery")).to_have_attribute("aria-busy", "true")
    shot("then")
    for r in held:
        r.continue_()
    expect(page.get_by_test_id("provider-gallery")).not_to_have_attribute("aria-busy", "true")


def test_po55_gallery_error_state_is_designed(page, shot):
    page.route("**/api/connections", lambda route: route.fulfill(
        status=500, content_type="application/json", body='{"detail":"boom internal"}'))
    page.goto("./#/connections")
    expect(page.get_by_text("Connections didn't load")).to_be_visible()
    expect(page.get_by_role("button", name="Try again")).to_be_visible()
    body = page.locator("body").inner_text()
    assert "boom internal" not in body and '{"detail"' not in body
    shot("then")


def test_po55_step1_error_state_is_designed(page, shot):
    page.route("**/api/connections", lambda route: route.fulfill(
        status=500, content_type="application/json", body='{"detail":"boom internal"}')
        if route.request.method == "POST" else route.continue_())
    page.goto("./#/connect/aws")
    expect(page.get_by_test_id("handshake-error")).to_be_visible()
    expect(page.get_by_test_id("handshake-retry")).to_be_visible()
    body = page.locator("body").inner_text()
    assert "boom internal" not in body and '{"detail"' not in body
    shot("then")


def test_po55_step2_error_state_is_designed(page, context, shot):
    token = _create(context, "aws")["state_token"]
    page.route("**/api/connections/*/check", lambda route: route.fulfill(
        status=500, content_type="application/json", body='{"detail":"boom internal"}'))
    page.goto(f"./#/connect/aws/callback?state={token}")
    expect(page.get_by_text(re.compile("couldn't run the checks", re.I))).to_be_visible()
    expect(page.get_by_test_id("permission-recheck")).to_be_visible()
    assert "boom internal" not in page.locator("body").inner_text()
    shot("then")


def test_po55_step3_empty_state_is_designed(page, context, shot):
    token = _create(context, "aws")["state_token"]
    page.goto(f"./#/connect/aws/callback?state={token}")
    expect(_heading(page)).to_contain_text("Check access")
    cid = re.search(r"/connect/aws/([^/]+)/access", page.url).group(1)
    page.route("**/api/connections/*/scopes", lambda route: route.fulfill(
        status=200, content_type="application/json", body='{"tree": []}'))
    page.goto(f"./#/connect/aws/{cid}/scope")
    expect(page.get_by_text(re.compile("No agents found", re.I))).to_be_visible()
    expect(page.get_by_role("button", name=re.compile("Check access", re.I))).to_be_visible()
    shot("then")


def test_po55_step3_error_state_is_designed(page, context, shot):
    c = _trusted_sample(page, context, "aws")
    page.route("**/api/connections/*/scopes", lambda route: route.fulfill(
        status=500, content_type="application/json", body='{"detail":"boom internal"}'))
    page.goto(f"./#/connect/aws/{c['id']}/scope")
    expect(page.get_by_role("alert")).to_be_visible()
    assert "boom internal" not in page.locator("body").inner_text()
    shot("then")


def test_po55_reduced_motion_has_no_looping_animation(page, shot):
    page.emulate_media(reduced_motion="reduce")
    page.goto("./#/connect/aws")
    expect(page.get_by_test_id("handshake-listener")).to_be_visible()
    settle(page)
    looping = page.evaluate("""() => document.getAnimations().filter(a => {
        const t = a.effect && a.effect.getTiming(); return t && t.iterations === Infinity && t.duration > 0; }).length""")
    assert looping == 0, looping
    shot("then")
