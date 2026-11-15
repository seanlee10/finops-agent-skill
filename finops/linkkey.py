import getpass
import os
import sys

from .source import Client, SourceError

M_INTEGRATION = """mutation($i:CreateSandboxIntegrationInput!){createSandboxIntegration(input:$i){
sandboxIntegration{id name provider}}}"""


def integration_input(account_id: str, space_id: str, name: str, key: str) -> dict:
    return {"accountId": account_id, "spaceId": space_id, "provider": "arize", "name": name,
            "env": [{"envVar": "ARIZE_API_KEY", "value": key}]}


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Link a read-only Arize API key as a sandbox integration. "
                                             "The key is read from FINOPS_READONLY_KEY or a hidden prompt and never printed.")
    ap.add_argument("--account-id", required=True)
    ap.add_argument("--space-id", required=True)
    ap.add_argument("--name", default="finops-readonly")
    args = ap.parse_args(argv)
    key = (os.environ.get("FINOPS_READONLY_KEY") or getpass.getpass("Read-only Arize API key: ")).strip()
    if not key:
        print("no key entered", file=sys.stderr)
        return 2
    try:
        out = Client().execute(M_INTEGRATION, {"i": integration_input(args.account_id, args.space_id, args.name, key)})
    except SourceError as e:
        print(f"failed: {e}", file=sys.stderr)
        return 3
    i = out["createSandboxIntegration"]["sandboxIntegration"]
    print(f"created integration {i['name']} ({i['provider']}): {i['id']}")
    return 0
