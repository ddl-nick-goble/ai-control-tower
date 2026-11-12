# ACT setup for Databricks (CLI 0.250 or later): a federated service principal that trusts Domino.
# Idempotent, contains no secret. It prints a connection code to paste into ACT.
set -e
ACCOUNT_ID="${DATABRICKS_ACCOUNT_ID:?set DATABRICKS_ACCOUNT_ID to your account id}"
SP_JSON="$(databricks account service-principals create --display-name act-reader -o json)"
SP_ID="$(echo "$SP_JSON" | python3 -c 'import sys,json; print(json.load(sys.stdin)["id"])')"
SP_CLIENT="$(echo "$SP_JSON" | python3 -c 'import sys,json; print(json.load(sys.stdin)["applicationId"])')"
databricks account service-principal-federation-policy create "$SP_ID" --json '{
  "oidc_policy": {"issuer": "{{ISSUER}}", "audiences": ["{{AUDIENCE}}"], "subject": "{{SUBJECT}}"}}'
databricks account workspaces list -o json > /tmp/act-workspaces.json
CODE="$(python3 - "$ACCOUNT_ID" "$SP_CLIENT" <<'PY'
import base64, json, sys
ws = [{"id": w["workspace_id"], "host": "https://%s.cloud.databricks.com" % w["deployment_name"]}
      for w in json.load(open("/tmp/act-workspaces.json"))]
print(base64.b64encode(json.dumps({"cloud": "aws", "account_id": sys.argv[1], "sp_client_id": sys.argv[2],
                                   "state": "{{STATE}}", "workspaces": ws}).encode()).decode())
PY
)"
echo
echo "Your ACT connection code (not a secret). Paste it into ACT:"
echo "$CODE"
echo "Or open: {{RETURN_URL}}&code=$CODE"
