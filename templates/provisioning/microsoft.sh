# ACT setup for Azure: a user-assigned managed identity with a federated credential and a read-only role.
# Idempotent, safe to re-run, contains no secret.
set -e
SUB="$(az account show --query id -o tsv)"
TENANT="$(az account show --query tenantId -o tsv)"
RG=act-readonly-rg
az group create -n "$RG" -l eastus -o none
az identity create -g "$RG" -n act-reader -o none
CLIENT_ID="$(az identity show -g "$RG" -n act-reader --query clientId -o tsv)"
PRINCIPAL_ID="$(az identity show -g "$RG" -n act-reader --query principalId -o tsv)"
az identity federated-credential create -g "$RG" --identity-name act-reader --name act-domino \
  --issuer "{{ISSUER}}" --subject "{{SUBJECT}}" --audiences "{{AUDIENCE}}" -o none
az role assignment create --assignee-object-id "$PRINCIPAL_ID" --assignee-principal-type ServicePrincipal \
  --role Reader --scope "/subscriptions/$SUB" -o none
echo
echo "Return to ACT: {{RETURN_URL}}&tenant=${TENANT}&subscription=${SUB}&client=${CLIENT_ID}"
