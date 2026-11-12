"""Setup artifacts: text rendered from templates/provisioning/ and pre-filled with the Domino trust values."""
import os
from pathlib import Path
from urllib.parse import quote

TEMPLATES = Path(__file__).resolve().parent.parent.parent / "templates" / "provisioning"
DEFAULT_ISSUER = "https://se-demo.domino.tech/auth/realms/DominoRealm"
AUDIENCE = "domino-platform"
TEMPLATE_VERSION = "1"
KINDS = {"aws": "quick_create_url", "google": "cloud_shell", "microsoft": "cloud_shell",
         "databricks": "cli_script", "snowflake": "sql_worksheet"}
FILES = {"aws": "aws.yaml", "google": "google.sh", "microsoft": "microsoft.sh",
         "databricks": "databricks.sh", "snowflake": "snowflake.sql"}


def issuer() -> str:
    return os.environ.get("ACT_DOMINO_ISSUER") or DEFAULT_ISSUER


def subject() -> str:
    return os.environ.get("ACT_SERVICE_ACCOUNT") or "act-svc"


def render(provider: str, state: str, return_url: str) -> str:
    text = (TEMPLATES / FILES[provider]).read_text()
    for k, v in {"ISSUER": issuer(), "AUDIENCE": AUDIENCE, "SUBJECT": subject(), "STATE": state,
                 "RETURN_URL": return_url, "TEMPLATE_VERSION": TEMPLATE_VERSION}.items():
        text = text.replace("{{" + k + "}}", v)
    return text


def build(provider: str, state: str, base_url: str) -> dict:
    """{kind, text, open_url?, return_url}. `base_url` is the App's public URL (no trailing slash)."""
    return_url = f"{base_url}/api/connections/callback?state={state}"
    out = {"kind": KINDS[provider], "text": render(provider, state, return_url), "return_url": return_url,
           "issuer": issuer(), "subject": subject(), "audience": AUDIENCE}
    if provider == "aws":
        template_url = f"{base_url}/api/connections/template?state={state}"
        out["open_url"] = ("https://console.aws.amazon.com/cloudformation/home?region=us-east-1#/stacks/quickcreate"
                           f"?templateURL={quote(template_url, safe='')}&stackName=ACT-ReadOnly"
                           f"&param_OidcIssuerUrl={quote(issuer(), safe='')}&param_OidcAudience={AUDIENCE}"
                           f"&param_OidcSubject={subject()}&param_Nonce={state}")
    elif provider == "google":
        out["open_url"] = "https://shell.cloud.google.com/?show=terminal"
    elif provider == "microsoft":
        out["open_url"] = "https://portal.azure.com/#cloudshell/"
    elif provider == "snowflake":
        out["open_url"] = "https://app.snowflake.com/"
    return out
