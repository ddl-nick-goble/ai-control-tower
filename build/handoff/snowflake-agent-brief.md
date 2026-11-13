# Brief for the Snowflake Claude Code: make MARKET_DATA_AGENT discoverable by ACT

## Goal
ACT (AI Control Tower) runs as a Domino App on se-demo.domino.tech. It needs to discover Cortex Agents in Snowflake account KMA35258 (DOMINODATA_PARTNER, Snowsight app.snowflake.com/hvmtatk/dominodata_partner). After discovery it will pull usage and traces, and later call and test the agents. The first target is the existing agent MARKET_AGENT_DEMO.MARKET.MARKET_DATA_AGENT. Do not create a new agent.

## How ACT authenticates: no stored secret
ACT uses Snowflake Workload Identity Federation (WIF).
- A Snowflake SERVICE user, ACT_SVC, trusts Domino's OIDC issuer for one Domino identity.
- On every SQL API or REST call, ACT sends its own Domino token, which lives 5 minutes, as `Authorization: Bearer WIF.OIDC.<jwt>` with `X-Snowflake-Authorization-Token-Type: WORKLOAD_IDENTITY_FEDERATION`.
- There is no password, no PAT and no key pair. To revoke access, `DROP USER ACT_SVC`.

The values below were checked live on 2026-11-13. They are claims only, never the token.
- ISSUER: `https://se-demo.domino.tech/auth/realms/DominoRealm`. It is publicly reachable, and its `.well-known/openid-configuration` returns 200.
- SUBJECT: `68236ccd0758aa07bfffdb83`. This is the `sub` of Domino user nick_goble, who publishes the App and runs its Jobs. If someone else republishes the App, this must change.
- AUDIENCE: `domino-platform`.

## Your job: review, prepare and verify. A human ACCOUNTADMIN runs the security changes.
1. **Pre-checks (read-only).** Report each:
   - `SHOW PARAMETERS LIKE 'NETWORK_POLICY' IN ACCOUNT;` If there is an account network policy, ACT's calls from Domino's egress will be blocked unless they are allowed. Report the policy name only.
   - `SHOW AUTHENTICATION POLICIES;` and any policy set on the account. It must allow WORKLOAD_IDENTITY for service users.
   - `SHOW USERS LIKE 'ACT_SVC';` (expect none) and `SHOW ROLES LIKE 'ACT_READER';`.
   - The exact signature of GET_LATEST_QUOTE: `SHOW USER FUNCTIONS LIKE 'GET_LATEST_QUOTE' IN SCHEMA MARKET_AGENT_DEMO.MARKET;`.
   - The account identifier ACT should use: `SELECT CURRENT_ORGANIZATION_NAME() || '-' || CURRENT_ACCOUNT_NAME();`. Because the account name contains `_`, also confirm that the host `<org>-<account with _ replaced by ->.snowflakecomputing.com` resolves. ACT builds its hostname from this, and underscores in hostnames break TLS.
2. **Review the SQL below line by line** against least privilege.
   - Fix any syntax that's wrong for this account. The lines marked `-- VERIFY` are unconfirmed.
   - Do not widen any grant.
   - Return the final script to Nick.
3. **Nick or Georgi runs it as ACCOUNTADMIN.** KMA35258 is Georgi's partner account, so get his OK first.
4. **Verify as ACCOUNTADMIN after it runs (read-only).** Report each:
   - `DESCRIBE USER ACT_SVC;` shows TYPE=SERVICE and the workload identity.
   - `SHOW GRANTS TO ROLE ACT_READER;` lists exactly the grants in the script.
   - `USE ROLE ACT_READER; SHOW AGENTS IN ACCOUNT;` lists MARKET_DATA_AGENT.
   - `DESCRIBE AGENT MARKET_AGENT_DEMO.MARKET.MARKET_DATA_AGENT;` returns the agent_spec.
   - `SELECT COUNT(*) FROM SNOWFLAKE.ACCOUNT_USAGE.CORTEX_AGENT_USAGE_HISTORY;` works. It may return 0.
5. **Report back.** Give the account identifier, the pre-check results, the final script, the verify results, and anything that failed with the exact error. Never paste tokens.

## The setup SQL (least privilege, scoped to one schema)
```sql
-- ACT setup: a read-only service user that trusts Domino through WIF. Contains no secret. Run as ACCOUNTADMIN.
USE ROLE ACCOUNTADMIN;

-- Dedicated role. Never SYSADMIN, ACCOUNTADMIN, ownership or MANAGE GRANTS.
CREATE ROLE IF NOT EXISTS ACT_READER COMMENT = 'ACT (Domino AI Control Tower) read-only discovery';

-- Small warehouse for ACT's metadata and usage queries only. It suspends after 60 s.
CREATE WAREHOUSE IF NOT EXISTS ACT_WH WAREHOUSE_SIZE = XSMALL AUTO_SUSPEND = 60 AUTO_RESUME = TRUE INITIALLY_SUSPENDED = TRUE;
GRANT USAGE ON WAREHOUSE ACT_WH TO ROLE ACT_READER;

-- Discovery: see the agent and read its spec, versions and grants (SHOW AGENTS, DESCRIBE AGENT).
GRANT USAGE ON DATABASE MARKET_AGENT_DEMO TO ROLE ACT_READER;
GRANT USAGE ON SCHEMA MARKET_AGENT_DEMO.MARKET TO ROLE ACT_READER;
GRANT MONITOR ON AGENT MARKET_AGENT_DEMO.MARKET.MARKET_DATA_AGENT TO ROLE ACT_READER;
GRANT MONITOR ON FUTURE AGENTS IN SCHEMA MARKET_AGENT_DEMO.MARKET TO ROLE ACT_READER;    -- VERIFY: so new agents in this schema show up without another grant

-- What the agent can reach: the tool's semantic view and the custom tool's function (DESCRIBE, GET_DDL), without reading data rows.
GRANT REFERENCES ON SEMANTIC VIEW MARKET_AGENT_DEMO.MARKET.MARKET_SV TO ROLE ACT_READER;  -- VERIFY: the privilege DESCRIBE SEMANTIC VIEW needs
GRANT USAGE ON FUNCTION MARKET_AGENT_DEMO.MARKET.GET_LATEST_QUOTE(VARCHAR) TO ROLE ACT_READER;  -- VERIFY: signature from the pre-check
GRANT MONITOR ON ALL TASKS IN SCHEMA MARKET_AGENT_DEMO.MARKET TO ROLE ACT_READER;        -- to find tasks that call the agent (ASK_MARKET_AGENT)

-- Usage, cost and query history (ACCOUNT_USAGE). SYSADMIN cannot read these today.
GRANT DATABASE ROLE SNOWFLAKE.USAGE_VIEWER TO ROLE ACT_READER;

-- Service user: authenticates only with a Domino token for one identity. No password, no key.
CREATE USER IF NOT EXISTS ACT_SVC
  TYPE = SERVICE
  DEFAULT_ROLE = ACT_READER
  DEFAULT_WAREHOUSE = ACT_WH
  WORKLOAD_IDENTITY = (
    TYPE = OIDC
    ISSUER = 'https://se-demo.domino.tech/auth/realms/DominoRealm'
    SUBJECT = '68236ccd0758aa07bfffdb83'
    OIDC_AUDIENCE_LIST = ('domino-platform')
  )
  COMMENT = 'ACT (Domino) WIF reader; trusts Domino user nick_goble on se-demo';
GRANT ROLE ACT_READER TO USER ACT_SVC;

-- LATER, not now. Only when ACT's "Try it" and agent testing ship (SF-2/SF-3), as a separate reviewed step:
-- GRANT USAGE ON AGENT MARKET_AGENT_DEMO.MARKET.MARKET_DATA_AGENT TO ROLE ACT_READER;    -- call the agent
-- GRANT DATABASE ROLE SNOWFLAKE.CORTEX_USER TO ROLE ACT_READER;                          -- Cortex access to call it (spends credits)
-- GRANT USAGE ON WAREHOUSE MARKET_AGENT_DEMO_WH TO ROLE ACT_READER;                      -- the tools' warehouse
-- Agent traces (AI observability): the read role name is unconfirmed; check Snowflake's docs for the agent monitoring reader role.
```

## Out of scope
- Do not run DATA_AGENT_RUN to test from ACT's role. Calling the agent isn't granted yet.
- Do not create PATs, passwords or key pairs for ACT_SVC.
- Do not change MARKET_DATA_AGENT, its tools or its data.
