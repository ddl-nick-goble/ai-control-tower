"""Paste-anything: classify whatever the user pasted. Pure function, no I/O."""
import base64
import json
import re
from urllib.parse import unquote, urlparse, parse_qs

GUID = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
PROVIDER_OF = {"aws_role_arn": "aws", "gcp_project": "google", "gcp_wif_provider": "google",
               "azure_guid": "microsoft", "databricks_account": "databricks", "databricks_url": "databricks",
               "databricks_code": "databricks", "snowflake_url": "snowflake", "snowflake_account": "snowflake"}
NAMES = {"aws": "AWS", "google": "Google Cloud", "microsoft": "Microsoft Azure", "databricks": "Databricks",
         "snowflake": "Snowflake"}
EXAMPLES = ("Examples: arn:aws:iam::123456789012:role/ACT-ReadOnly, act-demo-project-482311, "
            "a 36-character Azure ID, https://dbc-a1b2c3d4-e5f6.cloud.databricks.com, myorg-myaccount.")
RECOGNISED_TEXT = {
    "aws_role_arn": "Recognized: AWS role {value}.",
    "gcp_project": "Recognized: Google Cloud project {value}.",
    "gcp_wif_provider": "Recognized: Google Cloud workload identity provider {value}.",
    "azure_guid": "Recognized: Azure ID {value}.",
    "databricks_account": "Recognized: Databricks account {value}.",
    "databricks_url": "Recognized: Databricks address {value}.",
    "databricks_code": "Recognized: Databricks connection code (account {account_id}).",
    "snowflake_url": "Recognized: Snowflake account {value} (from a Snowsight link).",
    "snowflake_account": "Recognized: Snowflake account {value}.",
}


def _hit(kind, value, **extra):
    return {"kind": kind, "value": value, **extra}


def _classify_core(text: str, hint: str):
    t = text.strip()
    d = unquote(t)
    if re.search(r"\.well-known/agent(-card)?\.json|agent-card\.json", d):
        return _hit("a2a_url", t)
    m = re.search(r"arn:aws:iam::(\d{12}):role/[\w+=,.@/-]+", d)
    if m:
        return _hit("aws_role_arn", m.group(0), account=m.group(1))
    m = re.search(r"projects/(\d+)/locations/global/workloadIdentityPools/[\w-]+/providers/[\w-]+", d)
    if m:
        return _hit("gcp_wif_provider", m.group(0), project_number=m.group(1))
    m = re.search(r"app\.snowflake\.com/([\w-]+)/([\w-]+)", d) or None
    if m:
        return _hit("snowflake_url", f"{m.group(1)}-{m.group(2)}")
    m = re.search(r"([\w-]+)\.snowflakecomputing\.com", d)
    if m:
        return _hit("snowflake_account", m.group(1))
    m = re.search(r"[\w.-]*(?:cloud\.databricks\.com|azuredatabricks\.net|gcp\.databricks\.com)\S*", d)
    if m:
        url = m.group(0) if d.startswith("http") else "https://" + m.group(0)
        if d.startswith("http"):
            url = re.search(r"https?://\S+", d).group(0)
        acct = (parse_qs(urlparse(url).query).get("account_id") or [None])[0]
        return _hit("databricks_url", url, account_id=acct)
    if re.fullmatch(r"[A-Za-z0-9+/=_-]{24,}", t):  # base64 JSON connection code
        try:
            body = json.loads(base64.b64decode(t + "=" * (-len(t) % 4), altchars=b"-_" if "-" in t or "_" in t else None))
            if isinstance(body, dict) and body.get("account_id"):
                return _hit("databricks_code", t, account_id=body["account_id"], cloud=body.get("cloud"))
        except Exception:  # noqa: BLE001
            pass
    g = re.search(GUID, d)
    if g:
        return _hit("databricks_account" if hint == "databricks" else "azure_guid", g.group(0).lower())
    if re.fullmatch(r"\d{12}", t) and hint == "aws":
        return {"kind": "unknown", "value": t, "partial": "aws_account_id"}
    if re.fullmatch(r"\d{6,13}", t):
        return _hit("gcp_project", t)
    sf = re.fullmatch(r"[A-Za-z0-9]+-[A-Za-z0-9_]+", t) or re.fullmatch(r"[a-z]{2}\d{5}(\.[a-z0-9-]+){1,2}", t)
    gcp = re.fullmatch(r"[a-z][a-z0-9-]{4,28}[a-z0-9]", t) and re.search(r"[\d-]", t)
    if sf and (hint == "snowflake" or not gcp):
        return _hit("snowflake_account", t)
    if gcp:
        return _hit("gcp_project", t)
    return {"kind": "unknown", "value": t}


def classify(text: str, provider: str = "") -> dict:
    """{recognised, kind, value, message, provider?, wrong_provider?, hint?}. `provider` is the connection's cloud;
    it breaks ties for bare ids and decides whether a recognised value can be used on this connection."""
    r = _classify_core(text or "", provider)
    kind = r["kind"]
    if kind == "a2a_url":
        return {**r, "recognised": False, "hint": "use_other",
                "message": "That is an A2A agent card URL. Add it from Other: add an agent by URL."}
    if kind == "unknown":
        if r.get("partial") == "aws_account_id":
            return {**r, "recognised": False, "message": "Not recognised yet: that looks like an AWS account ID. "
                    "ACT needs the role too: paste the role ARN from the stack's Outputs tab, "
                    "e.g. arn:aws:iam::123456789012:role/ACT-ReadOnly."}
        return {**r, "recognised": False,
                "message": "Not recognised. ACT couldn't work out what that is. Paste a role ARN, a Finish or Return "
                           "link, a connection code or an account URL. " + EXAMPLES}
    owner = PROVIDER_OF[kind]
    msg = RECOGNISED_TEXT[kind].format(**{**r, "account_id": r.get("account_id") or "unknown"})
    if provider and owner != provider:
        return {**r, "recognised": False, "wrong_provider": True, "provider": owner,
                "message": f"{msg} That looks like a {NAMES[owner]} value, but this connection is "
                           f"{NAMES.get(provider, provider)}. Connect {NAMES[owner]} instead."}
    return {**r, "recognised": True, "provider": owner, "message": msg}
