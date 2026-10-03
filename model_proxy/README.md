# model_proxy: the local model router

`codex_model_router.py` is a single-file, stdlib-only HTTP proxy that Codex
talks to. It routes by model name:

```
Codex (127.0.0.1:4100)
  ├── every other model  ->  https://chatgpt.com/backend-api/codex
  │                          (ChatGPT auth forwarded)
  └── your custom models ->  your model endpoints
                             (auth stripped, tools translated)
```

## Running it

Single model:

```bash
CODEX_ROUTER_GLM_BASE=http://your-model-host:30000/v1 \
CODEX_ROUTER_GLM_MODEL=your-model-slug \
python3 ./codex_model_router.py
```

Several models from one proxy:

```bash
CODEX_ROUTER_ROUTES='{"model-one":"http://host-one:30000/v1","model-two":"http://host-two:8000/v1"}' \
python3 ./codex_model_router.py
```

Or install as a launchd service (macOS) that starts at login and restarts on
crash:

```bash
./install.sh http://your-model-host:30000/v1 your-model-slug
curl http://127.0.0.1:4100/health   # {"status":"ok",...}
```

For a multi-model launchd install, replace the single-model environment
variables in the rendered plist with `CODEX_ROUTER_ROUTES` (the template
shows the shape).

## What the proxy rewrites, and why

- **Tool translation.** Codex sends Responses Lite tools: nested `namespace`
  tools plus `additional_tools` items. SGLang expects a flat function-tool
  list. The proxy flattens the two forms together, hoists
  `additional_tools` into top-level `tools`, and rewrites tool calls in the
  response back into namespaced form so Codex can route them to the right
  tools.
- **Code-mode wrapper removal.** Codex's `functions.*` tools are JavaScript
  wrappers whose source format small models cannot reliably produce. The
  proxy removes them for custom-model requests, keeping the host executable
  tool so the model still has Codex's normal exec path.
- **Auth stripping.** `Authorization` and `Cookie` headers are removed before
  anything is sent to your model server, so ChatGPT credentials never leave
  toward it. The same headers are preserved for the ChatGPT backend.
- **Streaming.** SSE is passed through chunk by chunk when `stream=true`,
  including when no namespaced tools are present. The router reads with
  `read1` so bytes arrive as the model produces them.
- **`GET /health`.** Returns `{"status":"ok","service":"codex-model-router","pid":...}`.

## What the proxy does not do

It only carries model Responses traffic. Web search, browser control, and
other app connectors are host-side capabilities of the Codex app, not proxy
capabilities. If your model needs web access, add a structured host-side
HTTP/search tool; the proxy alone cannot provide it.

## Environment variables

| Variable | Default | Meaning |
| --- | --- | --- |
| `CODEX_ROUTER_HOST` | `127.0.0.1` | listen address |
| `CODEX_ROUTER_PORT` | `4100` | listen port |
| `CODEX_ROUTER_OPENAI_BASE` | `https://chatgpt.com/backend-api/codex` | ChatGPT upstream |
| `CODEX_ROUTER_GLM_BASE` | unset | single custom model endpoint |
| `CODEX_ROUTER_GLM_MODEL` | `glm-5.3-flash-nvfp4` | slug for the single model |
| `CODEX_ROUTER_ROUTES` | unset | JSON `{slug: base-url}` mapping for several models; overrides the single-model vars |
