# Codex config files: what to change and why

This folder holds the two things you modify inside a Codex install: the
model catalog and `~/.codex/config.toml`. Understanding why each change
exists makes the setup easy to adapt to any model.

## The model catalog is a replacement, not an addition

This is the most important thing to know.

When you set `model_catalog_json` in `~/.codex/config.toml`, Codex uses that
file as its **complete** model list. Every model in the file shows up in the
model picker; every model not in the file disappears. If you write a catalog
containing only your custom model, astra, sol, terra, luna, and the review
models all vanish from Codex.

That is why this repo ships a **merge** step instead of a finished catalog:
take the catalog Codex already uses, add your model's entry to it, and point
`model_catalog_json` at the merged result. Then both sets of models are
visible in the same session.

```bash
python3 merge_model_catalog.py --base /path/to/your/models.json \
    --entry glm-model-entry.json --out ~/.codex/model-catalogs/models-with-glm.json
```

Where the base catalog comes from:

- if your config already sets `model_catalog_json`, that file is the base;
- otherwise Codex fetched its catalog from its backend when it started; copy
  the catalog your install uses, or ask a Codex agent to retrieve it.

## Anatomy of a model entry

`glm-model-entry.json` is a worked example for GLM 5.3 Flash served by
SGLang. To integrate a different model, copy it and change these fields.
Each field controls something visible or behavioral in Codex:

| Field | What it controls |
| --- | --- |
| `slug` | The model ID. Must exactly match what your server reports at `/v1/models`, the `model` value in config, and the router's `CODEX_ROUTER_GLM_MODEL` (or a key in `CODEX_ROUTER_ROUTES`). |
| `display_name` | The name shown in Codex's model picker. |
| `context_window` | Token budget Codex assumes for context management. |
| `supported_reasoning_levels` + `default_reasoning_level` | Which reasoning-effort options appear and the default. Only include levels your model actually supports. |
| `shell_type` | `unified_exec` gives the model Codex's unified exec tool. |
| `supported_in_api` | Whether the model is callable through the API path. Keep `true`. |
| `visibility` | `list` shows the model in the picker; `hidden` hides it. |
| `input_modalities` | What the model accepts: `text`, or `text` and `image`. |
| `truncation_policy` | How Codex truncates long context for this model. |
| `supports_search_tool` | Web-search tool availability. Keep `false`: the proxy only carries model Responses traffic, and browser and app connectors are host-side capabilities. |
| `model_messages.instructions_template` | The system prompt Codex sends to the model. The GLM entry carries the full Codex agent instructions; keep this unless your model needs a different persona. |

## The provider blocks

See [config.toml.snippet](config.toml.snippet) for the full blocks. Two
points explain why they look the way they do:

**One provider for all models.** Codex has exactly one active model provider
per session. If you point that provider straight at your model server, every
ChatGPT request also goes there and fails, because ChatGPT models reject
SGLang's tool format. The local router exists so you never need that: one
provider, routed by model name.

**`requires_openai_auth = true` on the router provider.** This makes Codex
attach its ChatGPT credentials to requests. The proxy strips
`Authorization`/`Cookie` before forwarding anything to your model server and
keeps the credentials for the ChatGPT backend. Do not remove this from the
provider block.

**`wire_api = "responses"`.** Codex speaks the Responses API. SGLang serves
Responses at `/v1/responses`. If your model server only speaks chat
completions, set `wire_api = "chat"`; the proxy forwards paths unchanged, but
the tool translation assumes the Responses format, so a chat-only server
needs its own translation step.
