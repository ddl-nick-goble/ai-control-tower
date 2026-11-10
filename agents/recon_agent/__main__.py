"""Run the recon agent once from a workspace or Job: python -m agents.recon_agent ["prompt"]

Honours ACT_LLM_PROVIDER (gateway|mock), RECON_AGENT_ID (default local-recon-agent), ACT_TRACES_URL,
BREAK_CLASSIFIER_ENDPOINT_URL / BREAK_CLASSIFIER_ACCESS_KEY.
"""
import os
import sys

DEFAULT_PROMPT = "Reconcile the GL ledger against the custodian ledger and list every break with its classification."


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    os.environ.setdefault("RECON_AGENT_ID", "local-recon-agent")
    from agents.recon_agent.agent import run
    result = run(" ".join(argv) or DEFAULT_PROMPT)
    print(result["summary"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
