#!/usr/bin/env python3
"""Add a custom model entry to a Codex models.json catalog.

Usage:
    python3 merge_model_catalog.py --base models.json \
        --entry glm-model-entry.json --out models-with-glm.json

Get the base catalog from your current Codex install (the file referenced by
model_catalog_json in ~/.codex/config.toml, or the catalog Codex fetched from
its backend). The script adds or updates the entry by slug and writes the
merged catalog.
"""

import argparse
import json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True, help="existing models.json catalog")
    parser.add_argument("--entry", required=True, help="single model entry JSON file")
    parser.add_argument("--out", required=True, help="merged catalog output path")
    args = parser.parse_args()

    with open(args.base, encoding="utf-8") as f:
        catalog = json.load(f)
    with open(args.entry, encoding="utf-8") as f:
        entry = json.load(f)

    models = catalog.get("models")
    if not isinstance(models, list):
        raise SystemExit("base catalog has no 'models' list")

    slug = entry["slug"]
    catalog["models"] = [m for m in models if m.get("slug") != slug]
    catalog["models"].append(entry)

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(catalog, f, indent=2)
        f.write("\n")
    print(f"wrote {args.out} ({len(catalog['models'])} models, added/updated {slug})")


if __name__ == "__main__":
    main()
