# Domino token as the federation identity (spike, 2026-11-12, workspace on se-demo)
Checked from the workspace run token at localhost:8899/access-token. Only claim names and non-secret values were inspected; the token itself was never printed.
- iss: https://se-demo.domino.tech/auth/realms/DominoRealm. Discovery (/.well-known/openid-configuration) and JWKS (/protocol/openid-connect/certs) both return 200 from the public internet path. RS256 with kid.
- aud is a LIST of 6: domino-platform, flyteadmin, toolkit-client, grafana-client, grafana-workload-client, account. azp = domino-play.
- sub = the run's user (here Nick in a workspace). Lifetime 300 s, so every cloud exchange must re-fetch the token per call (cached under 4 min).
- Claims also include preferred_username, email, user_groups, roles, idp_id.

Consequences for every provider:
1. **Audience.** The shared multi-valued aud is weak (any Domino token carries it) and may be rejected outright. Entra federated credentials match a single audience. AWS uses azp when aud is multi-valued (verify). GCP, Snowflake and Databricks accept "any of".
   - Proposed fix: a Domino admin adds a Keycloak client scope or audience mapper "act-federation" that appears only in the ACT service account's tokens.
   - Until then, pin sub + iss + aud=domino-platform and record the weaker pin as a known limitation.
2. **Subject.** sub is whoever owns the run. An App's token belongs to the publisher, and a Job's to the user who started it (verify App behaviour). So the App and the crawl Jobs must run as one dedicated Domino service account (DECISION for Nick), and the trust pins that account's sub.
3. **Spike still owed (live).** One provider end to end (AWS AssumeRoleWithWebIdentity, the cheapest) using a token from a Job and from the published App. This needs a sandbox account from Nick; otherwise the live path stays recorded-HTTP only.
