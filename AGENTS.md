# AGENTS.md

You are a Codex instance working with this repository. The user will give you
a model endpoint and ask you to integrate it into their Codex install, for
example: "I have a model at XYZ endpoint, integrate." This file explains how
to do that end to end.

## What this repo is

- `model_proxy/codex_model_router.py` — a stdlib-only HTTP proxy that Codex
  talks to. It routes by model name: ChatGPT models go to
  `https://chatgpt.com/backend-api/codex` with ChatGPT auth preserved; the
  configured custom model goes to the user's model server with auth stripped
  and tools translated to SGLang's function-tool form.
- `model_proxy/plist.template` + `model_proxy/install.sh` — launchd service
  so the proxy survives terminal closure and restarts on crash.
- `codex_files_to_modify/config.toml.snippet` — the provider blocks to add to
  `~/.codex/config.toml`.
- `codex_files_to_modify/glm-model-entry.json` — the model-catalog entry.
- `codex_files_to_modify/merge_model_catalog.py` — merges the entry into a
  Codex models.json catalog.

## Integration procedure

1. **Gather from the user**: the endpoint URL (an OpenAI-compatible
   `/v1` endpoint, e.g. `http://100.96.198.48:30000/v1`) for each model and
   the exact model slug each server reports. If either is unknown, check the
   server yourself first: `curl <endpoint>/v1/models` and
   `curl <endpoint>/health`.
2. **Update the catalog entry**: edit
   `codex_files_to_modify/glm-model-entry.json` so `slug` matches the server's
   model ID exactly, and set `display_name`, `context_window`, and the
   supported reasoning levels for that model.
3. **Merge the catalog**: get the base catalog from the user's current Codex
   install — the file referenced by `model_catalog_json` in
   `~/.codex/config.toml` if present, otherwise ask the user or fetch the
   catalog Codex pulled from its backend. The merged catalog must keep every
   existing model: `model_catalog_json` replaces Codex's model list
   entirely, so a catalog holding only the custom model hides the other
   models from the picker. Then:
   `python3 codex_files_to_modify/merge_model_catalog.py --base <base.json> --entry codex_files_to_modify/glm-model-entry.json --out ~/.codex/model-catalogs/models-with-glm.json`
4. **Start or restart the proxy**: run
   `model_proxy/install.sh <endpoint> <model-slug>` (macOS launchd), or run
   `CODEX_ROUTER_GLM_BASE=<endpoint> CODEX_ROUTER_GLM_MODEL=<model-slug> python3 model_proxy/codex_model_router.py`
   manually. For several models from one proxy, set `CODEX_ROUTER_ROUTES` to
   a JSON `{slug: base-url}` mapping instead (see `model_proxy/README.md`).
   Verify `curl http://127.0.0.1:4100/health` returns `{"status":"ok",...}`.
5. **Edit `~/.codex/config.toml`**: follow
   `codex_files_to_modify/config.toml.snippet`. Set
   `model_catalog_json` to the merged catalog, `model` to the model slug, and
   `model_provider` to `local_router`. Add the `[model_providers.local_router]`
   block as written. Add the `[model_providers.glm_5_3_flash]` block and the
   `[profiles.glm53flash]` profile only as a troubleshooting path.
6. **Restart Codex or create a new chat**: Codex reads provider config at
   session start.
7. **Verify**: check `codex --strict-config --help`, then
   `curl http://127.0.0.1:4100/v1/responses -H 'Content-Type: application/json' --data '{"model":"<slug>","input":"Reply with exactly OK","max_output_tokens":128,"store":false,"stream":false}'`.
   Expect a completed Responses API object. Then have the user open a new
   chat, pick the model, and run a small tool-using prompt.

## Critical knowledge (do not skip this)

- **One provider per session.** Codex has one active model provider. Never
  set a provider whose base_url is the model server directly as the global
  provider — every ChatGPT request would go to SGLang and fail with
  `additional_tools`-unsupported errors. The local router is the working
  provider for all models.
- **Tool translation.** Codex sends Responses Lite tools (nested
  `namespace` tools and `additional_tools` items). The proxy flattens them
  into SGLang's function-tool form, removes the JavaScript code-mode wrappers
  (`functions.*` except the host executable tool) because small models cannot
  reliably produce them, and rewrites tool names in responses back into
  namespaced form. If a model stalls after a tool result, check the router log
  at `/tmp/codex-model-router.log` before blaming the proxy.
- **Auth handling.** `requires_openai_auth = true` on the provider makes Codex
  attach ChatGPT credentials; the proxy strips `Authorization`/`Cookie`
  before forwarding to the model server and forwards them to the ChatGPT
  backend. Do not remove this from the provider block.
- **Streaming.** The proxy passes SSE through chunk by chunk when
  `stream=true`, including when no namespaced tools are present. If the proxy
  returns 200 with zero body bytes while the model streams directly, the
  proxy version running is older than this repo — restart it with the current
  code.
- **Web search.** The catalog entry advertises `supports_search_tool = false`
  because the proxy only provides model Responses traffic. Browser and other
  app connectors are host-side capabilities, not proxy capabilities. A
  structured host-side HTTP/search tool is the fix if the model needs web
  access; the proxy alone cannot provide it.

## Where the proxy runs

Codex's single provider is the router, so while the proxy is down every
model in that session fails, including ChatGPT models. Two deployment
shapes:

- **Per machine**: install it on every machine running the ChatGPT app, with
  `base_url = "http://127.0.0.1:4100/v1"` on each machine. If one machine's
  proxy is down, only that machine's Codex is affected.
- **One shared proxy**: run it on a single reachable host (the model box, a
  NAS over Tailscale) and point every Codex install's `base_url` at that
  host. That host then carries ChatGPT credentials and becomes a single
  point of failure for every machine's model traffic.

Ask the user which shape they want when integrating; the per-machine shape
is the repo default.

## Key lesson: the catalog is a replacement

When `model_catalog_json` is set, Codex shows exactly the models in that
file and nothing else. A catalog containing only the custom model hides
astra, sol, terra, luna, and the review models. Always merge the custom
entry into a catalog that keeps every existing model, then point
`model_catalog_json` at the merged file. See
`codex_files_to_modify/README.md`.

The merged catalog is a snapshot: new model releases do not appear until it
is rebuilt. The proxy auto-refreshes it when `CODEX_ROUTER_CATALOG_OUT` is
set (the installer enables this when the merged catalog exists), and
`codex_files_to_modify/refresh_model_catalog.py` is the manual fallback.
After either runs, restart Codex or open a new chat to pick up new models.

## Diagnosing failures

Isolate in this order: model server, proxy, launchd.

1. `curl <endpoint>/health` and `curl <endpoint>/v1/models` — is the model up?
2. `lsof -nP -iTCP:4100 -sTCP:LISTEN` — is the proxy listening?
3. Run the router manually — `python3 model_proxy/codex_model_router.py` with
   the env vars — to see the actual exception.
4. If launchd claims running but the port is closed, check
   `~/Library/LaunchAgents/com.openai.codex-model-router.plist` paths with
   `plutil -lint` and re-run the installer.

The router log is `/tmp/codex-model-router.log`. Lines like
`POST /v1/responses model=<name> -> GLM/SGLang` or `-> ChatGPT` show the
routing decision per request.
