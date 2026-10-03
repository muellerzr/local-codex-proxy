#!/usr/bin/env python3
"""Route Codex Responses requests to ChatGPT or custom model endpoints."""

import json
import os
import ssl
import sys
import threading
import time
from http.client import HTTPSConnection, HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit


HOST = os.environ.get("CODEX_ROUTER_HOST", "127.0.0.1")
PORT = int(os.environ.get("CODEX_ROUTER_PORT", "4100"))
OPENAI_BASE = os.environ.get("CODEX_ROUTER_OPENAI_BASE", "https://chatgpt.com/backend-api/codex")
GLM_BASE = os.environ.get("CODEX_ROUTER_GLM_BASE", "")
GLM_MODEL = os.environ.get("CODEX_ROUTER_GLM_MODEL", "glm-5.3-flash-nvfp4")
HOST_EXEC_TOOL = "exec"
CATALOG_OUT = os.environ.get("CODEX_ROUTER_CATALOG_OUT", "")
REFRESH_HOURS = float(os.environ.get("CODEX_ROUTER_REFRESH_HOURS", "24"))
AUTH_FILE = os.environ.get("CODEX_ROUTER_AUTH_FILE", "~/.codex/auth.json")
APP_PLIST = os.environ.get(
    "CODEX_ROUTER_APP_PLIST", "/Applications/ChatGPT.app/Contents/Info.plist"
)
CLIENT_VERSION_ENV = os.environ.get("CODEX_ROUTER_CLIENT_VERSION", "")


def upstream_parts(base):
    parsed = urlsplit(base.rstrip("/"))
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"invalid upstream URL: {base}")
    return parsed.scheme, parsed.netloc, parsed.path.rstrip("/")


def load_custom_routes():
    """Read CODEX_ROUTER_ROUTES: a JSON object mapping model slugs to base URLs."""
    raw = os.environ.get("CODEX_ROUTER_ROUTES", "")
    if not raw:
        return {}
    try:
        routes = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"CODEX_ROUTER_ROUTES is not valid JSON: {exc}")
    if not isinstance(routes, dict):
        raise SystemExit(
            "CODEX_ROUTER_ROUTES must be a JSON object mapping model slugs "
            "to base URLs"
        )
    for model, base in routes.items():
        try:
            upstream_parts(base)
        except ValueError as exc:
            raise SystemExit(f"CODEX_ROUTER_ROUTES[{model!r}]: {exc}")
    return routes


CUSTOM_ROUTES = load_custom_routes()
if not CUSTOM_ROUTES and GLM_BASE:
    CUSTOM_ROUTES = {GLM_MODEL: GLM_BASE}


def read_client_version():
    if CLIENT_VERSION_ENV:
        return CLIENT_VERSION_ENV
    try:
        import plistlib
        with open(APP_PLIST, "rb") as f:
            return plistlib.load(f).get("CFBundleShortVersionString")
    except (OSError, ValueError):
        return None


def refresh_catalog_once():
    """Fetch the official catalog, merge custom models, and write the catalog file.

    Official models are added, and every model already in the catalog whose
    slug is not in the official list is preserved as a custom model. The file
    is only rewritten when the model set actually changed.
    """
    if not CATALOG_OUT:
        return
    try:
        with open(os.path.expanduser(AUTH_FILE), encoding="utf-8") as f:
            token = json.load(f)["tokens"]["access_token"]
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        sys.stderr.write(f"[codex-router] catalog refresh: no auth token ({exc})\n")
        return
    version = read_client_version()
    if not version:
        sys.stderr.write("[codex-router] catalog refresh: no client version\n")
        return
    scheme, netloc, prefix = upstream_parts(OPENAI_BASE)
    connection = (HTTPSConnection(netloc, timeout=60, context=ssl.create_default_context())
                  if scheme == "https" else HTTPConnection(netloc, timeout=60))
    try:
        connection.request(
            "GET", f"{prefix}/models?client_version={version}",
            headers={"Authorization": f"Bearer {token}"},
        )
        response = connection.getresponse()
        data = json.loads(response.read())
        official = data["models"] if isinstance(data, dict) else data
    except Exception as exc:
        sys.stderr.write(f"[codex-router] catalog refresh failed: {exc!r}\n")
        return
    finally:
        connection.close()
    try:
        with open(CATALOG_OUT, encoding="utf-8") as f:
            current = json.load(f)
        current_models = current["models"] if isinstance(current, dict) else current
    except (OSError, json.JSONDecodeError):
        current_models = []
    official_slugs = {m.get("slug") for m in official}
    current_slugs = {m.get("slug") for m in current_models}
    custom_models = [m for m in current_models if m.get("slug") not in official_slugs]
    if official_slugs | {m.get("slug") for m in custom_models} == current_slugs:
        sys.stderr.write("[codex-router] catalog refresh: no new models\n")
        return
    merged = {"models": list(official) + custom_models}
    tmp_path = CATALOG_OUT + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(merged, f, indent=2)
        f.write("\n")
    os.replace(tmp_path, CATALOG_OUT)
    new_slugs = sorted(official_slugs - current_slugs)
    sys.stderr.write(f"[codex-router] catalog refreshed: added {', '.join(new_slugs)}\n")


def catalog_refresh_loop():
    while True:
        refresh_catalog_once()
        time.sleep(REFRESH_HOURS * 3600)


def read_json_model(body):
    try:
        value = json.loads(body)
        return value.get("model") if isinstance(value, dict) else None
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None


def flatten_tools(tools):
    flattened = []
    namespace_map = {}

    def visit(tool_list, namespace=()):
        for tool in tool_list:
            if not isinstance(tool, dict):
                flattened.append(tool)
                continue
            if tool.get("type") == "namespace" and isinstance(tool.get("tools"), list):
                name = tool.get("name")
                next_namespace = namespace + ((name,) if isinstance(name, str) and name else ())
                visit(tool["tools"], next_namespace)
                continue
            copied = dict(tool)
            if namespace and isinstance(copied.get("name"), str):
                original_name = copied["name"]
                flat_name = ".".join((*namespace, original_name))
                copied["name"] = flat_name
                namespace_map[flat_name] = {
                    "name": original_name,
                    "namespace": ".".join(namespace),
                }
            flattened.append(copied)

    visit(tools)
    return flattened, namespace_map


def hoist_additional_tools(body):
    """Translate Codex Responses Lite tools to SGLang's function-tool form."""
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return body, 0, {}
    if not isinstance(payload, dict) or not isinstance(payload.get("input"), list):
        return body, 0, {}

    promoted = []
    kept = []
    for item in payload["input"]:
        if isinstance(item, dict) and item.get("type") == "additional_tools":
            tools = item.get("tools", [])
            if isinstance(tools, list):
                promoted.extend(tools)
            continue
        kept.append(item)

    if not promoted:
        return body, 0, {}

    existing = payload.get("tools")
    if existing is None:
        existing = []
    if not isinstance(existing, list):
        return body, 0, {}

    payload["tools"], namespace_map = flatten_tools(existing + promoted)
    payload["input"] = kept
    for item in payload["input"]:
        if not isinstance(item, dict) or item.get("type") not in {"function_call", "custom_tool_call"}:
            continue
        namespace = item.get("namespace")
        name = item.get("name")
        if isinstance(namespace, str) and isinstance(name, str):
            item["name"] = f"{namespace}.{name}"
            item.pop("namespace", None)
    return (
        json.dumps(payload, separators=(",", ":")).encode("utf-8"),
        len(promoted),
        namespace_map,
    )


def drop_incompatible_code_mode_tools(tools, namespace_map):
    """Remove Codex's JS code-mode wrapper from the GLM-facing tool list.

    Keep the host executable tool available so GLM can use the normal Codex
    network and shell path.  Other functions tools are code-mode wrappers whose
    source format this model does not reliably produce.
    """
    kept = []
    removed = []
    for tool in tools:
        if not isinstance(tool, dict):
            kept.append(tool)
            continue
        name = tool.get("name")
        mapped = namespace_map.get(name) if isinstance(name, str) else None
        if (mapped and mapped.get("namespace") == "functions"
                and mapped.get("name") != HOST_EXEC_TOOL):
            removed.append(name)
            namespace_map.pop(name, None)
            continue
        kept.append(tool)
    return kept, removed


def restore_namespace_tools(value, namespace_map):
    if isinstance(value, list):
        for item in value:
            restore_namespace_tools(item, namespace_map)
    elif isinstance(value, dict):
        if value.get("type") in {"function_call", "custom_tool_call"}:
            name = value.get("name")
            original = namespace_map.get(name)
            if original:
                value["name"] = original["name"]
                value["namespace"] = original["namespace"]
        for item in value.values():
            restore_namespace_tools(item, namespace_map)


def rewrite_sse_line(line, namespace_map):
    ending = b"\r" if line.endswith(b"\r") else b""
    core = line[:-1] if ending else line
    if not core.startswith(b"data: ") or core[6:] == b"[DONE]":
        return line
    try:
        event = json.loads(core[6:])
    except (UnicodeDecodeError, json.JSONDecodeError):
        return line
    restore_namespace_tools(event, namespace_map)
    return b"data: " + json.dumps(event, separators=(",", ":")).encode("utf-8") + ending


def read_stream_chunk(response):
    """Read currently available upstream bytes without waiting for EOF."""
    read1 = getattr(response, "read1", None)
    return read1(64 * 1024) if read1 is not None else response.read(64 * 1024)


class RouterHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        sys.stderr.write("[codex-router] " + (fmt % args) + "\n")

    def do_GET(self):
        if self.path.split("?", 1)[0] in {"/health", "/healthz", "/v1/health", "/v1/healthz"}:
            self.send_health()
            return
        self.forward(self.path, b"", None)

    def send_health(self):
        body = json.dumps({
            "status": "ok",
            "service": "codex-model-router",
            "pid": os.getpid(),
        }, separators=(",", ":")).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)
        self.wfile.flush()

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        self.forward(self.path, body, read_json_model(body))

    def forward(self, path, body, model):
        use_custom = model in CUSTOM_ROUTES if model else False
        namespace_map = {}
        request_stream = False
        if use_custom:
            try:
                request_stream = bool(json.loads(body).get("stream"))
            except (UnicodeDecodeError, json.JSONDecodeError, AttributeError):
                pass
            body, promoted_tools, namespace_map = hoist_additional_tools(body)
            if promoted_tools:
                try:
                    payload = json.loads(body)
                    tools = payload.get("tools", [])
                    tools, removed_tools = drop_incompatible_code_mode_tools(
                        tools, namespace_map
                    )
                    payload["tools"] = tools
                    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
                    tool_types = [
                        item.get("type") for item in tools if isinstance(item, dict)
                    ]
                    tool_names = [
                        item.get("name") for item in tools
                        if isinstance(item, dict) and item.get("name")
                    ]
                except (UnicodeDecodeError, json.JSONDecodeError, AttributeError):
                    tool_types = []
                    tool_names = []
                    removed_tools = []
                self.log_message(
                    "hoisted %d additional_tools into top-level tools types=%s names=%s removed=%s",
                    promoted_tools,
                    tool_types,
                    tool_names,
                    removed_tools,
                )
        base = CUSTOM_ROUTES[model] if use_custom else OPENAI_BASE
        scheme, netloc, prefix = upstream_parts(base)
        api_path = path if path.startswith("/") else "/" + path
        if api_path == "/v1":
            api_path = "/"
        elif api_path.startswith("/v1/"):
            api_path = api_path[3:]
        target = prefix + api_path

        headers = {}
        for key, value in self.headers.items():
            lower = key.lower()
            if lower in {"host", "content-length", "connection"}:
                continue
            if use_custom and lower in {"authorization", "cookie"}:
                continue
            headers[key] = value
        if body:
            headers["Content-Length"] = str(len(body))

        connection = (HTTPSConnection(netloc, timeout=300, context=ssl.create_default_context())
                      if scheme == "https" else HTTPConnection(netloc, timeout=300))
        try:
            route = "custom" if use_custom else "ChatGPT"
            self.log_message("%s %s model=%s -> %s%s", self.command, path, model, route, target)
            connection.request(self.command, target, body=body or None, headers=headers)
            response = connection.getresponse()
            response_headers = {}
            for key, value in response.getheaders():
                if key.lower() in {"connection", "keep-alive", "transfer-encoding", "content-length"}:
                    continue
                response_headers[key] = value
            payload_length = response.getheader("Content-Length")
            response_body = None
            if namespace_map and not request_stream:
                response_body = response.read()
                try:
                    decoded = json.loads(response_body)
                    restore_namespace_tools(decoded, namespace_map)
                    response_body = json.dumps(decoded, separators=(",", ":")).encode("utf-8")
                except (UnicodeDecodeError, json.JSONDecodeError):
                    pass
                response_headers["Content-Length"] = str(len(response_body))
            elif payload_length is not None and not (namespace_map and request_stream):
                response_headers["Content-Length"] = payload_length
            else:
                response_headers["Connection"] = "close"
                self.close_connection = True
            self.send_response(response.status, response.reason)
            for key, value in response_headers.items():
                self.send_header(key, value)
            if request_stream:
                self.send_header("Connection", "close")
                self.close_connection = True
            self.end_headers()
            if request_stream and namespace_map:
                buffer = b""
                while True:
                    chunk = read_stream_chunk(response)
                    if not chunk:
                        break
                    buffer += chunk
                    while b"\n" in buffer:
                        line, buffer = buffer.split(b"\n", 1)
                        self.wfile.write(rewrite_sse_line(line, namespace_map) + b"\n")
                        self.wfile.flush()
                if buffer:
                    self.wfile.write(rewrite_sse_line(buffer, namespace_map))
                    self.wfile.flush()
            elif request_stream:
                while True:
                    chunk = read_stream_chunk(response)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    self.wfile.flush()
            else:
                if response_body is None:
                    response_body = response.read()
                self.wfile.write(response_body)
                self.wfile.flush()
        except Exception as exc:
            self.log_message("upstream failure: %r", exc)
            if not self.wfile.closed:
                try:
                    self.send_error(502, "upstream failure")
                except OSError:
                    pass
        finally:
            connection.close()


def main():
    if not CUSTOM_ROUTES and not CATALOG_OUT:
        sys.stderr.write(
            "No custom models configured. Set CODEX_ROUTER_ROUTES to a JSON "
            "object mapping model slugs to base URLs, e.g. "
            "CODEX_ROUTER_ROUTES='{\"my-model\":\"http://your-host:30000/v1\"}', "
            "or set CODEX_ROUTER_GLM_BASE=http://your-host:30000/v1 for a "
            "single model.\n"
        )
        sys.exit(2)
    server = ThreadingHTTPServer((HOST, PORT), RouterHandler)
    routes_desc = ", ".join(f"{m} -> {b}" for m, b in sorted(CUSTOM_ROUTES.items()))
    print(
        f"Codex model router listening on http://{HOST}:{PORT}/v1 "
        f"(other models -> {OPENAI_BASE}; {routes_desc})",
        flush=True,
    )
    if CATALOG_OUT:
        threading.Thread(target=catalog_refresh_loop, daemon=True).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
