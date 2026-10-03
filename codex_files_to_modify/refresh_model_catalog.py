#!/usr/bin/env python3
"""Refresh a Codex model catalog so new model releases are never missed.

Codex reads its model list from the file referenced by model_catalog_json.
That list is a snapshot: when OpenAI adds models, your merged catalog does
not update itself, and the new models stay hidden until you rebuild it.
This script fetches the current official catalog and re-merges it, keeping
every custom model you already had.

Usage (with the local router running):
    python3 refresh_model_catalog.py \
        --current ~/.codex/model-catalogs/models-with-glm.json \
        --client-version 26.930.31730

Or from a fetched copy without the router:
    python3 refresh_model_catalog.py --current models-with-glm.json \
        --official official-models.json

The script reads the ChatGPT token from ~/.codex/auth.json at runtime and
never prints it. After refreshing, restart Codex or open a new chat.
"""

import argparse
import json
import os
import urllib.error
import urllib.request


def fetch_official(endpoint, client_version, auth_file):
    with open(os.path.expanduser(auth_file), encoding="utf-8") as f:
        token = json.load(f)["tokens"]["access_token"]
    url = f"{endpoint.rstrip('/')}/v1/models?client_version={client_version}"
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read()
    except urllib.error.HTTPError as exc:
        raise SystemExit(
            f"fetch failed ({exc.code}): {exc.read()[:400].decode('utf-8', 'replace')}"
        )
    return json.loads(body)


def main():
    parser = argparse.ArgumentParser(description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--current", required=True,
        help="your current merged catalog (custom models are preserved)")
    parser.add_argument("--out", help="output path; defaults to --current")
    parser.add_argument("--official", help="fetched official catalog JSON; skips the network fetch")
    parser.add_argument("--endpoint", default="http://127.0.0.1:4100",
        help="router or backend to fetch the official catalog from")
    parser.add_argument("--client-version",
        help="Codex client version, e.g. 26.930.31730 (required when fetching)")
    parser.add_argument("--auth-file", default="~/.codex/auth.json",
        help="ChatGPT auth file holding tokens.access_token")
    args = parser.parse_args()

    if args.official:
        with open(args.official, encoding="utf-8") as f:
            official = json.load(f)
    else:
        if not args.client_version:
            raise SystemExit("--client-version is required when fetching")
        official = fetch_official(args.endpoint, args.client_version, args.auth_file)

    official_models = official["models"] if isinstance(official, dict) else official
    official_slugs = {m.get("slug") for m in official_models}

    with open(args.current, encoding="utf-8") as f:
        current = json.load(f)
    current_models = current["models"] if isinstance(current, dict) else current
    custom_models = [m for m in current_models if m.get("slug") not in official_slugs]

    merged = {"models": list(official_models) + custom_models}
    out_path = args.out or args.current
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(merged, f, indent=2)
        f.write("\n")

    new_slugs = sorted(official_slugs - {m.get("slug") for m in current_models})
    print(f"wrote {out_path}: {len(merged['models'])} models "
          f"({len(official_models)} official + {len(custom_models)} custom)")
    if new_slugs:
        print(f"new official models: {', '.join(new_slugs)}")


if __name__ == "__main__":
    main()
