# Snowflake Cortex Agent: MARKET_DATA_AGENT (from Nick, 2026-11-13)

Spec source for the SF-* tasks. ✅ = verified live as SYSADMIN in KMA35258; 📖 = documented, not run here (verify before relying).

## The agent
- MARKET_AGENT_DEMO.MARKET.MARKET_DATA_AGENT, account DOMINODATA_PARTNER (locator HVMTATK, SQL id KMA35258), us-west-2. Snowsight app.snowflake.com/hvmtatk/dominodata_partner. Owner SYSADMIN, display name "Market Data Agent".
- Orchestration model: auto. Instructions: response = concise, tool output only, say data is simulated, include trade date; orchestration = latest_quote for one symbol's latest price, market_analyst for aggregates/comparisons/sector/date ranges.
- Tool market_analyst: cortex_analyst_text_to_sql over semantic view MARKET_SV (dims trade_date, symbol, company_name, sector; facts open/high/low/close/volume; metrics avg_close, total_volume, max_high, min_low), warehouse MARKET_AGENT_DEMO_WH.
- Tool latest_quote: generic tool on SQL function GET_LATEST_QUOTE(sym) -> JSON {symbol, company, trade date, close, volume}; 60 s timeout, same warehouse.
- Data: MARKET_DATA 520 rows, 8 symbols (AAPL, MSFT, NVDA, JPM, GS, XOM, PFE, WMT), sectors Technology/Financials/Energy/Healthcare/Consumer Staples, weekdays, ~90 days ending 2026-11-13, static simulated (sine + hash). Ground truth is queryable, so answers are checkable exactly.
- Observed: DATA_AGENT_RUN via RUN_AGENT_QUESTION() answered "MSFT last closed at $394.51"; GET_LATEST_QUOTE('aapl') close 174.96; AGENT_RUN_LOG stores the full raw response (tool-use blocks + final text). Task ASK_MARKET_AGENT assumed suspended (unverified). Agent choosing latest_quote not yet seen in a log.

## Discovery
- ✅ SHOW AGENTS IN ACCOUNT (only agents the role can see: USAGE/MODIFY/MONITOR/OWNERSHIP). ✅ DESCRIBE AGENT -> agent_spec (models, instructions, tools, tool_resources incl. semantic_view, search_service, identifier, warehouse, query_timeout).
- ✅ SHOW SEMANTIC VIEWS IN SCHEMA / DESCRIBE SEMANTIC VIEW. 📖 SHOW CORTEX SEARCH SERVICES. SHOW USER FUNCTIONS / SHOW PROCEDURES / DESCRIBE FUNCTION / GET_DDL('FUNCTION', ...).
- ✅ SHOW GRANTS ON AGENT. 📖 SHOW GRANTS TO ROLE / OF ROLE; SNOWFLAKE.CORTEX_USER database role.
- Tasks: SHOW TASKS + GET_DDL bodies containing DATA_AGENT_RUN (dynamic EXECUTE IMMEDIATE calls only show in text).
- Blind spots: invisible agents, agents outside Snowflake, spec drift (CREATE OR REPLACE; track created_on/version).

## Invocation
- ✅ SQL: SNOWFLAKE.CORTEX.DATA_AGENT_RUN('<db.schema.agent>', '<json body>') (constant body) -> JSON with tool-use blocks + final text.
- 📖 REST: /api/v2/databases/{db}/schemas/{schema}/agents (list, describe, :run), OAuth / key-pair JWT / PAT (ACT uses WIF via backend/connections/federation.py).
- Execution identity = caller's or owner's role; the agent has no auth of its own.

## Usage and traces
- 📖 SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY / INFORMATION_SCHEMA.QUERY_HISTORY() (filter warehouse MARKET_AGENT_DEMO_WH or query_text ILIKE '%DATA_AGENT_RUN%').
- 📖 ACCOUNT_USAGE Cortex usage views; CORTEX_AGENT_USAGE_HISTORY (already queried by adapters/snowflake.py coverage).
- 📖 Agent monitoring / AI observability: threads, tool calls, traces.
- App table AGENT_RUN_LOG (full responses).
- ✅ Limitation: SYSADMIN cannot see SNOWFLAKE.ACCOUNT_USAGE (no IMPORTED PRIVILEGES); needs ACCOUNTADMIN or a grant (e.g. SNOWFLAKE.USAGE_VIEWER).
