# ACT setup for Google Cloud: keyless trust with Domino. Idempotent, safe to re-run, contains no secret.
set -e
PROJECT="$(gcloud config get-value project)"
PROJECT_NUMBER="$(gcloud projects describe "$PROJECT" --format='value(projectNumber)')"
gcloud services enable sts.googleapis.com iamcredentials.googleapis.com agentregistry.googleapis.com \
  discoveryengine.googleapis.com aiplatform.googleapis.com cloudresourcemanager.googleapis.com cloudasset.googleapis.com
gcloud iam workload-identity-pools create act-pool --location=global 2>/dev/null || true
gcloud iam workload-identity-pools providers create-oidc act-domino --location=global \
  --workload-identity-pool=act-pool --issuer-uri="{{ISSUER}}" --allowed-audiences="{{AUDIENCE}}" \
  --attribute-mapping="google.subject=assertion.sub" \
  --attribute-condition="assertion.sub=='{{SUBJECT}}'" 2>/dev/null || true
gcloud iam service-accounts create act-reader --display-name="ACT read-only" 2>/dev/null || true
SA="act-reader@${PROJECT}.iam.gserviceaccount.com"
gcloud iam service-accounts add-iam-policy-binding "$SA" --role=roles/iam.workloadIdentityUser \
  --member="principal://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/act-pool/subject/{{SUBJECT}}"
for ROLE in roles/agentregistry.viewer roles/discoveryengine.viewer roles/aiplatform.viewer roles/cloudasset.viewer; do
  gcloud projects add-iam-policy-binding "$PROJECT" --member="serviceAccount:$SA" --role="$ROLE" --condition=None >/dev/null
done
echo
echo "Finish in ACT: {{RETURN_URL}}&project=${PROJECT_NUMBER}&wif=projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/act-pool/providers/act-domino"
