# local-codex-proxy

Run Codex against a self-hosted model while keeping the normal ChatGPT models
working in the same session.

Codex has one active model provider per session, and a ChatGPT-backed session
cannot talk to your own model server. This proxy sits between Codex and both
upstreams and routes by model name:

```
Codex (127.0.0.1:4100)
  ├── every other model  ->  https://chatgpt.com/backend-api/codex
  │                          (ChatGPT auth forwarded)
  └── your custom models ->  http://your-model-host:30000/v1
                             (auth stripped, tools translated)
```

The proxy is a single Python file with no dependencies beyond the standard
library. It:

- routes Responses API traffic by model name
- translates Codex Responses Lite tools (nested `namespace` tools,
  `additional_tools`) into SGLang's flat function-tool form, and rewrites tool
  calls in the response back into namespaced form
- strips Codex's JavaScript code-mode wrappers (`functions.*` except the
  executable host tool), which small models cannot reliably produce
- removes `Authorization`/`Cookie` headers before sending requests to your
  model server, so ChatGPT credentials never leave your machine toward it
- passes SSE streaming through chunk by chunk, even when no namespaced tools
  are present
- exposes `GET /health`

## Why it is built this way

Two lessons from getting this working explain the design:

**The model catalog is a replacement, not an addition.** When you set
`model_catalog_json`, Codex uses that file as its complete model list. Every
model in the file shows up in the picker; every model not in it disappears.
That is why the custom models live in a merged catalog that also carries the
normal astra, sol, terra, luna, and review models — leave them out and they
vanish from Codex. See
[codex_files_to_modify/README.md](codex_files_to_modify/README.md) for the
full explanation and the anatomy of a model entry.

**One provider, routed by model name.** Codex has exactly one active model
provider per session. Pointing it straight at your model server makes every
ChatGPT request go there and fail. The router lets one provider serve both
upstreams, keeps ChatGPT credentials away from your model server, and
translates Codex tools into the form your model server accepts. See
[model_proxy/README.md](model_proxy/README.md) for the routing pattern and
what gets rewritten.

**Where the proxy runs.** The proxy must be listening wherever Codex sends
its traffic — Codex's one provider is this router, so while it is down every
model in that session fails, ChatGPT models included. Run it per machine
(this repo's default, with `127.0.0.1`), or run one shared proxy on a
reachable host and point other machines' `base_url` at it. See
[model_proxy/README.md](model_proxy/README.md) for the trade-offs.

## Repository layout

- [model_proxy/](model_proxy/) — the proxy code, the launchd template, and the
  installer, plus [model_proxy/README.md](model_proxy/README.md) on routing
  and tool translation
- [codex_files_to_modify/](codex_files_to_modify/) — what to add to
  `~/.codex/config.toml`, the model-catalog entry, and the catalog merge script
  plus [codex_files_to_modify/README.md](codex_files_to_modify/README.md) on
  why the catalog works the way it does

## Quick deploy (macOS)

### 1. Start the proxy

Manually, to see errors directly:

```bash
cd model_proxy
CODEX_ROUTER_GLM_BASE=http://your-model-host:30000/v1 \
CODEX_ROUTER_GLM_MODEL=your-model-slug \
python3 ./codex_model_router.py
```

Or install it as a launchd service that starts at login and restarts on
crash:

```bash
cd model_proxy
./install.sh http://your-model-host:30000/v1 your-model-slug
curl http://127.0.0.1:4100/health   # {"status":"ok",...}
```

### 2. Merge your model into the model catalog

Codex reads its model list from a JSON catalog. Get a base catalog from your
current Codex install (the file referenced by `model_catalog_json` in your
`~/.codex/config.toml`, or the catalog Codex fetched from its backend), then
merge the entry:

```bash
cd codex_files_to_modify
python3 merge_model_catalog.py --base /path/to/models.json \
    --entry glm-model-entry.json \
    --out ~/.codex/model-catalogs/models-with-glm.json
```

For a different model than GLM, edit `glm-model-entry.json` first: set
`slug` to the exact model ID your server reports at `/v1/models`, the
`display_name`, the `context_window`, and the supported reasoning levels.
To serve several custom models from one proxy, set `CODEX_ROUTER_ROUTES`
(a JSON `{slug: base-url}` mapping) instead of the single-model variables —
see [model_proxy/README.md](model_proxy/README.md).

### 3. Point Codex at the router and the catalog

Add to `~/.codex/config.toml` (see
[config.toml.snippet](codex_files_to_modify/config.toml.snippet) for the full
blocks, including the direct-to-SGLang troubleshooting profile):

```toml
model_catalog_json = "/Users/you/.codex/model-catalogs/models-with-glm.json"
model = "glm-5.3-flash-nvfp4"
model_provider = "local_router"

[model_providers.local_router]
name = "Local model router"
base_url = "http://127.0.0.1:4100/v1"
wire_api = "responses"
requires_openai_auth = true
supports_websockets = false
stream_max_retries = 3
stream_idle_timeout_ms = 120000
```

Keep one provider for all models. A global provider pointed straight at
SGLang makes every ChatGPT request go to SGLang, which fails because ChatGPT
models reject SGLang's tool format. The router exists so you never need that.

### 4. Restart Codex

Codex reads the provider configuration when a session starts. Restart Codex
or open a new chat after changing the config or the router.

## Staying current with model releases

The merged catalog is a snapshot. When OpenAI adds models, your file does
not update itself, and the new models stay hidden until you rebuild it.
After a Codex update, re-merge from the current official catalog:

```bash
cd codex_files_to_modify
python3 refresh_model_catalog.py \
    --current ~/.codex/model-catalogs/models-with-glm.json \
    --client-version 26.930.31730
```

The script fetches the official catalog through the router (or from a
`--official` file without the network), keeps every custom model you already
had, reports which models are new, and writes the merged catalog. Restart
Codex or open a new chat afterwards. To find the current `client-version`,
check the Codex app's About/version screen or the version the app reports.

### 5. Verify the path

```bash
codex --strict-config --help

curl http://127.0.0.1:4100/v1/responses \
  -H 'Content-Type: application/json' \
  --data '{"model":"glm-5.3-flash-nvfp4","input":"Reply with exactly OK","max_output_tokens":128,"store":false,"stream":false}'
```

Expected result: a completed Responses API object containing `OK`.

Then open a new Codex chat, pick your model from the model list, and give it
a small tool-using prompt. The router log at
`/tmp/codex-model-router.log` shows which upstream each request went to.

## Troubleshooting

Isolate whether the failure is your model server, the proxy, or launchd:

```bash
# 1. Is your model alive?
curl http://your-model-host:30000/health
curl http://your-model-host:30000/v1/models

# 2. Is the proxy listening?
lsof -nP -iTCP:4100 -sTCP:LISTEN

# 3. Run the router manually to see the actual error.
python3 ./codex_model_router.py
```

Common causes:

- `Connection refused` from the model host in the router log: your model
  server is down. Restart SGLang (or whatever serves it), then retest.
- launchd says running but port 4100 is closed: the plist points at a Python
  binary or router path that does not exist. Check
  `~/Library/LaunchAgents/com.openai.codex-model-router.plist`, run
  `plutil -lint` on it, and re-run `./install.sh`.
- `502 upstream failure` after a long stall: the upstream stream broke
  mid-response. The proxy forwards what it can; retry the request.
- Requests route to the wrong upstream: the model name in the request must
  match `CODEX_ROUTER_GLM_MODEL` exactly.

## Environment variables

| Variable | Default | Meaning |
| --- | --- | --- |
| `CODEX_ROUTER_HOST` | `127.0.0.1` | listen address |
| `CODEX_ROUTER_PORT` | `4100` | listen port |
| `CODEX_ROUTER_OPENAI_BASE` | `https://chatgpt.com/backend-api/codex` | ChatGPT upstream |
| `CODEX_ROUTER_GLM_BASE` | unset (required) | your model endpoint |
| `CODEX_ROUTER_GLM_MODEL` | `glm-5.3-flash-nvfp4` | model name routed to your endpoint |
| `CODEX_ROUTER_ROUTES` | unset | JSON `{slug: base-url}` mapping for several custom models |

## Provenance

This setup was built and debugged against a real deployment: GLM 5.3 Flash
(NVFP4, SGLang, 1,048,576-token context) on a local GPU box, used inside the
Codex desktop app through this router. The original working configuration and
the debugging history that produced the streaming pass-through and tool
translation fixes came out of that work.
