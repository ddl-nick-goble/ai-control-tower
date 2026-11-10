"""T-12 e2e: register an external A2A agent and view its passport (AC-11).

Routes: #/register (Register external agent), #/agents/<id> (agent page, also shows the passport).

data-testid contract
  Fleet:     register-external-button, fleet-row (data-agent-id), fleet-source, fleet-status, tier-badge (data-tier)
  Register:  card-url (label "Agent card URL"), criterion-<name> for decision_impact, financial_exposure,
             customer_impact, autonomy, data_sensitivity, reversibility, regulatory_exposure (values 1-5),
             data-scopes (chip input: Enter adds), criterion-<name> is a radiogroup of radios "1".."5", button "Register agent", register-error
  Passport:  passport, passport-identity, passport-skill (one per card skill id), passport-scope (one per scope),
             passport-expiry, passport-signature, passport-status, tier-badge (data-tier)
"""
import datetime
import json
import pathlib
import re

import pytest
from playwright.sync_api import expect

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
CARD = json.loads((ROOT / "fixtures/external/agent-card.json").read_text())
SKILL_IDS = [s["id"] for s in CARD["skills"]]
SCOPES = ["accounts:read", "payments:write"]
CRITERIA = {"decision_impact": 4, "financial_exposure": 5, "customer_impact": 3, "autonomy": 4,
            "data_sensitivity": 4, "reversibility": 3, "regulatory_exposure": 4}


@pytest.fixture
def authed(context):
    context.set_extra_http_headers({"X-Domino-User-Id": "reg-user", "X-Domino-User": "reg-user"})


def _register(page, url):
    page.goto("./")
    page.get_by_test_id("register-external-button").click()
    expect(page).to_have_url(re.compile(r"#/register"))
    page.get_by_label("Agent card URL").fill(url)
    for name, v in CRITERIA.items():
        page.get_by_test_id(f"criterion-{name}").get_by_role("radio", name=str(v), exact=True).click()
    for sc in SCOPES:
        page.get_by_test_id("data-scopes").fill(sc)
        page.get_by_test_id("data-scopes").press("Enter")
    page.get_by_role("button", name="Register agent", exact=True).click()


def _check_passport(page, signature):
    passport = page.get_by_test_id("passport")
    expect(passport).to_be_visible(timeout=15000)
    ident = passport.get_by_test_id("passport-identity")
    expect(ident).to_contain_text(re.compile(re.escape(CARD["provider"]["organization"]) + "|" + re.escape(CARD["name"])))
    skills = passport.get_by_test_id("passport-skill")
    expect(skills).to_have_count(len(SKILL_IDS))
    text = " ".join(skills.all_inner_texts())
    for sid in SKILL_IDS:
        assert sid in text
    scopes = passport.get_by_test_id("passport-scope")
    expect(scopes).to_have_count(len(SCOPES))
    stext = " ".join(scopes.all_inner_texts())
    for sc in SCOPES:
        assert sc in stext
    exp = passport.get_by_test_id("passport-expiry").inner_text()
    m = re.search(r"\d{4}-\d{2}-\d{2}", exp)
    assert m, f"no ISO date in expiry: {exp!r}"
    assert datetime.date.fromisoformat(m.group(0)) > datetime.date.today()
    expect(passport.get_by_test_id("passport-signature")).to_contain_text(signature)
    expect(passport.get_by_test_id("passport-status")).to_contain_text(re.compile("pending_review|Pending review", re.I))
    badge = passport.get_by_test_id("tier-badge")
    expect(badge).to_be_visible()
    assert badge.get_attribute("data-tier") in {"1", "2", "3", "4"}


def test_ac11_register_unsigned(page, authed, shot, card_urls):
    _register(page, card_urls["unsigned"])
    _check_passport(page, "Unsigned")
    shot("then")


def test_ac11_register_signed(page, authed, shot, card_urls):
    _register(page, card_urls["signed"])
    _check_passport(page, re.compile(r"^\s*Signed|Signed", re.I))
    expect(page.get_by_test_id("passport-signature")).not_to_contain_text("Unsigned")
    shot("then")


def test_ac11_fleet_shows_external(page, authed, shot, card_urls):
    _register(page, card_urls["unsigned"])
    expect(page.get_by_test_id("passport")).to_be_visible(timeout=15000)
    page.goto("./#/")
    row = page.get_by_test_id("fleet-row").filter(has_text=CARD["name"]).first
    row.scroll_into_view_if_needed()
    expect(row).to_be_visible()
    expect(row.get_by_test_id("fleet-source")).to_contain_text("external", ignore_case=True)
    expect(row.get_by_test_id("fleet-status")).to_have_text(re.compile(r"^\s*Pending review\s*$"))
    shot("fleet")
    row.get_by_role("link", name=CARD["name"]).first.click()
    expect(page.get_by_test_id("passport")).to_be_visible()
    expect(page.get_by_test_id("passport-skill")).to_have_count(len(SKILL_IDS))
    expect(page.get_by_test_id("agent-status")).to_have_text(re.compile(r"^\s*Pending review\s*$"))
    shot("then")


def test_ac11_external_identity_card_asserted(page, authed, shot, card_urls):
    _register(page, card_urls["signed"])
    expect(page.get_by_test_id("passport")).to_be_visible(timeout=15000)
    asserted = page.get_by_test_id("identity-card-asserted")
    expect(asserted).to_be_visible()
    expect(asserted).to_contain_text(re.compile(r"asserted by the agent card", re.I))
    expect(asserted).to_contain_text(re.compile(r"signed", re.I))
    expect(page.get_by_test_id("identity-flag-no-distinct")).to_have_count(0)
    shot("then")


@pytest.fixture
def tampered_url(tmp_path):
    import socket
    import subprocess
    import sys
    import time
    card = json.loads((ROOT / "fixtures/external/agent-card-signed.json").read_text())
    card["description"] = card["description"] + " (tampered)"
    d = tmp_path / "srv" / ".well-known"
    d.mkdir(parents=True)
    (d / "agent-card.json").write_text(json.dumps(card))
    with socket.socket() as sk:
        sk.bind(("127.0.0.1", 0))
        port = sk.getsockname()[1]
    proc = subprocess.Popen([sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1",
                             "--directory", str(tmp_path / "srv")],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(40):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.5).close()
            break
        except OSError:
            time.sleep(0.25)
    yield f"http://127.0.0.1:{port}/.well-known/agent-card.json"
    proc.terminate()
    proc.wait(timeout=10)


def test_ac11_invalid_signature_warning(page, authed, shot, tampered_url, card_urls):
    _register(page, tampered_url)
    sig = page.get_by_test_id("passport-signature")
    expect(sig).to_be_visible(timeout=15000)
    expect(sig).to_contain_text("Invalid signature")
    expect(sig).to_have_attribute("data-signature", "invalid")
    warn = sig.evaluate("""e => { for (let n = e; n; n = n.parentElement) {
        if (/warn|alert/i.test(n.className || '') || n.getAttribute('data-severity') === 'warning') return true;
      } return false; }""")
    assert warn, "invalid signature has no warning style"
    shot("then")


def test_ac11_signature_attributes(page, authed, shot, card_urls):
    _register(page, card_urls["signed"])
    expect(page.get_by_test_id("passport-signature")).to_have_attribute("data-signature", "signed", timeout=15000)
    _register(page, card_urls["unsigned"])
    expect(page.get_by_test_id("passport-signature")).to_have_attribute("data-signature", "unsigned", timeout=15000)
    shot("then")


def test_ac11_errors_visible(page, authed, shot):
    page.goto("./#/register")
    submit = page.get_by_role("button", name="Register agent", exact=True)
    url = page.get_by_label("Agent card URL")
    url.fill("")
    if submit.is_enabled():
        submit.click()
        expect(page.get_by_test_id("register-error")).to_be_visible()
    else:
        expect(submit).to_be_disabled()
    url.fill("http://169.254.169.254/latest")
    for name, v in CRITERIA.items():
        page.get_by_test_id(f"criterion-{name}").get_by_role("radio", name=str(v), exact=True).click()
    page.get_by_test_id("data-scopes").fill("accounts:read")
    page.get_by_test_id("data-scopes").press("Enter")
    page.get_by_role("button", name="Register agent", exact=True).click()
    err = page.get_by_test_id("register-error")
    expect(err).to_be_visible(timeout=15000)
    expect(err).to_contain_text(re.compile(r"not allowed", re.I))
    assert not re.match(r"\s*\d{3}\b", err.inner_text()), f"error starts with a status code: {err.inner_text()!r}"
    shot("then")


def test_ac11_no_claude_text(page, authed, shot, card_urls):
    page.goto("./#/register")
    expect(page.get_by_label("Agent card URL")).to_be_visible()
    assert "claude" not in page.content().lower()
    _register(page, card_urls["unsigned"])
    expect(page.get_by_test_id("passport")).to_be_visible(timeout=15000)
    assert "claude" not in page.content().lower()
    shot("then")
