# Governance API notes for backend/governance.py (T-8)

Discovered live on this deployment on 2026-11-09 as the project owner (nick_goble), through the in-run proxy. Open question 10 is answered: yes, create bundle, attach policy, attach evidence, answer questions, approve and progress stages all work from the user's identity. Not yet verified with a separate service account token.

## Base and auth
- Base: `$DOMINO_API_PROXY/api/governance/v1`, no auth header (proxy adds the user). Fallback: `$DOMINO_API_HOST` plus bearer from `http://localhost:8899/access-token`. Never X-Domino-Api-Key.
- `GET /policy-overviews` returns `{"data":[{id,name,status,version,...}],"meta":{}}` (332 policies on this deployment). `GET /bundles?projectId=` returns `{"data":[...]}`.

## Policies (the policy API takes YAML)
- Files: `policies/act-2026-54.yaml`, `policies/sr-26-2-model.yaml`. Top-level keys `name`, `description`, `stages[]`; each stage has `name`, `evidenceSet[]` (`id: Local.<externalId>`, `name`, `description`, `definition[]` of `{artifactType: input|metadata, details:{type,label,...}}`) and `approvals[]` (`name`, `approvers: [model-gov-org]`, `evidence{id,name,definition}`).
- Create: `POST /policies {"name","description","labels":{}}` -> 200 `{id,status:"Draft",...}`. Then `PUT /policies/{id}/definition {"definition": "<yaml string>"}` -> 200 with the full parsed policy. Then `PUT /policies/{id}/status {"status":"Published"}` -> 200. Only Published policies can be put on a bundle. Published policies are immutable; to change one use `POST /policies/{id}/versions` and `PUT /policy-versions/{id}/definition`, then publish.
- Policy ids (Published, v1.0):
  - ACT 2026-54 Agentic Governance: `dbda3f71-df98-44fb-84f2-a7dcb14b8916`
  - SR 26-2 Model Risk (slim): `8341eac0-8430-4d17-980c-e9af89cdaa83`
- Approver `model-gov-org` resolved to organization id `671a9237a23252621980ba04` on this deployment. Other deployments need that org to exist or the YAML changed.
- `GET /policies/{id}`: `stages[].evidenceSet[]{id (uuid), externalId, name, artifacts[]{id (uuid), artifactType, details{type,label,options}}}` and `stages[].approvals[]{id,name,approvers,evidence{id,externalId,artifacts[]}}`. Map the YAML id (`act-tier`) to uuids through `externalId`, and artifacts by order or `details.label`. Cache this per policy id.
- ACT policy externalIds: act-identity, act-tier, act-manifest, act-actions, act-validation (approval evidence act-validation-signoff), act-monitoring, act-overrides (approval evidence act-approval), act-records. Stage names: Identity and ownership; Tier and criteria; Permitted actions; Validation; Monitoring and findings; Approvals and overrides; Records and retention. Approvals: "Validation sign-off" (in Validation), "Approval to operate" (in Approvals and overrides).
- SR externalIds: sr-model-id, sr-use, sr-validation, sr-monitoring; approval "Model risk approval" (sr-approval) in stage Approval.

## Bundles
- Create: `POST /bundles {"name","projectId","policyId":ACT,"additionalPolicyIds":[SR]}` -> 200. Optional `attachments[]`, `resolveNameOnConflict`. Response fields: `id` (bundle id), `name`, `state` (Active|Archived|Complete), `policyId`, `policyVersionId`, `stage` (name of current primary-policy stage), `stages[]` (all stages of the primary policy: `{bundleId,stageId,stage{id,name},assignee,assignedAt}`), `policies[]` (one per policy: `policyId,policyName,policyVersionId,stage,compliantToCurrentStage,...`), `stageApprovals[]`, `projectId`, `attachments[]` (on GET).
- Add a policy later: `POST /rpc/add-policies-to-bundle {"bundleId","policyIds":[...]}`.
- Read: `GET /bundles/{id}`; list: `GET /bundles?projectId=...` (name filter works).
- Delete: `DELETE /bundles/{id}` -> 204 works (used for cleanup). `PATCH /bundles/{id} {"state":"Archived"}` -> 200 also works.
- PDF report: `GET /bundles/{id}/report` -> 200 application/pdf.

## Attachments (references, not uploads)
- `POST /bundles/{id}/attachments {"type","identifier":{...},"name","description"}` -> 200 `{id,type,identifier,source:"MANUAL",createdAt,createdBy}`. `DELETE .../attachments/{attachmentId}` -> 204.
- Types accepted: Report, ModelVersion, DatasetSnapshotFile, Endpoint, NetAppVolumeSnapshotFile, FlowArtifact, FlowArtifactFile. Any other type (e.g. "File") -> 400.
- Report: `identifier {"branch","commit","source":"git","filename":"<path in repo>"}`. It is a reference to a file at a commit. There is no content upload through this API. Commit and push the evidence file first, then attach with the commit sha. The API does NOT validate existence: a nonexistent filename and a branch/commit mismatch were both accepted (200). The builder must make sure the commit is pushed, or the UI link breaks.
- ModelVersion: `{"name":"<registered model>","version":int}`. A nonexistent model was accepted (200); no validation.
- Endpoint: requires `identifier.name`, `identifier.version`, `identifier.endpointVersionId` (400 otherwise, field-level messages). Not exercised with a live endpoint.
- Attachments are bundle-wide and show in every approval's `approvalTimelineMap`.
- Plan for ACT: tier reasoning, drift findings and validation record are best written as evidence answers (textarea artifacts) and also as a Report file in the evidence pack; manifest and pack files go as Report attachments at a pushed commit.

## Evidence answers
- `POST /rpc/submit-result-to-policy {"bundleId","policyId","evidenceId":<evidenceSet uuid>,"content":{"<artifactUuid>":"value"}}` -> 200 `{"bundle":{...},...}` (updated bundle and computed policy). Radio/text/select values are strings; checkbox/multiSelect arrays. All artifacts are `required:false` in our policies.
- Read back: `GET /results/latest?bundleID=&policyID=` -> `[{id,evidenceId,artifactId,artifactContent,isLatest,createdBy,createdAt}]`.
- Works for approval evidence too (same call with the approval's `evidence.id`).
- `POST /rpc/compute-policy {"bundleId","policyId"}` -> 200.

## Approvals and stages
- `GET /bundles/{id}/approvals` -> `[{id (approval instance id), stageApprovalId, name, bundleId, policyId, policyVersionId, status, approvers[], updatedAt, updatedBy}]`; status PendingSubmission -> PendingReview -> Approved (also ConditionallyApproved, PendingExpiration, Expired). One entry per approval of all attached policies.
- `POST /rpc/publish-approval-event {"bundleId","policyId","projectId","eventType","approvalId":<approval instance id>,"stageApprovalId"}`; eventType RequestSubmitted | RequestApproved | RequestConditionalApproved | RequestRejected | RequestCancelled | ... Returns the approval. Tested: RequestSubmitted -> PendingReview, RequestApproved -> Approved, both 200.
- Separation NOT enforced by the API: the same user who built the bundle submitted and approved (200) even though that user is not shown as an approver. ACT must enforce builder/approver separation itself and mirror the result by publishing the events (use the approver's own token if available, otherwise the ACT service identity, and record the real approver in `meta`).
- Progress: `PATCH /bundles/{id} {"stage":"<stage name>"}` -> 200 and `stage` changes (Identity and ownership -> Tier and criteria worked even with an approval pending). Unknown name -> 500 with message "stage ... not found". Stage moves are not gated by the API; ACT should gate. This moves the primary policy only.
- `PATCH /bundles/{id}/stages/{stageId} {"assignee":{"id","name"}}` -> 200 (assignee only).
- Policy `enforceSequentialOrder` is false in both policies, so no API-side ordering.

## Limits and gotchas
- No file upload to a bundle; Report attachments reference git commits. Files must be pushed first.
- Attachments and model versions are not validated for existence.
- `currentStageInfo` was absent on GET /bundles/{id} here; use `stage` and `GET /approvals`.
- The discovery bundle "act-discovery-test" (id dfa45961-9270-46b6-97f9-b0c8b04051d6) was deleted (204). The two policies were kept (Published) and should be created once; do not recreate on every run. `GET /policy-overviews` and match by name to find them, or take ids from config.
- Backend should use `resolveNameOnConflict` or one bundle per agent id with a stable name like `ACT <agent_id>`.

## T-8b fallback
Not needed for the Governance API itself. Needed only for: uploading evidence file bytes (use pushed git commit references), and a real second-person approval (ACT enforces and mirrors).
