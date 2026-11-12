"""PO-1.4 paste-anything classifier via POST /api/connections/{id}/paste {text} ->
{recognised, kind, value, message}. See tests/test_po1_connections.py for the full contract."""
import pytest

from test_po1_connections import H, client, create  # noqa: F401

AWS = "arn:aws:iam::123456789012:role/ActReadOnly"
GUID = "3fa85f64-5717-4562-b3fc-2c963f66afa6"


def paste(c, text, provider="aws"):
    d = create(c, provider)
    r = c.post(f"/api/connections/{d['id']}/paste", json={"text": text}, headers=H)
    assert r.status_code == 200, r.text
    return d, r.json()


CASES = [
    ("aws", AWS, "aws_role_arn"),
    ("aws", "  " + AWS + "\n", "aws_role_arn"),
    ("google", "act-demo-project-482311", "gcp_project"),
    ("google", "483920174563", "gcp_project"),
    ("google", "projects/483920174563/locations/global/workloadIdentityPools/domino-pool/providers/domino-oidc",
     "gcp_wif_provider"),
    ("microsoft", GUID, "azure_guid"),
    ("databricks", "dbc-a1b2c3d4-e5f6.cloud.databricks.com", "databricks_url"),
    ("databricks", "https://adb-1234567890123456.7.azuredatabricks.net/", "databricks_url"),
    ("databricks", "https://accounts.cloud.databricks.com/?account_id=" + GUID, "databricks_url"),
    ("snowflake", "https://app.snowflake.com/myorg/myaccount/#/homepage", "snowflake_url"),
    ("snowflake", "myorg-myaccount", "snowflake_account"),
    ("snowflake", "xy12345.us-east-1", "snowflake_account"),
]


@pytest.mark.parametrize("provider,text,kind", CASES)
def test_ac4_classifies(client, provider, text, kind):
    d, r = paste(client, text, provider)
    assert r["recognised"] is True and r["kind"] == kind, r
    assert r["value"] and r["message"]


def test_ac4_recognised_moves_to_trusted(client):
    d, r = paste(client, AWS)
    assert client.get(f"/api/connections/{d['id']}", headers=H).json()["state"] == "trusted"


def test_ac4_databricks_account_id_and_code(client):
    _, r = paste(client, "Databricks account id: " + GUID, "databricks")
    assert r["recognised"] and r["kind"] in ("databricks_account", "azure_guid")
    _, r = paste(client, "act-conn-ABCD-1234-EFGH-5678", "databricks")
    assert r["kind"] in ("databricks_code", "unknown")


def test_ac4_a2a_card_says_use_other(client):
    d, r = paste(client, "https://agents.example.com/.well-known/agent-card.json")
    assert r["kind"] == "a2a_url"
    assert "other" in r["message"].lower()
    assert client.get(f"/api/connections/{d['id']}", headers=H).json()["state"] != "trusted"


@pytest.mark.parametrize("text", ["hello world", "please connect my cloud", "12"])
def test_ac4_unknown_not_recognised_with_examples(client, text):
    d, r = paste(client, text)
    assert r["recognised"] is False and r["kind"] == "unknown"
    assert "not recognised" in r["message"].lower() or "not recognized" in r["message"].lower()
    assert "arn:aws:iam" in r["message"] or "e.g." in r["message"].lower() or "example" in r["message"].lower()
    assert client.get(f"/api/connections/{d['id']}", headers=H).json()["state"] != "trusted"
