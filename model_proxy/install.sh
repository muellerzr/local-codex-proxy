#!/usr/bin/env bash
# Install the Codex model router as a launchd service (macOS).
# Usage: ./install.sh http://your-model-host:30000/v1 [model-slug]

set -euo pipefail

GLM_BASE="${1:?Usage: ./install.sh http://your-model-host:30000/v1 [model-slug]}"
GLM_MODEL="${2:-glm-5.3-flash-nvfp4}"
CATALOG_OUT="${3:-}"

# Auto-refresh the model catalog by default: if the machine already has a
# merged catalog, point the router at it so new model releases show up
# automatically. Pass an empty third argument to disable.
if [ -z "${CATALOG_OUT}" ] && [ -f "${HOME}/.codex/model-catalogs/models-with-glm.json" ]; then
  CATALOG_OUT="${HOME}/.codex/model-catalogs/models-with-glm.json"
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROUTER_PATH="${SCRIPT_DIR}/codex_model_router.py"
PYTHON_PATH="$(command -v python3)"
PLIST_OUT="${HOME}/Library/LaunchAgents/com.openai.codex-model-router.plist"

sed -e "s|__PYTHON_PATH__|${PYTHON_PATH}|g" \
    -e "s|__ROUTER_PATH__|${ROUTER_PATH}|g" \
    -e "s|__GLM_BASE__|${GLM_BASE}|g" \
    -e "s|__GLM_MODEL__|${GLM_MODEL}|g" \
    -e "s|__CATALOG_OUT__|${CATALOG_OUT}|g" \
    "${SCRIPT_DIR}/plist.template" > "${PLIST_OUT}"

launchctl bootout "gui/$(id -u)" "${PLIST_OUT}" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "${PLIST_OUT}"
launchctl kickstart -k "gui/$(id -u)/com.openai.codex-model-router"

sleep 1
curl -fsS "http://127.0.0.1:4100/health" && echo
