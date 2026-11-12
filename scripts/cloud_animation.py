"""Optional, receipt-bound cloud motion provider adapters for H3 candidates.

This module submits only explicitly planned provider requests. It never writes
to game assets, never promotes artwork, and never interprets a successful cloud
job as an accepted H3 action group. Credentials are read from environment
variables and are never copied into operation plans or receipts.
"""
from __future__ import annotations
from tool_paths import resolve_tool_root

import argparse
import base64
import hashlib
import ipaddress
import json
import math
import mimetypes
import os
from pathlib import Path
import re
import socket
import sys
import time
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, quote, urlencode, urlsplit
from urllib.request import (HTTPRedirectHandler, Request, build_opener,
                            install_opener)


TOOL_ROOT = resolve_tool_root(__file__)
DEVELOPMENT_ROOT = TOOL_ROOT / "build" / "experiments" / "cloud-animation"
PLAN_SCHEMA = "h3.cloud-animation.plan.v1"
DISCOVERY_SCHEMA = "h3.cloud-animation.scenario-discovery.v1"
STATE_SCHEMA = "h3.cloud-animation.state.v1"
RECEIPT_SCHEMA = "h3.cloud-animation.fetch-receipt.v1"
ERROR_SCHEMA = "h3.cloud-animation.error.v1"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
OPERATION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$", re.ASCII)
ASSET_ID_RE = re.compile(r"^[A-Za-z0-9_-]{4,160}$", re.ASCII)
MAX_CONTROL_BYTES = 8 * 1024 * 1024
MAX_DOWNLOAD_BYTES = 256 * 1024 * 1024
MAX_SCENARIO_ASSET_BYTES_EXCLUSIVE = 4_000_000
MAX_SCENARIO_OUTPUT_ASSETS = 64
DEFAULT_TIMEOUT_SECONDS = 60

MESHY_BASE = "https://api.meshy.ai/openapi/v1"
SCENARIO_BASE = "https://api.cloud.scenario.com/v1"

MESHY_STAGES: dict[str, dict[str, Any]] = {
    "image-to-3d": {
        "path": "image-to-3d",
        "method": "POST",
        "status_path": "image-to-3d/{id}",
        "allowed": {
            "image_url", "input_task_id", "model_type", "ai_model",
            "geometry_resolution", "should_texture", "enable_pbr",
            "texture_resolution", "texture_prompt", "texture_image_url",
            "should_remesh", "topology", "target_polycount", "target_formats",
            "pose_mode", "moderation", "origin_at", "auto_size",
        },
        "sources": {"image_url": "image", "texture_image_url": "image"},
        "max_source_bytes": 20 * 1024 * 1024,
    },
    "rig": {
        "path": "rigging",
        "method": "POST",
        "status_path": "rigging/{id}",
        "allowed": {"input_task_id", "model_url", "height_meters", "texture_image_url"},
        "sources": {"model_url": "glb", "texture_image_url": "image"},
        "max_source_bytes": 100 * 1024 * 1024,
    },
    "animation": {
        "path": "animations",
        "method": "POST",
        "status_path": "animations/{id}",
        "allowed": {"rig_task_id", "action_id", "action_ids", "motion_task_id", "post_process"},
        "sources": {},
        "max_source_bytes": 0,
    },
    "text-to-motion": {
        "path": "text-to-motion",
        "method": "POST",
        "status_path": "text-to-motion/{id}",
        "allowed": {"prompt", "mode", "duration"},
        "sources": {},
        "max_source_bytes": 0,
    },
}

PROVIDER_CATALOG: tuple[dict[str, Any], ...] = (
    {
        "provider": "meshy",
        "status": "implemented",
        "stages": ["image-to-3d", "rig", "animation", "text-to-motion"],
        "api": "REST; bearer key from MESHY_API_KEY",
        "outputs": "GLB/FBX model or motion candidates; local rendering is still required for H3 PNG frames",
        "limits": ["rig API supports standard textured humanoid bipeds", "animation action_ids accepts 1..10 actions", "text-to-motion requires biped retargeting"],
        "docs": [
            "https://docs.meshy.ai/en/api/image-to-3d",
            "https://docs.meshy.ai/en/api/rigging",
            "https://docs.meshy.ai/en/api/animation",
            "https://docs.meshy.ai/en/api/text-to-motion",
        ],
    },
    {
        "provider": "scenario",
        "status": "workflow-adapter",
        "stages": ["discover", "workflow-run"],
        "api": "REST; Basic API key/secret from SCENARIO_API_KEY and SCENARIO_API_SECRET",
        "outputs": "Workflow assets; cloud job success does not establish H3 timing, alpha, frame, or art acceptance",
        "limits": ["workflow ID and input schema are discovered live; no Action Sprite Studio ID is hardcoded",
                   "POST /assets base64 upload is limited to source files under 4,000,000 bytes",
                   "multipart /uploads is not implemented; sources at or above 4,000,000 bytes are refused",
                   "local image sources require explicit upload consent"],
        "docs": [
            "https://www.scenario.com/apps/action-sprite-studio",
            "https://docs.scenario.com/get-started/documentation/workflows-and-apps",
            "https://docs.scenario.com/api/resources/workflows/methods/run",
            "https://docs.scenario.com/api/resources/assets/methods/upload",
        ],
    },
    {
        "provider": "anything-world",
        "status": "not-adapted",
        "stages": ["3d-rig-animation"],
        "api": "documented REST and Python client; experimental service",
        "outputs": "Animated GLB/FBX; category-specific actions do not cover all native H3 groups",
        "limits": ["not connected", "separate integration and species pilot required"],
        "docs": ["https://anything-world.gitbook.io/anything-world/api/rest-api-references",
                 "https://github.com/anythingworld/anything-world-python"],
    },
    {
        "provider": "sprite-lab.com",
        "status": "not-adapted",
        "stages": ["2d-sprite-animation"],
        "api": "the listed product documents transparent sheets/frames, but no official generation API was found",
        "outputs": "UI-exported PNG frame assets",
        "limits": ["no documented API adapter"],
        "docs": ["https://www.sprite-lab.com/ai-sprite-animation-generator"],
    },
    {
        "provider": "spritelab.dev",
        "status": "not-adapted",
        "stages": ["pixel-art-animation"],
        "api": "separate domain with REST/MCP animate capability",
        "outputs": "pixel-art sprite sheets/GIF; 16-frame cap, 256/320-pixel input side depending on mode",
        "limits": ["different product/domain from sprite-lab.com", "pixel art and size limits do not fit the primary HD art target"],
        "docs": ["https://spritelab.dev/docs/mcp"],
    },
    {
        "provider": "mixamo",
        "status": "not-adapted",
        "stages": ["web-rig-animation"],
        "api": "no public official API was found; current official workflow is web UI",
        "outputs": "manual FBX/animation export",
        "limits": ["Adobe documents biped-only auto-rigging", "no supported unattended batch interface"],
        "docs": ["https://helpx.adobe.com/creative-cloud/faq/mixamo-faq.html"],
    },
)


class CloudAnimationError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


class HttpResponse:
    def __init__(self, status: int, headers: dict[str, str], body: bytes):
        self.status = status
        self.headers = headers
        self.body = body


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def _json_response(response: HttpResponse, *, allow_cost_estimate: bool = False) -> Any:
    if not (200 <= response.status < 300 or (allow_cost_estimate and response.status == 269)):
        if 400 <= response.status < 500:
            raise CloudAnimationError("PROVIDER_REQUEST_REJECTED", f"provider returned HTTP {response.status}")
        raise CloudAnimationError("PROVIDER_RESPONSE_UNKNOWN", f"provider returned HTTP {response.status}; submission may have been accepted")
    if len(response.body) > MAX_CONTROL_BYTES:
        raise CloudAnimationError("PROVIDER_RESPONSE_TOO_LARGE", "provider control response exceeded the configured limit")
    try:
        return json.loads(response.body.decode("utf-8")) if response.body else {}
    except (UnicodeError, json.JSONDecodeError) as error:
        raise CloudAnimationError("PROVIDER_RESPONSE_INVALID", "provider response was not valid UTF-8 JSON") from error


class _SameHostHTTPSRedirect(HTTPRedirectHandler):
    def __init__(self, expected_host: str | None = None):
        super().__init__()
        self.expected_host = expected_host

    def redirect_request(self, request, fp, code, msg, headers, new_url):
        original = urlsplit(request.full_url)
        target = urlsplit(new_url)
        if target.scheme != "https" or not target.hostname:
            raise URLError("refusing non-HTTPS redirect")
        if self.expected_host and target.hostname.lower() != self.expected_host.lower():
            raise URLError("refusing cross-host API redirect")
        return super().redirect_request(request, fp, code, msg, headers, new_url)


def default_transport(method: str, url: str, headers: dict[str, str], body: bytes | None,
                      timeout: int = DEFAULT_TIMEOUT_SECONDS) -> HttpResponse:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise CloudAnimationError("HTTPS_REQUIRED", "provider requests must use HTTPS")
    request = Request(url, data=body, headers=headers, method=method)
    opener = build_opener(_SameHostHTTPSRedirect(parsed.hostname))
    try:
        with opener.open(request, timeout=timeout) as response:
            payload = response.read(MAX_CONTROL_BYTES + 1 if "json" in response.headers.get("Content-Type", "") else MAX_DOWNLOAD_BYTES + 1)
            return HttpResponse(response.status, dict(response.headers.items()), payload)
    except HTTPError as error:
        # Scenario's documented dryRun cost estimate is an HTTP 269 response.
        try:
            payload = error.read(MAX_CONTROL_BYTES + 1)
        finally:
            error.close()
        return HttpResponse(error.code, dict(error.headers.items()) if error.headers else {}, payload)
    except (URLError, TimeoutError, OSError) as error:
        # Do not include the URL, request headers, or body; signed URLs and credentials may be present.
        raise CloudAnimationError("NETWORK_OUTCOME_UNKNOWN", "provider request transport failed; inspect operation state before any new operation") from error


def _request(transport: Callable, method: str, url: str, *, headers: dict[str, str] | None = None,
             payload: Any = None, raw_body: bytes | None = None,
             allow_cost_estimate: bool = False, with_status: bool = False) -> Any:
    request_headers = dict(headers or {})
    body = raw_body
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
        request_headers.setdefault("Content-Type", "application/json")
    request_headers.setdefault("Accept", "application/json")
    response = transport(method, url, request_headers, body, DEFAULT_TIMEOUT_SECONDS)
    value = _json_response(response, allow_cost_estimate=allow_cost_estimate)
    return (value, response.status) if with_status else value


def _mesh_headers(environ: dict[str, str]) -> dict[str, str]:
    key = environ.get("MESHY_API_KEY", "")
    if not key:
        raise CloudAnimationError("MESHY_CREDENTIALS_REQUIRED", "set MESHY_API_KEY in the process environment")
    return {"Authorization": f"Bearer {key}"}


def _scenario_headers(environ: dict[str, str]) -> dict[str, str]:
    key = environ.get("SCENARIO_API_KEY", "")
    secret = environ.get("SCENARIO_API_SECRET", "")
    if not key or not secret:
        raise CloudAnimationError("SCENARIO_CREDENTIALS_REQUIRED", "set SCENARIO_API_KEY and SCENARIO_API_SECRET in the process environment")
    token = base64.b64encode(f"{key}:{secret}".encode("utf-8")).decode("ascii")
    return {"Authorization": f"Basic {token}"}


def _safe_provider_url(value: Any, *, label: str, data_uri: bool = False) -> str:
    if not isinstance(value, str) or not value:
        raise CloudAnimationError("URL_INVALID", f"{label} must be a non-empty URL")
    if value.startswith("data:"):
        raise CloudAnimationError("DATA_URI_IN_PLAN_FORBIDDEN", f"{label} must use a hashed --source binding instead of embedding bytes in the plan")
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise CloudAnimationError("URL_INVALID", f"{label} must be an HTTPS URL without embedded credentials")
    host = parsed.hostname.casefold()
    if host in {"localhost", "localhost.localdomain"} or host.endswith((".local", ".localhost", ".internal")):
        raise CloudAnimationError("URL_PRIVATE_HOST_FORBIDDEN", f"{label} cannot point to a local host")
    try:
        address = ipaddress.ip_address(host)
        if not address.is_global:
            raise CloudAnimationError("URL_PRIVATE_HOST_FORBIDDEN", f"{label} cannot point to a private or reserved address")
    except ValueError:
        pass
    query_names = set()
    for raw_name, _ in parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=False):
        normalized = re.sub(r"[^a-z0-9]", "", raw_name.casefold())
        query_names.add(normalized)
    credential_names = {"accesstoken", "token", "apikey", "authorization", "credential",
                        "signature", "sig", "securitytoken", "xgoogcredential",
                        "xgoogsignature", "xgoogsecuritytoken", "xamzcredential",
                        "xamzsignature", "xamzsecuritytoken"}
    if query_names & credential_names:
        raise CloudAnimationError("URL_CREDENTIALS_FORBIDDEN", f"{label} contains signed or credential query parameters; bind a local source instead")
    return value


def _sensitive_payload(value: Any, trail: str = "payload") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if any(term in str(key).casefold() for term in ("api_key", "apikey", "secret", "password", "authorization", "cookie", "credential", "access_token")):
                raise CloudAnimationError("PAYLOAD_SECRET_FORBIDDEN", f"credential-like field is forbidden at {trail}.{key}")
            _sensitive_payload(child, f"{trail}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _sensitive_payload(child, f"{trail}[{index}]")
    elif isinstance(value, str) and value.startswith("https://"):
        _safe_provider_url(value, label=trail)


def _source_marker(value: Any) -> int | None:
    if isinstance(value, dict) and set(value) == {"source_index"}:
        index = value["source_index"]
        if type(index) is int and index >= 0:
            return index
        raise CloudAnimationError("SOURCE_MARKER_INVALID", "source_index must be a nonnegative integer")
    return None


def _walk_source_markers(value: Any, trail: str = "payload") -> list[tuple[str, int]]:
    found: list[tuple[str, int]] = []
    marker = _source_marker(value)
    if marker is not None:
        found.append((trail, marker))
    elif isinstance(value, dict):
        for key, child in value.items():
            found.extend(_walk_source_markers(child, f"{trail}.{key}"))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.extend(_walk_source_markers(child, f"{trail}[{index}]"))
    return found


def _source_info(raw_path: str) -> dict[str, Any]:
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        raise CloudAnimationError("SOURCE_PATH_ABSOLUTE_REQUIRED", "source paths must be absolute")
    try:
        resolved = path.resolve(strict=True)
        if not resolved.is_file():
            raise CloudAnimationError("SOURCE_FILE_REQUIRED", f"source is not a file: {resolved}")
        stat = resolved.stat()
        digest = sha256_file(resolved)
    except OSError as error:
        raise CloudAnimationError("SOURCE_UNAVAILABLE", f"source cannot be read: {path}") from error
    return {"path": str(resolved), "sha256": digest, "bytes": stat.st_size}


def _mime_and_size(path: Path, kind: str, max_bytes: int) -> tuple[str, bytes]:
    extension = path.suffix.lower()
    if kind == "image":
        mime = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}.get(extension)
    elif kind == "glb":
        mime = "model/gltf-binary" if extension == ".glb" else None
    else:
        mime = None
    if not mime:
        raise CloudAnimationError("SOURCE_TYPE_UNSUPPORTED", f"{path.name} has an unsupported extension for {kind}")
    try:
        data = path.read_bytes()
    except OSError as error:
        raise CloudAnimationError("SOURCE_UNREADABLE", f"source cannot be read: {path}") from error
    if len(data) > max_bytes:
        raise CloudAnimationError("SOURCE_TOO_LARGE", f"source exceeds the provider limit for {kind}")
    return mime, data


def _read_bound_source(receipt: dict[str, Any], kind: str, max_bytes: int) -> tuple[str, bytes, dict[str, Any]]:
    """Read once, verify the exact bytes pinned in the plan, and return those bytes for sending."""
    path = Path(receipt["path"])
    mime, data = _mime_and_size(path, kind, max_bytes)
    actual_sha = sha256_bytes(data)
    if actual_sha != receipt.get("sha256") or len(data) != receipt.get("bytes"):
        raise CloudAnimationError("SOURCE_HASH_MISMATCH", f"source changed after planning: {path.name}")
    return mime, data, {"source_index": None, "sha256": actual_sha, "bytes": len(data)}


def _validate_meshy_payload(stage: str, payload: dict[str, Any]) -> None:
    config = MESHY_STAGES.get(stage)
    if not config:
        raise CloudAnimationError("MESHY_STAGE_UNSUPPORTED", f"unsupported Meshy stage: {stage}")
    unknown = sorted(set(payload) - config["allowed"])
    if unknown:
        raise CloudAnimationError("PAYLOAD_FIELD_UNSUPPORTED", f"unsupported {stage} fields: {', '.join(unknown)}")
    for name in payload:
        _sensitive_payload({name: payload[name]})
    if stage == "image-to-3d":
        if ("image_url" in payload) == ("input_task_id" in payload):
            raise CloudAnimationError("PAYLOAD_REQUIRED_FIELD", "image-to-3d needs exactly one of image_url or input_task_id")
    elif stage == "rig":
        if ("model_url" in payload) == ("input_task_id" in payload):
            raise CloudAnimationError("PAYLOAD_REQUIRED_FIELD", "rig needs exactly one of model_url or input_task_id")
        if "model_url" in payload and not _source_marker(payload["model_url"]):
            _safe_provider_url(payload["model_url"], label="model_url", data_uri=True)
    elif stage == "animation":
        actions = sum(name in payload for name in ("action_id", "action_ids", "motion_task_id"))
        if not isinstance(payload.get("rig_task_id"), str) or not payload["rig_task_id"] or actions != 1:
            raise CloudAnimationError("PAYLOAD_REQUIRED_FIELD", "animation needs rig_task_id and exactly one action_id/action_ids/motion_task_id")
        if "action_id" in payload and (type(payload["action_id"]) is not int or payload["action_id"] < 0):
            raise CloudAnimationError("PAYLOAD_INVALID", "action_id must be a nonnegative integer")
        if "action_ids" in payload:
            ids = payload["action_ids"]
            if not isinstance(ids, list) or not 1 <= len(ids) <= 10 or any(type(item) is not int or item < 0 for item in ids) or len(set(ids)) != len(ids):
                raise CloudAnimationError("PAYLOAD_INVALID", "action_ids must contain 1..10 unique nonnegative integers")
        if "motion_task_id" in payload and (not isinstance(payload["motion_task_id"], str) or not payload["motion_task_id"]):
            raise CloudAnimationError("PAYLOAD_INVALID", "motion_task_id must be a non-empty string")
    elif stage == "text-to-motion":
        prompt, duration = payload.get("prompt"), payload.get("duration")
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 400:
            raise CloudAnimationError("PAYLOAD_INVALID", "text-to-motion prompt must contain 1..400 characters")
        if type(duration) not in (int, float) or not math.isfinite(float(duration)) or not 2 <= float(duration) <= 10 or float(duration) * 2 != round(float(duration) * 2):
            raise CloudAnimationError("PAYLOAD_INVALID", "text-to-motion duration must be 2..10 seconds in 0.5-second steps")
        if payload.get("mode", "prime") not in {"prime", "swift"}:
            raise CloudAnimationError("PAYLOAD_INVALID", "text-to-motion mode must be prime or swift")


def _workflow_schema_hash(workflow: dict[str, Any]) -> str:
    normalized = {
        "id": workflow.get("id"),
        "name": workflow.get("name"),
        "status": workflow.get("status"),
        "privacy": workflow.get("privacy"),
        "inputs": workflow.get("inputs", []),
    }
    return sha256_bytes(canonical_json(normalized))


def _validate_scenario_payload(workflow: dict[str, Any], payload: dict[str, Any]) -> None:
    if not isinstance(payload, dict):
        raise CloudAnimationError("PAYLOAD_INVALID", "Scenario workflow input payload must be a JSON object")
    _sensitive_payload(payload)
    inputs = workflow.get("inputs")
    if not isinstance(inputs, list):
        raise CloudAnimationError("WORKFLOW_INPUT_SCHEMA_MISSING", "workflow detail has no input schema")
    schemas = {item.get("name"): item for item in inputs if isinstance(item, dict) and isinstance(item.get("name"), str)}
    unknown = sorted(set(payload) - set(schemas))
    if unknown:
        raise CloudAnimationError("PAYLOAD_FIELD_UNSUPPORTED", f"unknown Scenario workflow inputs: {', '.join(unknown)}")
    for name, schema in schemas.items():
        required = schema.get("required")
        if isinstance(required, dict) and required.get("always") is True and name not in payload:
            raise CloudAnimationError("PAYLOAD_REQUIRED_FIELD", f"workflow input is required: {name}")
    for name, value in payload.items():
        schema = schemas[name]
        input_type = schema.get("type")
        if input_type in {"file", "file_array"}:
            kind = schema.get("kind", "image")
            markers = []
            if input_type == "file":
                markers = [_source_marker(value)]
                valid = isinstance(value, str) and bool(value) or markers[0] is not None
            else:
                valid = isinstance(value, list) and all(isinstance(item, str) and item or _source_marker(item) is not None for item in value)
                if isinstance(value, list):
                    markers = [_source_marker(item) for item in value]
            if not valid:
                raise CloudAnimationError("PAYLOAD_INVALID", f"Scenario {input_type} input {name} must be asset ID(s) or source marker(s)")
            candidates = [value] if input_type == "file" else (value if isinstance(value, list) else [])
            for candidate in candidates:
                if isinstance(candidate, str) and not ASSET_ID_RE.fullmatch(candidate):
                    raise CloudAnimationError("SCENARIO_ASSET_ID_INVALID", f"Scenario input {name} contains an invalid asset ID")
            if any(marker is not None for marker in markers) and kind != "image":
                raise CloudAnimationError("SOURCE_TYPE_UNSUPPORTED", f"local source upload currently supports Scenario image inputs; {name} is {kind}")
        elif input_type in {"string", "model"} and not isinstance(value, str):
            raise CloudAnimationError("PAYLOAD_INVALID", f"Scenario input {name} must be a string")
        elif input_type == "boolean" and type(value) is not bool:
            raise CloudAnimationError("PAYLOAD_INVALID", f"Scenario input {name} must be boolean")
        elif input_type == "number" and (type(value) not in (int, float) or not math.isfinite(float(value))):
            raise CloudAnimationError("PAYLOAD_INVALID", f"Scenario input {name} must be a finite number")
        elif input_type in {"string_array", "model_array"} and (not isinstance(value, list) or any(not isinstance(item, str) for item in value)):
            raise CloudAnimationError("PAYLOAD_INVALID", f"Scenario input {name} must be a string array")
        elif input_type == "number_array" and (not isinstance(value, list) or any(type(item) not in (int, float) or not math.isfinite(float(item)) for item in value)):
            raise CloudAnimationError("PAYLOAD_INVALID", f"Scenario input {name} must be a finite number array")
        elif input_type == "inputs_array" and not isinstance(value, list):
            raise CloudAnimationError("PAYLOAD_INVALID", f"Scenario input {name} must be an array")


def _normalize_action_plan(path_arg: str | None) -> dict[str, Any] | None:
    if path_arg is None:
        return None
    info = _source_info(path_arg)
    path = Path(info["path"])
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise CloudAnimationError("ACTION_PLAN_INVALID", "native action plan is not valid UTF-8 JSON") from error
    if not isinstance(document, dict) or document.get("schema") != "h3.creature.action-plan.v1":
        raise CloudAnimationError("ACTION_PLAN_SCHEMA_UNSUPPORTED", "action plan schema must be h3.creature.action-plan.v1")
    if document.get("def_or_game_written") is True:
        raise CloudAnimationError("ACTION_PLAN_INVALID", "action-plan reference cannot claim game writes")
    groups = [group for unit in document.get("units", []) if isinstance(unit, dict)
              for group in unit.get("action_groups", []) if isinstance(group, dict)]
    slots = sum(group.get("frame_count", 0) for group in groups if type(group.get("frame_count")) is int)
    return {**info, "schema": document["schema"], "unit_count": document.get("unit_count"),
            "native_action_group_count": document.get("native_action_group_count"), "slot_count": slots}


def _operation_dir(operation_id: str, development_root: Path | None = None) -> Path:
    if not isinstance(operation_id, str) or not OPERATION_ID_RE.fullmatch(operation_id) or operation_id in {".", ".."}:
        raise CloudAnimationError("OPERATION_ID_INVALID", "operation ID must be 1..64 safe ASCII letters, digits, '_' or '-'")
    root = (development_root or DEVELOPMENT_ROOT).resolve(strict=False)
    return root / "operations" / operation_id


def _assert_under(path: Path, root: Path, code: str) -> Path:
    resolved = path.resolve(strict=False)
    try:
        resolved.relative_to(root.resolve(strict=False))
    except ValueError as error:
        raise CloudAnimationError(code, f"path must stay under {root}") from error
    return resolved


def _write_exclusive(path: Path, payload: bytes, *, parent_root: Path) -> None:
    path = _assert_under(path, parent_root, "OUTPUT_OUTSIDE_DEVELOPMENT_ROOT")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError as error:
        raise CloudAnimationError("OUTPUT_ALREADY_EXISTS", f"refusing to overwrite existing output: {path.name}") from error
    except OSError as error:
        raise CloudAnimationError("OUTPUT_WRITE_FAILED", "could not persist new operation data") from error


def _write_state(state_path: Path, state: dict[str, Any], *, root: Path) -> None:
    data = (json.dumps(state, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")
    temporary = state_path.with_name("state.pending")
    if temporary.exists():
        raise CloudAnimationError("STATE_WRITE_AMBIGUOUS", "state transition has a leftover pending file; inspect operation before continuing")
    _write_exclusive(temporary, data, parent_root=root)
    try:
        os.replace(temporary, state_path)
    except OSError as error:
        raise CloudAnimationError("STATE_WRITE_FAILED", "could not atomically update operation state") from error


def _state_path(operation_dir: Path) -> Path:
    return operation_dir / "state.json"


def _read_json(path: Path, code: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise CloudAnimationError(code, f"could not read {path.name} as UTF-8 JSON") from error


def _schema_workflow_from_discovery(discovery_path: Path,
                                    development_root: Path | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        resolved = discovery_path.expanduser().resolve(strict=True)
    except OSError as error:
        raise CloudAnimationError("WORKFLOW_DISCOVERY_UNAVAILABLE", "workflow discovery file is unavailable") from error
    root = (development_root or DEVELOPMENT_ROOT).resolve(strict=False)
    _assert_under(resolved, root, "WORKFLOW_DISCOVERY_OUTSIDE_DEVELOPMENT_ROOT")
    document = _read_json(resolved, "WORKFLOW_DISCOVERY_INVALID")
    if not isinstance(document, dict) or document.get("schema") != DISCOVERY_SCHEMA or not isinstance(document.get("workflow"), dict):
        raise CloudAnimationError("WORKFLOW_DISCOVERY_INVALID", "expected an H3 Scenario workflow discovery receipt")
    workflow = document["workflow"]
    expected = document.get("inputs_sha256")
    if expected != _workflow_schema_hash(workflow):
        raise CloudAnimationError("WORKFLOW_DISCOVERY_DRIFT", "workflow input schema differs from its discovery receipt")
    return workflow, {"path": str(resolved), "sha256": sha256_file(resolved), "bytes": resolved.stat().st_size,
                      "inputs_sha256": expected}


def build_operation_plan(*, provider: str, stage: str, payload: dict[str, Any],
                         source_paths: list[str], operation_id: str,
                         action_plan_path: str | None = None,
                         workflow_discovery_path: str | None = None,
                         project_id: str | None = None,
                         development_root: Path | None = None) -> dict[str, Any]:
    operation_dir = _operation_dir(operation_id, development_root)
    root = (development_root or DEVELOPMENT_ROOT).resolve(strict=False)
    _assert_under(operation_dir, root, "OUTPUT_OUTSIDE_DEVELOPMENT_ROOT")
    if operation_dir.exists():
        raise CloudAnimationError("OPERATION_ALREADY_EXISTS", "operation ID already has a directory; choose a new ID")
    if not isinstance(payload, dict):
        raise CloudAnimationError("PAYLOAD_INVALID", "provider payload must be a JSON object")
    if len(canonical_json(payload)) > 1024 * 1024:
        raise CloudAnimationError("PAYLOAD_TOO_LARGE", "provider payload exceeds the 1 MiB frozen-plan limit")
    _sensitive_payload(payload)
    sources = [_source_info(raw_path) for raw_path in source_paths]
    markers = _walk_source_markers(payload)
    used = sorted({index for _, index in markers})
    if any(index >= len(sources) for index in used):
        raise CloudAnimationError("SOURCE_INDEX_OUT_OF_RANGE", "payload refers to a source index not provided with --source")
    if used != list(range(len(sources))):
        raise CloudAnimationError("SOURCE_UNUSED", "every --source must be referenced by a payload source_index marker")
    if provider == "meshy":
        _validate_meshy_payload(stage, payload)
        config = MESHY_STAGES[stage]
        for key, expected_kind in config["sources"].items():
            if key in payload:
                marker = _source_marker(payload[key])
                if marker is not None:
                    max_bytes = 20 * 1024 * 1024 if expected_kind == "image" else config["max_source_bytes"]
                    _read_bound_source(sources[marker], expected_kind, max_bytes)
                elif isinstance(payload[key], str) and not payload[key].startswith("data:"):
                    _safe_provider_url(payload[key], label=key)
        if stage == "image-to-3d" and _source_marker(payload.get("image_url")) is None and "image_url" in payload:
            _safe_provider_url(payload["image_url"], label="image_url", data_uri=True)
        if stage == "rig" and _source_marker(payload.get("model_url")) is None and "model_url" in payload:
            _safe_provider_url(payload["model_url"], label="model_url", data_uri=True)
    elif provider == "scenario":
        if stage != "workflow-run" or workflow_discovery_path is None:
            raise CloudAnimationError("SCENARIO_WORKFLOW_REQUIRED", "Scenario plans require stage=workflow-run and a discovered workflow receipt")
        workflow, workflow_ref = _schema_workflow_from_discovery(Path(workflow_discovery_path), root)
        if workflow.get("status") != "ready":
            raise CloudAnimationError("SCENARIO_WORKFLOW_NOT_READY", "only ready Scenario workflows can be planned")
        if not isinstance(workflow.get("id"), str) or not workflow["id"]:
            raise CloudAnimationError("WORKFLOW_ID_INVALID", "discovery receipt is missing workflow ID")
        if workflow.get("privacy") == "private" and not project_id:
            raise CloudAnimationError("SCENARIO_PROJECT_REQUIRED", "private Scenario workflows require --project-id")
        _validate_scenario_payload(workflow, payload)
        for index in sorted(used):
            if sources[index]["bytes"] >= MAX_SCENARIO_ASSET_BYTES_EXCLUSIVE:
                raise CloudAnimationError("SCENARIO_MULTIPART_NOT_SUPPORTED",
                                          "Scenario POST /assets accepts images below 4,000,000 bytes; multipart /uploads is not implemented")
            _read_bound_source(sources[index], "image", MAX_SCENARIO_ASSET_BYTES_EXCLUSIVE - 1)
    else:
        raise CloudAnimationError("PROVIDER_NOT_ADAPTED", f"provider is not wired: {provider}")

    action_ref = _normalize_action_plan(action_plan_path)
    for item in sources + ([action_ref] if action_ref else []):
        source_path = Path(item["path"])
        if source_path.resolve(strict=False) == operation_dir.resolve(strict=False) or source_path.resolve(strict=False).is_relative_to(operation_dir.resolve(strict=False)):
            raise CloudAnimationError("OUTPUT_INTERSECTS_INPUT", "operation output directory overlaps one of its inputs")
    plan = {
        "schema": PLAN_SCHEMA,
        "status": "PLANNED",
        "operation_id": operation_id,
        "provider": provider,
        "stage": stage,
        "payload": payload,
        "payload_sha256": sha256_bytes(canonical_json(payload)),
        "sources": sources,
        "source_marker_bindings": [{"field": field, "source_index": index} for field, index in markers],
        "native_action_plan": action_ref,
        "scenario_workflow": ({
            "id": workflow["id"], "name": workflow.get("name"), "privacy": workflow.get("privacy"),
            "status": workflow.get("status"), "project_id": project_id,
            "discovery": workflow_ref,
        } if provider == "scenario" else None),
        "operation_directory": str(operation_dir),
        "output_directory": str(operation_dir / "outputs"),
        "authorization_required": {"cost": True, "upload_or_remote_submission": True},
        "provider_limits": ({"scenario_asset_post_max_bytes_exclusive": MAX_SCENARIO_ASSET_BYTES_EXCLUSIVE,
                              "scenario_multipart_upload_implemented": False}
                             if provider == "scenario" else {}),
        "acceptance": {
            "cloud_job_success": False,
            "h3_action_group_complete": False,
            "native_frame_timing_verified": False,
            "alpha_verified": False,
            "art_approved": False,
            "runtime_accepted": False,
            "game_deployed": False,
        },
        "created_unix_seconds": int(time.time()),
    }
    return plan


def write_plan(plan: dict[str, Any], *, development_root: Path | None = None) -> Path:
    root = (development_root or DEVELOPMENT_ROOT).resolve(strict=False)
    operation_dir = Path(plan["operation_directory"]).resolve(strict=False)
    _assert_under(operation_dir, root, "OUTPUT_OUTSIDE_DEVELOPMENT_ROOT")
    if operation_dir.exists():
        raise CloudAnimationError("OPERATION_ALREADY_EXISTS", "operation ID already has a directory; refusing to replace it")
    try:
        operation_dir.mkdir(parents=True, exist_ok=False)
    except FileExistsError as error:
        raise CloudAnimationError("OPERATION_ALREADY_EXISTS", "operation ID already has a directory; refusing to replace it") from error
    data = (json.dumps(plan, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")
    try:
        with (operation_dir / "plan.json").open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        with (operation_dir / "plan.sha256").open("xb") as stream:
            stream.write((sha256_bytes(data) + "\n").encode("ascii"))
            stream.flush()
            os.fsync(stream.fileno())
    except OSError as error:
        # An incomplete operation directory intentionally remains as a collision to inspect.
        raise CloudAnimationError("PLAN_WRITE_FAILED", "could not persist plan; operation directory remains reserved") from error
    return operation_dir / "plan.json"


def validate_plan(plan_path: Path, *, development_root: Path | None = None,
                  require_fresh_output: bool = False) -> tuple[dict[str, Any], Path]:
    root = (development_root or DEVELOPMENT_ROOT).resolve(strict=False)
    try:
        resolved = plan_path.expanduser().resolve(strict=True)
    except OSError as error:
        raise CloudAnimationError("PLAN_UNAVAILABLE", "operation plan is unavailable") from error
    _assert_under(resolved, root, "PLAN_OUTSIDE_DEVELOPMENT_ROOT")
    if resolved.name != "plan.json":
        raise CloudAnimationError("PLAN_PATH_INVALID", "plan path must be the operation's plan.json")
    data = resolved.read_bytes()
    digest_path = resolved.with_name("plan.sha256")
    if not digest_path.is_file() or digest_path.read_text(encoding="ascii").strip() != sha256_bytes(data):
        raise CloudAnimationError("PLAN_SHA256_MISMATCH", "plan bytes differ from the frozen plan digest")
    try:
        plan = json.loads(data.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise CloudAnimationError("PLAN_INVALID", "plan is not valid UTF-8 JSON") from error
    if not isinstance(plan, dict) or plan.get("schema") != PLAN_SCHEMA:
        raise CloudAnimationError("PLAN_SCHEMA_UNSUPPORTED", f"expected {PLAN_SCHEMA}")
    if plan.get("provider") not in {"meshy", "scenario"} or not isinstance(plan.get("stage"), str):
        raise CloudAnimationError("PLAN_INVALID", "provider or stage is missing from operation plan")
    operation_dir = resolved.parent.resolve(strict=True)
    expected_dir = _operation_dir(plan.get("operation_id", ""), root).resolve(strict=False)
    if operation_dir != expected_dir or plan.get("operation_directory") != str(expected_dir):
        raise CloudAnimationError("OPERATION_ID_PATH_MISMATCH", "plan operation ID and path do not match")
    payload = plan.get("payload")
    if not isinstance(payload, dict) or plan.get("payload_sha256") != sha256_bytes(canonical_json(payload)):
        raise CloudAnimationError("PAYLOAD_SHA256_MISMATCH", "frozen provider payload changed")
    _sensitive_payload(payload)
    if not isinstance(plan.get("sources"), list):
        raise CloudAnimationError("PLAN_INVALID", "source receipt array is missing")
    for source in plan["sources"]:
        if not isinstance(source, dict) or not isinstance(source.get("path"), str) or not SHA256_RE.fullmatch(str(source.get("sha256", ""))) or type(source.get("bytes")) is not int:
            raise CloudAnimationError("PLAN_INVALID", "source receipt fields are malformed")
        path = Path(source.get("path", ""))
        current = _source_info(str(path))
        if current["sha256"] != source.get("sha256") or current["bytes"] != source.get("bytes"):
            raise CloudAnimationError("SOURCE_HASH_MISMATCH", f"source bytes changed: {path.name}")
    if plan["provider"] == "scenario":
        source_indices = {item.get("source_index") for item in plan.get("source_marker_bindings", [])
                          if isinstance(item, dict) and type(item.get("source_index")) is int}
        for index in source_indices:
            if index < 0 or index >= len(plan["sources"]):
                raise CloudAnimationError("PLAN_INVALID", "Scenario source index is outside the frozen source table")
            if plan["sources"][index]["bytes"] >= MAX_SCENARIO_ASSET_BYTES_EXCLUSIVE:
                raise CloudAnimationError("SCENARIO_MULTIPART_NOT_SUPPORTED",
                                          "Scenario POST /assets accepts images below 4,000,000 bytes; multipart /uploads is not implemented")
    action_ref = plan.get("native_action_plan")
    if action_ref:
        if not isinstance(action_ref, dict) or not isinstance(action_ref.get("path"), str):
            raise CloudAnimationError("PLAN_INVALID", "native action plan reference is malformed")
        current = _source_info(action_ref["path"])
        if current["sha256"] != action_ref.get("sha256") or current["bytes"] != action_ref.get("bytes"):
            raise CloudAnimationError("ACTION_PLAN_HASH_MISMATCH", "native action plan bytes changed")
    workflow_ref = (plan.get("scenario_workflow") or {}).get("discovery")
    if workflow_ref:
        current = _source_info(workflow_ref["path"])
        if current["sha256"] != workflow_ref.get("sha256") or current["bytes"] != workflow_ref.get("bytes"):
            raise CloudAnimationError("WORKFLOW_DISCOVERY_HASH_MISMATCH", "workflow discovery receipt changed")
        workflow, _ = _schema_workflow_from_discovery(Path(workflow_ref["path"]), root)
        if _workflow_schema_hash(workflow) != workflow_ref.get("inputs_sha256"):
            raise CloudAnimationError("WORKFLOW_SCHEMA_HASH_MISMATCH", "workflow input schema changed")
    output = Path(plan.get("output_directory", "")).resolve(strict=False)
    _assert_under(output, operation_dir, "OUTPUT_OUTSIDE_OPERATION")
    for source in plan["sources"] + ([action_ref] if action_ref else []):
        src = Path(source["path"]).resolve(strict=False)
        if src == output or src.is_relative_to(output) or output.is_relative_to(src):
            raise CloudAnimationError("OUTPUT_INTERSECTS_INPUT", "frozen output path overlaps a source input")
    if require_fresh_output and output.exists():
        raise CloudAnimationError("OUTPUT_COLLISION", "candidate output directory already exists")
    return plan, operation_dir


def _save_discovery(document: dict[str, Any], workflow_id: str | None,
                    development_root: Path | None = None) -> Path:
    root = (development_root or DEVELOPMENT_ROOT).resolve(strict=False)
    data = (json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")
    digest = sha256_bytes(data)
    name_id = (workflow_id or "public-list")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,160}", name_id, re.ASCII):
        name_id = "public-list"
    path = root / "discoveries" / f"{name_id}-{digest[:12]}.json"
    if path.exists():
        if path.read_bytes() != data:
            raise CloudAnimationError("DISCOVERY_COLLISION", "discovery path exists with different contents")
        return path
    _write_exclusive(path, data, parent_root=root)
    return path


def _normalize_workflow(workflow: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(workflow, dict):
        raise CloudAnimationError("WORKFLOW_RESPONSE_INVALID", "Scenario workflow response is not an object")
    inputs = workflow.get("inputs", [])
    if not isinstance(inputs, list):
        raise CloudAnimationError("WORKFLOW_SCHEMA_INVALID", "Scenario workflow inputs are not an array")
    return {
        "id": workflow.get("id"), "name": workflow.get("name"),
        "description": workflow.get("description"), "shortDescription": workflow.get("shortDescription"),
        "privacy": workflow.get("privacy"), "status": workflow.get("status"),
        "inputs": inputs,
    }


def scenario_discover(*, workflow_id: str | None = None, name: str | None = None,
                      project_id: str | None = None, environ: dict[str, str] | None = None,
                      transport: Callable = default_transport,
                      development_root: Path | None = None) -> dict[str, Any]:
    env = os.environ if environ is None else environ
    headers = _scenario_headers(dict(env))
    if workflow_id:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,160}", workflow_id, re.ASCII):
            raise CloudAnimationError("WORKFLOW_ID_INVALID", "workflow ID contains unsupported characters")
        query = ("?" + urlencode({"projectId": project_id}) if project_id else "")
        response = _request(transport, "GET", f"{SCENARIO_BASE}/workflows/{quote(workflow_id, safe='')}{query}", headers=headers)
        workflow = _normalize_workflow(response.get("workflow", response))
        if workflow.get("id") != workflow_id:
            raise CloudAnimationError("WORKFLOW_ID_MISMATCH", "Scenario returned a different workflow ID")
        document = {"schema": DISCOVERY_SCHEMA, "provider": "scenario", "workflow": workflow,
                    "inputs_sha256": _workflow_schema_hash(workflow), "api_endpoint": "/workflows/{workflowId}"}
        path = _save_discovery(document, workflow_id, development_root)
        return {"status": "DISCOVERED", "workflow_id": workflow_id, "name": workflow.get("name"),
                "privacy": workflow.get("privacy"), "lifecycle": workflow.get("status"),
                "input_count": len(workflow["inputs"]), "inputs_sha256": document["inputs_sha256"],
                "path": str(path), "path_sha256": sha256_file(path)}

    query: dict[str, Any] = {"privacy": "public", "pageSize": 100, "full": "false"}
    if project_id:
        query["projectId"] = project_id
    candidates = []
    pagination: str | None = None
    for _ in range(20):
        if pagination:
            query["paginationToken"] = pagination
        url = f"{SCENARIO_BASE}/workflows?{urlencode(query)}"
        response = _request(transport, "GET", url, headers=headers)
        rows = response.get("workflows", [])
        if not isinstance(rows, list):
            raise CloudAnimationError("WORKFLOW_LIST_INVALID", "Scenario workflow list is not an array")
        for row in rows:
            if not isinstance(row, dict):
                continue
            if name is None or name.casefold() in str(row.get("name", "")).casefold():
                candidates.append({"id": row.get("id"), "name": row.get("name"),
                                   "shortDescription": row.get("shortDescription"),
                                   "privacy": row.get("privacy"), "status": row.get("status"),
                                   "hasFlow": row.get("hasFlow"),
                                   "inputs": row.get("inputs", [])})
        pagination = response.get("nextPaginationToken")
        if not pagination:
            break
    else:
        raise CloudAnimationError("WORKFLOW_LIST_TOO_LARGE", "Scenario workflow list exceeded 20 pages")
    document = {"schema": DISCOVERY_SCHEMA, "provider": "scenario", "workflow": None,
                "inputs_sha256": None, "query": {"privacy": "public", "name_contains": name},
                "candidates": candidates, "api_endpoint": "/workflows"}
    path = _save_discovery(document, None, development_root)
    return {"status": "DISCOVERED", "candidate_count": len(candidates),
            "candidates": candidates, "path": str(path), "path_sha256": sha256_file(path),
            "note": "pass one workflow ID to retrieve its full input schema before planning"}


def _load_plan(operation_id: str, development_root: Path | None = None,
               *, fresh_output: bool = False) -> tuple[dict[str, Any], Path]:
    operation_dir = _operation_dir(operation_id, development_root)
    return validate_plan(operation_dir / "plan.json", development_root=development_root,
                         require_fresh_output=fresh_output)


def check_operation(operation_id: str, *, development_root: Path | None = None) -> dict[str, Any]:
    plan, operation_dir = _load_plan(operation_id, development_root, fresh_output=True)
    return {"schema": "h3.cloud-animation.check.v1", "status": "READY",
            "operation_id": operation_id, "provider": plan["provider"], "stage": plan["stage"],
            "source_count": len(plan["sources"]), "payload_sha256": plan["payload_sha256"],
            "output_directory": plan["output_directory"],
            "provider_limits": plan.get("provider_limits", {}),
            "operation_state": "not-submitted" if not _state_path(operation_dir).exists() else "state-exists-review-required",
            "acceptance": plan["acceptance"]}


def _resolve_scenario_payload(plan: dict[str, Any], state: dict[str, Any] | None) -> dict[str, Any]:
    payload = json.loads(json.dumps(plan["payload"]))
    uploads = (state or {}).get("asset_uploads", {})
    def resolve(value: Any) -> Any:
        marker = _source_marker(value)
        if marker is not None:
            record = uploads.get(str(marker))
            if not record or record.get("phase") != "UPLOADED" or not isinstance(record.get("asset_id"), str):
                raise CloudAnimationError("SCENARIO_SOURCE_NOT_UPLOADED", f"source {marker} must be uploaded with submit --upload-only before dry-run/run")
            return record["asset_id"]
        if isinstance(value, list):
            return [resolve(item) for item in value]
        if isinstance(value, dict):
            return {key: resolve(child) for key, child in value.items()}
        return value
    return {key: resolve(value) for key, value in payload.items()}


def _scenario_base_headers(environ: dict[str, str]) -> dict[str, str]:
    headers = _scenario_headers(environ)
    headers["Content-Type"] = "application/json"
    return headers


def _fetch_live_workflow(plan: dict[str, Any], *, environ: dict[str, str],
                         transport: Callable) -> dict[str, Any]:
    pinned = plan.get("scenario_workflow")
    if not isinstance(pinned, dict):
        raise CloudAnimationError("SCENARIO_WORKFLOW_MISSING", "operation has no pinned Scenario workflow")
    query = {"projectId": pinned["project_id"]} if pinned.get("project_id") else {}
    suffix = ("?" + urlencode(query)) if query else ""
    url = f"{SCENARIO_BASE}/workflows/{quote(pinned['id'], safe='')}{suffix}"
    data = _request(transport, "GET", url, headers=_scenario_headers(environ))
    workflow = _normalize_workflow(data.get("workflow", data))
    if workflow.get("id") != pinned["id"] or _workflow_schema_hash(workflow) != pinned.get("discovery", {}).get("inputs_sha256"):
        raise CloudAnimationError("SCENARIO_WORKFLOW_SCHEMA_DRIFT", "live Scenario workflow ID or input schema differs from the frozen plan")
    if workflow.get("status") != "ready":
        raise CloudAnimationError("SCENARIO_WORKFLOW_NOT_READY", "Scenario workflow is not ready")
    return workflow


def _state_default(plan: dict[str, Any]) -> dict[str, Any]:
    return {"schema": STATE_SCHEMA, "operation_id": plan["operation_id"], "provider": plan["provider"],
            "stage": plan["stage"], "phase": "PLANNED", "asset_uploads": {},
            "provider_job_id": None, "last_status": None, "last_dry_run": None,
            "h3_acceptance": plan["acceptance"]}


def _read_state(operation_dir: Path) -> dict[str, Any] | None:
    path = _state_path(operation_dir)
    if not path.exists():
        return None
    state = _read_json(path, "STATE_INVALID")
    if not isinstance(state, dict) or state.get("schema") != STATE_SCHEMA:
        raise CloudAnimationError("STATE_SCHEMA_INVALID", "operation state schema is not supported")
    return state


def _write_guard(path: Path, operation_id: str, *, root: Path, phase: str) -> None:
    data = (json.dumps({"schema": "h3.cloud-animation.guard.v1", "operation_id": operation_id,
                        "phase": phase, "created_unix_seconds": int(time.time())}, sort_keys=True) + "\n").encode("utf-8")
    _write_exclusive(path, data, parent_root=root)


def _encode_meshy_payload(plan: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    payload = json.loads(json.dumps(plan["payload"]))
    sent_sources: list[dict[str, Any]] = []
    source_fields = MESHY_STAGES[plan["stage"]]["sources"]
    for field, expected_kind in source_fields.items():
        if field not in payload:
            continue
        marker = _source_marker(payload[field])
        if marker is None:
            continue
        if marker >= len(plan["sources"]):
            raise CloudAnimationError("SOURCE_INDEX_OUT_OF_RANGE", "frozen plan has an invalid source marker")
        max_bytes = (20 * 1024 * 1024 if expected_kind == "image"
                     else MESHY_STAGES[plan["stage"]]["max_source_bytes"])
        mime, data, receipt = _read_bound_source(plan["sources"][marker], expected_kind, max_bytes)
        receipt["source_index"] = marker
        sent_sources.append(receipt)
        payload[field] = f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"
    return payload, sent_sources


def _find_asset_id(value: Any) -> str | None:
    if isinstance(value, dict):
        for key in ("assetId", "asset_id"):
            candidate = value.get(key)
            if isinstance(candidate, str) and ASSET_ID_RE.fullmatch(candidate):
                return candidate
        for child in value.values():
            found = _find_asset_id(child)
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find_asset_id(child)
            if found:
                return found
    return None


def _scenario_upload_sources(plan: dict[str, Any], operation_dir: Path, state: dict[str, Any], *,
                             environ: dict[str, str], transport: Callable,
                             development_root: Path) -> dict[str, Any]:
    markers = sorted({index for _, index in _walk_source_markers(plan["payload"])})
    if not markers:
        state["phase"] = "ASSETS_UPLOADED"
        _write_state(_state_path(operation_dir), state, root=development_root)
        return state
    guard = operation_dir / "asset-upload.claim"
    _write_guard(guard, plan["operation_id"], root=development_root, phase="SCENARIO_SOURCE_UPLOAD")
    headers = _scenario_base_headers(environ)
    for index in markers:
        existing = state["asset_uploads"].get(str(index))
        if existing:
            if existing.get("phase") == "UPLOADED":
                continue
            raise CloudAnimationError("SCENARIO_UPLOAD_ALREADY_CLAIMED", "a prior upload outcome is unresolved; do not repeat it")
        state["asset_uploads"][str(index)] = {"phase": "UPLOADING"}
        state["phase"] = "SOURCE_UPLOAD_IN_PROGRESS"
        _write_state(_state_path(operation_dir), state, root=development_root)
        path = Path(plan["sources"][index]["path"])
        if plan["sources"][index]["bytes"] >= MAX_SCENARIO_ASSET_BYTES_EXCLUSIVE:
            state["asset_uploads"][str(index)] = {"phase": "UPLOAD_REJECTED", "error_code": "SCENARIO_MULTIPART_NOT_SUPPORTED"}
            state["phase"] = "UPLOAD_REJECTED"
            _write_state(_state_path(operation_dir), state, root=development_root)
            raise CloudAnimationError("SCENARIO_MULTIPART_NOT_SUPPORTED",
                                      "Scenario POST /assets accepts images below 4,000,000 bytes; multipart /uploads is not implemented")
        mime, data, source_receipt = _read_bound_source(plan["sources"][index], "image",
                                                        MAX_SCENARIO_ASSET_BYTES_EXCLUSIVE - 1)
        source_receipt["source_index"] = index
        state["asset_uploads"][str(index)] = {"phase": "UPLOADING", **source_receipt}
        state["phase"] = "SOURCE_UPLOAD_IN_PROGRESS"
        _write_state(_state_path(operation_dir), state, root=development_root)
        body = {"name": path.name, "mimeType": mime,
                "image": base64.b64encode(data).decode("ascii"), "privacy": "private"}
        try:
            response = _request(transport, "POST", f"{SCENARIO_BASE}/assets", headers=headers, payload=body)
        except CloudAnimationError as error:
            phase = "UPLOAD_REJECTED" if error.code == "PROVIDER_REQUEST_REJECTED" else "UPLOAD_UNKNOWN"
            state["asset_uploads"][str(index)] = {"phase": phase, "error_code": error.code}
            state["phase"] = phase
            _write_state(_state_path(operation_dir), state, root=development_root)
            raise
        asset_id = _find_asset_id(response)
        if not asset_id:
            state["asset_uploads"][str(index)] = {"phase": "UPLOAD_UNKNOWN", "error_code": "ASSET_ID_MISSING"}
            state["phase"] = "UPLOAD_UNKNOWN"
            _write_state(_state_path(operation_dir), state, root=development_root)
            raise CloudAnimationError("SCENARIO_UPLOAD_UNKNOWN", "Scenario upload response did not contain an asset ID; do not repeat the upload")
        state["asset_uploads"][str(index)] = {"phase": "UPLOADED", "asset_id": asset_id,
                                               "source_sha256": source_receipt["sha256"],
                                               "source_bytes": source_receipt["bytes"],
                                               "source_index": index}
        state["phase"] = "SOURCE_UPLOAD_IN_PROGRESS"
        _write_state(_state_path(operation_dir), state, root=development_root)
    state["phase"] = "ASSETS_UPLOADED"
    _write_state(_state_path(operation_dir), state, root=development_root)
    return state


def scenario_dry_run(operation_id: str, *, mode: str = "api", environ: dict[str, str] | None = None,
                     transport: Callable = default_transport,
                     development_root: Path | None = None) -> dict[str, Any]:
    if mode not in {"api", "true"}:
        raise CloudAnimationError("DRY_RUN_MODE_INVALID", "Scenario dry-run mode must be api or true")
    root = (development_root or DEVELOPMENT_ROOT).resolve(strict=False)
    plan, operation_dir = _load_plan(operation_id, root)
    if plan["provider"] != "scenario":
        raise CloudAnimationError("DRY_RUN_UNAVAILABLE", "Meshy documents no dry-run endpoint; no request was sent")
    env = dict(os.environ if environ is None else environ)
    workflow = _fetch_live_workflow(plan, environ=env, transport=transport)
    state = _read_state(operation_dir)
    payload = _resolve_scenario_payload(plan, state)
    project_id = (plan.get("scenario_workflow") or {}).get("project_id")
    query = {"dryRun": mode}
    if project_id:
        query["projectId"] = project_id
    url = f"{SCENARIO_BASE}/workflows/{quote(workflow['id'], safe='')}/run?{urlencode(query)}"
    response, http_status = _request(transport, "PUT", url, headers=_scenario_base_headers(env), payload=payload,
                                     allow_cost_estimate=True, with_status=True)
    summary = {"schema": "h3.cloud-animation.dry-run.v1", "status": "ESTIMATED",
               "operation_id": operation_id, "workflow_id": workflow["id"],
               "http_status": http_status,
               "estimate": response, "job_submitted": False,
               "h3_action_group_complete": False, "art_approved": False}
    current = state or _state_default(plan)
    current["last_dry_run"] = {"status": "ESTIMATED", "workflow_id": workflow["id"],
                               "result_sha256": sha256_bytes(canonical_json(response))}
    _write_state(_state_path(operation_dir), current, root=root)
    return summary


def submit_operation(operation_id: str, *, authorize_cost: bool = False,
                     authorize_upload: bool = False, upload_only: bool = False,
                     environ: dict[str, str] | None = None,
                     transport: Callable = default_transport,
                     development_root: Path | None = None) -> dict[str, Any]:
    if not authorize_upload:
        raise CloudAnimationError("UPLOAD_AUTHORIZATION_REQUIRED", "pass --authorize-upload to send local sources, prompts, or asset references to a provider")
    if upload_only:
        if not authorize_cost:
            # Uploading assets does not run model generation; cost authorization remains a separate later step.
            pass
    elif not authorize_cost:
        raise CloudAnimationError("COST_AUTHORIZATION_REQUIRED", "pass --authorize-cost to start a billable provider operation")
    root = (development_root or DEVELOPMENT_ROOT).resolve(strict=False)
    plan, operation_dir = _load_plan(operation_id, root, fresh_output=True)
    env = dict(os.environ if environ is None else environ)
    state = _read_state(operation_dir) or _state_default(plan)
    mesh_headers: dict[str, str] | None = None
    mesh_body: dict[str, Any] | None = None
    mesh_source_receipts: list[dict[str, Any]] = []
    if plan["provider"] == "scenario":
        _fetch_live_workflow(plan, environ=env, transport=transport)
        if upload_only:
            if _state_path(operation_dir).exists() and state.get("phase") in {"ASSETS_UPLOADED", "SUBMITTED", "RUNNING", "SUCCEEDED"}:
                return {"schema": "h3.cloud-animation.submit.v1", "status": state["phase"],
                        "operation_id": operation_id, "provider_job_id": state.get("provider_job_id"),
                        "duplicate_submit_blocked": True}
            _write_state(_state_path(operation_dir), state, root=root) if not _state_path(operation_dir).exists() else None
            state = _scenario_upload_sources(plan, operation_dir, state, environ=env,
                                             transport=transport, development_root=root)
            return {"schema": "h3.cloud-animation.submit.v1", "status": "ASSETS_UPLOADED",
                    "operation_id": operation_id,
                    "asset_count": len(state.get("asset_uploads", {})),
                    "cost_authorization_consumed": False,
                    "next": "run dry-run, then submit with --authorize-cost --authorize-upload"}
        if _walk_source_markers(plan["payload"]):
            if not state.get("asset_uploads") or any(row.get("phase") != "UPLOADED" for row in state["asset_uploads"].values()):
                raise CloudAnimationError("SCENARIO_UPLOAD_STEP_REQUIRED", "local sources require a prior submit --upload-only --authorize-upload; then dry-run before the billable run")
            _resolve_scenario_payload(plan, state)
    elif plan["provider"] == "meshy":
        mesh_headers = _mesh_headers(env)
        if upload_only:
            raise CloudAnimationError("UPLOAD_ONLY_UNSUPPORTED", "Meshy local sources are included in the one explicitly authorized job request")
        # Freeze the exact bytes to be sent before making a durable submission claim.
        mesh_body, mesh_source_receipts = _encode_meshy_payload(plan)
    else:
        raise CloudAnimationError("PROVIDER_NOT_ADAPTED", f"provider is not adapted: {plan['provider']}")

    claim = operation_dir / "submit.claim"
    _write_guard(claim, operation_id, root=root, phase="PROVIDER_SUBMIT")
    state.update({"schema": STATE_SCHEMA, "operation_id": operation_id,
                  "provider": plan["provider"], "stage": plan["stage"],
                  "phase": "SUBMITTING", "provider_job_id": None,
                  "submitted_sources": mesh_source_receipts,
                  "h3_acceptance": plan["acceptance"]})
    _write_state(_state_path(operation_dir), state, root=root)
    try:
        if plan["provider"] == "meshy":
            config = MESHY_STAGES[plan["stage"]]
            url = f"{MESHY_BASE}/{config['path']}"
            response = _request(transport, config["method"], url,
                                headers={**(mesh_headers or {}), "Content-Type": "application/json"},
                                payload=mesh_body)
            task_id = response.get("result") if isinstance(response, dict) else None
        else:
            headers = _scenario_base_headers(env)
            project_id = (plan.get("scenario_workflow") or {}).get("project_id")
            payload = _resolve_scenario_payload(plan, state)
            query = {"projectId": project_id} if project_id else {}
            suffix = ("?" + urlencode(query)) if query else ""
            url = f"{SCENARIO_BASE}/workflows/{quote(plan['scenario_workflow']['id'], safe='')}/run{suffix}"
            response = _request(transport, "PUT", url, headers=headers, payload=payload)
            job = response.get("job", {}) if isinstance(response, dict) else {}
            task_id = job.get("jobId") if isinstance(job, dict) else None
    except CloudAnimationError as error:
        state["phase"] = "SUBMIT_REJECTED" if error.code == "PROVIDER_REQUEST_REJECTED" else "SUBMIT_UNKNOWN"
        state["last_error_code"] = error.code
        _write_state(_state_path(operation_dir), state, root=root)
        raise
    if not isinstance(task_id, str) or not task_id or len(task_id) > 256:
        state["phase"] = "SUBMIT_UNKNOWN"
        state["last_error_code"] = "PROVIDER_JOB_ID_MISSING"
        _write_state(_state_path(operation_dir), state, root=root)
        raise CloudAnimationError("SUBMIT_UNKNOWN", "provider accepted or returned an ambiguous response without a stable job ID; do not resubmit this operation")
    state["phase"] = "SUBMITTED"
    state["provider_job_id"] = task_id
    state["last_error_code"] = None
    _write_state(_state_path(operation_dir), state, root=root)
    return {"schema": "h3.cloud-animation.submit.v1", "status": "SUBMITTED",
            "operation_id": operation_id, "provider": plan["provider"],
            "provider_job_id": task_id, "poll_command": f"status --operation-id {operation_id}",
            "h3_action_group_complete": False, "art_approved": False,
            "alpha_verified": False, "runtime_accepted": False}


def _normalize_cloud_status(provider: str, data: dict[str, Any]) -> dict[str, Any]:
    job = data.get("job") if provider == "scenario" and isinstance(data.get("job"), dict) else data
    if not isinstance(job, dict):
        job = {}
    raw_value = str(job.get("status", "UNKNOWN"))
    raw_status = raw_value.casefold() if provider == "scenario" else raw_value.upper()
    completed = raw_status in ({"success"} if provider == "scenario" else {"SUCCEEDED", "SUCCESS", "COMPLETED"})
    failed = raw_status in ({"failure"} if provider == "scenario" else {"FAILED", "FAILURE", "ERROR"})
    canceled = raw_status in ({"canceled"} if provider == "scenario" else {"CANCELED", "CANCELLED"})
    if completed:
        normalized = "CLOUD_SUCCEEDED_REVIEW_REQUIRED"
    elif failed:
        normalized = "CLOUD_FAILED"
    elif canceled:
        normalized = "CLOUD_CANCELED"
    elif raw_status in ({"pending", "queued", "warming-up", "in-progress", "processing", "finalizing"}
                        if provider == "scenario" else {"PENDING", "IN_PROGRESS", "RUNNING", "QUEUED"}):
        normalized = "CLOUD_RUNNING"
    else:
        normalized = "CLOUD_STATUS_UNKNOWN"
    progress = job.get("progress")
    if type(progress) not in (int, float) or not math.isfinite(float(progress)):
        progress = None
    consumed = job.get("consumed_credits")
    if type(consumed) is not int or consumed < 0:
        consumed = None
    return {"status": normalized, "provider_status": raw_status,
            "progress": progress, "consumed_credits": consumed,
            "h3_action_group_complete": False, "native_frame_timing_verified": False,
            "alpha_verified": False, "art_approved": False,
            "runtime_accepted": False, "game_deployed": False}


def status_operation(operation_id: str, *, environ: dict[str, str] | None = None,
                     transport: Callable = default_transport,
                     development_root: Path | None = None) -> dict[str, Any]:
    root = (development_root or DEVELOPMENT_ROOT).resolve(strict=False)
    plan, operation_dir = _load_plan(operation_id, root)
    state = _read_state(operation_dir)
    if state is None:
        return {"schema": "h3.cloud-animation.status.v1", "status": "NOT_SUBMITTED",
                "operation_id": operation_id, "provider_job_id": None,
                "h3_action_group_complete": False, "art_approved": False}
    if state.get("phase") in {"SUBMITTING", "SUBMIT_UNKNOWN", "UPLOAD_UNKNOWN", "SOURCE_UPLOAD_IN_PROGRESS"}:
        return {"schema": "h3.cloud-animation.status.v1", "status": "OUTCOME_UNKNOWN_NO_RETRY",
                "operation_id": operation_id, "phase": state.get("phase"),
                "provider_job_id": state.get("provider_job_id"),
                "message": "request outcome is ambiguous or a request was interrupted; this command did not resubmit it"}
    if not state.get("provider_job_id"):
        return {"schema": "h3.cloud-animation.status.v1", "status": state.get("phase", "PLANNED"),
                "operation_id": operation_id, "provider_job_id": None,
                "asset_count": sum(row.get("phase") == "UPLOADED" for row in state.get("asset_uploads", {}).values()),
                "h3_action_group_complete": False, "art_approved": False}
    env = dict(os.environ if environ is None else environ)
    try:
        if plan["provider"] == "meshy":
            headers = _mesh_headers(env)
            template = MESHY_STAGES[plan["stage"]]["status_path"]
            url = f"{MESHY_BASE}/{template.format(id=quote(state['provider_job_id'], safe=''))}"
        else:
            headers = _scenario_headers(env)
            url = f"{SCENARIO_BASE}/jobs/{quote(state['provider_job_id'], safe='')}"
        data = _request(transport, "GET", url, headers=headers)
    except CloudAnimationError as error:
        return {"schema": "h3.cloud-animation.status.v1", "status": "STATUS_UNAVAILABLE",
                "operation_id": operation_id, "provider_job_id": state["provider_job_id"],
                "error_code": error.code, "message": "status request failed; stored operation identity was preserved"}
    summary = _normalize_cloud_status(plan["provider"], data if isinstance(data, dict) else {})
    state["last_status"] = {key: summary[key] for key in ("status", "provider_status", "progress", "consumed_credits")}
    if summary["status"] == "CLOUD_RUNNING":
        state["phase"] = "RUNNING"
    elif summary["status"] == "CLOUD_SUCCEEDED_REVIEW_REQUIRED":
        state["phase"] = "SUCCEEDED_REVIEW_REQUIRED"
    elif summary["status"] == "CLOUD_FAILED":
        state["phase"] = "CLOUD_FAILED"
    elif summary["status"] == "CLOUD_CANCELED":
        state["phase"] = "CLOUD_CANCELED"
    _write_state(_state_path(operation_dir), state, root=root)
    return {"schema": "h3.cloud-animation.status.v1", "operation_id": operation_id,
            "provider_job_id": state["provider_job_id"], **summary}


def _asset_ids_from_job(data: Any) -> list[str]:
    found: list[str] = []
    def walk(value: Any) -> None:
        if isinstance(value, dict):
            candidate = value.get("assetId") or value.get("asset_id")
            if isinstance(candidate, str) and ASSET_ID_RE.fullmatch(candidate):
                found.append(candidate)
            for key, child in value.items():
                if key in {"assetIds", "asset_ids"} and isinstance(child, list):
                    found.extend(item for item in child if isinstance(item, str) and ASSET_ID_RE.fullmatch(item))
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)
    walk(data)
    return list(dict.fromkeys(found))


def _mesh_asset_urls(plan: dict[str, Any], data: dict[str, Any]) -> list[tuple[str, str]]:
    result = data.get("result") if isinstance(data.get("result"), dict) else data
    urls: list[tuple[str, str]] = []
    def add(label: str, value: Any) -> None:
        if isinstance(value, str) and value.startswith("https://"):
            parsed = urlsplit(value)
            if parsed.hostname and parsed.hostname.lower() == "assets.meshy.ai":
                urls.append((label, value))
    if plan["stage"] == "image-to-3d":
        for label, value in (result.get("model_urls", {}) or {}).items():
            add(f"model-{label}", value)
        if not urls:
            for key in ("glb", "fbx", "obj"):
                add(f"model-{key}", result.get(f"model_{key}_url"))
    elif plan["stage"] == "rig":
        for key, label in (("rigged_character_glb_url", "rigged-glb"), ("rigged_character_fbx_url", "rigged-fbx")):
            add(label, result.get(key))
        for action, artifacts in (result.get("basic_animations", {}) or {}).items():
            if isinstance(artifacts, dict):
                for extension, value in artifacts.items():
                    add(f"{action}-{extension}", value)
    elif plan["stage"] == "animation":
        for key, label in (("animation_glb_url", "animation-glb"), ("animation_fbx_url", "animation-fbx"),
                           ("processed_armature_fbx_url", "armature-fbx"), ("processed_animation_fps_fbx_url", "animation-fps-fbx")):
            add(label, result.get(key))
    elif plan["stage"] == "text-to-motion":
        add("motion", result.get("motion_url"))
    return urls


def _safe_download_url(url: str) -> str:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise CloudAnimationError("DOWNLOAD_URL_REJECTED", "provider returned an unsafe download URL")
    host = parsed.hostname.lower()
    if host in {"localhost", "localhost.localdomain"} or host.endswith((".local", ".localhost", ".internal")):
        raise CloudAnimationError("DOWNLOAD_URL_REJECTED", "provider returned a local or internal download host")
    try:
        ip = ipaddress.ip_address(host)
        if not ip.is_global:
            raise CloudAnimationError("DOWNLOAD_URL_REJECTED", "provider returned a non-public download address")
    except ValueError:
        if "." not in host or host.endswith((".local", ".localhost")):
            raise CloudAnimationError("DOWNLOAD_URL_REJECTED", "provider returned a local or malformed download host")
    return url


def _download_with_transport(transport: Callable, url: str, *, max_bytes: int = MAX_DOWNLOAD_BYTES) -> tuple[bytes, dict[str, str]]:
    safe_url = _safe_download_url(url)
    response = transport("GET", safe_url, {"Accept": "*/*"}, None, DEFAULT_TIMEOUT_SECONDS)
    if not 200 <= response.status < 300:
        raise CloudAnimationError("DOWNLOAD_FAILED", f"asset download returned HTTP {response.status}")
    if len(response.body) > max_bytes:
        raise CloudAnimationError("DOWNLOAD_TOO_LARGE", "asset download exceeded configured byte limit")
    return response.body, response.headers


def _scenario_asset_links(job_data: dict[str, Any], *, environ: dict[str, str],
                          transport: Callable) -> list[tuple[str, str]]:
    asset_ids = _asset_ids_from_job(job_data)
    if len(asset_ids) > MAX_SCENARIO_OUTPUT_ASSETS:
        raise CloudAnimationError("ASSET_LIMIT_EXCEEDED",
                                  f"Scenario job returned {len(asset_ids)} assets; fetch limit is {MAX_SCENARIO_OUTPUT_ASSETS}, refusing partial output")
    result = []
    headers = _scenario_headers(environ)
    for asset_id in asset_ids:
        details = _request(transport, "GET", f"{SCENARIO_BASE}/assets/{quote(asset_id, safe='')}", headers=headers)
        asset = details.get("asset", details) if isinstance(details, dict) else {}
        url = asset.get("originalFileUrl") or asset.get("url") if isinstance(asset, dict) else None
        if isinstance(url, str) and url.startswith("https://"):
            result.append((asset_id, url))
    return result


def _extension(label: str, url: str, headers: dict[str, str]) -> str:
    content_type = str(headers.get("Content-Type", "")).split(";", 1)[0].lower()
    known = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp",
             "video/mp4": ".mp4", "image/gif": ".gif", "model/gltf-binary": ".glb",
             "application/octet-stream": ""}
    if content_type in known and known[content_type]:
        return known[content_type]
    suffix = Path(urlsplit(url).path).suffix.lower()
    return suffix if suffix in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".mp4", ".webm", ".mov", ".glb", ".fbx", ".bvh", ".obj"} else ".bin"


def fetch_operation(operation_id: str, *, environ: dict[str, str] | None = None,
                    transport: Callable = default_transport,
                    development_root: Path | None = None) -> dict[str, Any]:
    root = (development_root or DEVELOPMENT_ROOT).resolve(strict=False)
    plan, operation_dir = _load_plan(operation_id, root)
    state = _read_state(operation_dir)
    if not state or not state.get("provider_job_id"):
        raise CloudAnimationError("FETCH_JOB_ID_REQUIRED", "fetch requires a known submitted provider job ID")
    previous = state.get("last_status")
    if not previous or previous.get("status") != "CLOUD_SUCCEEDED_REVIEW_REQUIRED":
        status = status_operation(operation_id, environ=environ, transport=transport, development_root=root)
        if status.get("status") != "CLOUD_SUCCEEDED_REVIEW_REQUIRED":
            raise CloudAnimationError("FETCH_NOT_READY", "cloud job is not known to have succeeded; no candidate was downloaded")
        state = _read_state(operation_dir) or state
    output_dir = Path(plan["output_directory"]).resolve(strict=False)
    _assert_under(output_dir, operation_dir, "OUTPUT_OUTSIDE_OPERATION")
    if output_dir.exists():
        raise CloudAnimationError("OUTPUT_COLLISION", "candidate output directory already exists; fetch will not overwrite or mix files")
    env = dict(os.environ if environ is None else environ)
    if plan["provider"] == "meshy":
        config = MESHY_STAGES[plan["stage"]]
        headers = _mesh_headers(env)
        detail_url = f"{MESHY_BASE}/{config['status_path'].format(id=quote(state['provider_job_id'], safe=''))}"
        data = _request(transport, "GET", detail_url, headers=headers)
        links = _mesh_asset_urls(plan, data)
    elif plan["provider"] == "scenario":
        headers = _scenario_headers(env)
        job = _request(transport, "GET", f"{SCENARIO_BASE}/jobs/{quote(state['provider_job_id'], safe='')}", headers=headers)
        links = _scenario_asset_links(job, environ=env, transport=transport)
    else:
        raise CloudAnimationError("PROVIDER_NOT_ADAPTED", "no asset fetch adapter for provider")
    if not links:
        raise CloudAnimationError("NO_PROVIDER_ASSETS", "successful cloud job returned no supported downloadable assets")
    try:
        output_dir.mkdir(parents=True, exist_ok=False)
    except FileExistsError as error:
        raise CloudAnimationError("OUTPUT_COLLISION", "candidate output directory appeared during preflight") from error
    files = []
    for index, (label, url) in enumerate(links):
        body, response_headers = _download_with_transport(transport, url)
        suffix = _extension(label, url, response_headers)
        safe_label = re.sub(r"[^A-Za-z0-9_-]+", "-", label).strip("-")[:64] or f"asset-{index}"
        path = output_dir / f"{index:03d}-{safe_label}{suffix}"
        try:
            with path.open("xb") as stream:
                stream.write(body)
                stream.flush()
                os.fsync(stream.fileno())
        except OSError as error:
            raise CloudAnimationError("FETCH_WRITE_PARTIAL", "some candidate files may have been written; inspect the isolated output directory") from error
        files.append({"path": str(path), "bytes": len(body), "sha256": sha256_bytes(body),
                      "mime_type": response_headers.get("Content-Type"), "candidate_only": True})
    receipt = {"schema": RECEIPT_SCHEMA, "operation_id": operation_id,
               "provider": plan["provider"], "stage": plan["stage"],
               "provider_job_id": state["provider_job_id"], "files": files,
               "art_approved": False, "alpha_verified": False,
               "h3_action_group_complete": False, "native_frame_timing_verified": False,
               "runtime_accepted": False, "game_deployed": False}
    receipt_path = output_dir / "fetch-receipt.json"
    data = (json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    try:
        with receipt_path.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except OSError as error:
        raise CloudAnimationError("FETCH_RECEIPT_WRITE_FAILED", "candidate files may exist without a complete receipt") from error
    state["phase"] = "FETCHED_CANDIDATE_REVIEW_REQUIRED"
    state["fetch_receipt_sha256"] = sha256_bytes(data)
    _write_state(_state_path(operation_dir), state, root=root)
    return {"schema": RECEIPT_SCHEMA, "status": "CANDIDATE_REVIEW_REQUIRED",
            "operation_id": operation_id, "files": files,
            "art_approved": False, "alpha_verified": False,
            "h3_action_group_complete": False, "native_frame_timing_verified": False,
            "runtime_accepted": False, "game_deployed": False}


def catalog() -> dict[str, Any]:
    return {"schema": "h3.cloud-animation.catalog.v1", "providers": list(PROVIDER_CATALOG),
            "defaults": {"output_root": str(DEVELOPMENT_ROOT.resolve(strict=False)),
                         "mixamo_api": False, "anything_world_adapter": False,
                         "spritelab_adapters": False},
            "important": "Provider job success is not H3 action, alpha, native timing, art, or game acceptance."}


def _parse_payload(args: argparse.Namespace) -> dict[str, Any]:
    if bool(args.payload_json) == bool(args.payload_file):
        raise CloudAnimationError("PAYLOAD_SOURCE_REQUIRED", "provide exactly one of --payload-json or --payload-file")
    if args.payload_json:
        raw = args.payload_json
    else:
        path = Path(args.payload_file).expanduser()
        if not path.is_absolute():
            raise CloudAnimationError("PAYLOAD_PATH_ABSOLUTE_REQUIRED", "payload file path must be absolute")
        try:
            raw = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            raise CloudAnimationError("PAYLOAD_FILE_UNAVAILABLE", "payload file could not be read") from error
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as error:
        raise CloudAnimationError("PAYLOAD_JSON_INVALID", "provider payload must be valid JSON") from error
    if not isinstance(value, dict):
        raise CloudAnimationError("PAYLOAD_INVALID", "provider payload must be a JSON object")
    return value


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Plan and run explicit H3 motion-provider candidates")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("catalog", help="show implemented and documented-but-unadapted providers")
    discover = sub.add_parser("discover", help="discover Scenario workflows and freeze their current input schema")
    discover.add_argument("--workflow-id")
    discover.add_argument("--name")
    discover.add_argument("--project-id")
    plan = sub.add_parser("plan", help="freeze source hashes, provider body, workflow schema, and output root")
    plan.add_argument("--operation-id", required=True)
    plan.add_argument("--provider", required=True, choices=("meshy", "scenario"))
    plan.add_argument("--stage", required=True)
    plan.add_argument("--payload-json")
    plan.add_argument("--payload-file")
    plan.add_argument("--source", action="append", default=[])
    plan.add_argument("--action-plan")
    plan.add_argument("--workflow-discovery")
    plan.add_argument("--project-id")
    check = sub.add_parser("check", help="offline hash/schema/authorization preflight")
    check.add_argument("--operation-id", required=True)
    dry = sub.add_parser("dry-run", help="Scenario credit estimate only; Meshy has no documented dry-run endpoint")
    dry.add_argument("--operation-id", required=True)
    dry.add_argument("--mode", choices=("api", "true"), default="api")
    submit = sub.add_parser("submit", help="explicitly upload Scenario sources or submit one cloud job")
    submit.add_argument("--operation-id", required=True)
    submit.add_argument("--authorize-cost", action="store_true")
    submit.add_argument("--authorize-upload", action="store_true")
    submit.add_argument("--upload-only", action="store_true")
    status = sub.add_parser("status", help="poll an existing cloud job ID; never resubmits")
    status.add_argument("--operation-id", required=True)
    fetch = sub.add_parser("fetch", help="download a successful job into the isolated candidate directory")
    fetch.add_argument("--operation-id", required=True)
    return parser


def main(argv: list[str] | None = None, *, transport: Callable = default_transport,
         development_root: Path | None = None, environ: dict[str, str] | None = None) -> int:
    parser = make_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "catalog":
            result = catalog()
        elif args.command == "discover":
            if not args.workflow_id and not args.name:
                raise CloudAnimationError("DISCOVERY_QUERY_REQUIRED", "pass --workflow-id or --name")
            result = scenario_discover(workflow_id=args.workflow_id, name=args.name,
                                      project_id=args.project_id, environ=environ,
                                      transport=transport, development_root=development_root)
        elif args.command == "plan":
            payload = _parse_payload(args)
            plan = build_operation_plan(provider=args.provider, stage=args.stage, payload=payload,
                                        source_paths=args.source, operation_id=args.operation_id,
                                        action_plan_path=args.action_plan,
                                        workflow_discovery_path=args.workflow_discovery,
                                        project_id=args.project_id,
                                        development_root=development_root)
            path = write_plan(plan, development_root=development_root)
            result = {"schema": PLAN_SCHEMA, "status": "PLANNED", "operation_id": args.operation_id,
                      "path": str(path), "sha256": sha256_file(path),
                      "source_count": len(plan["sources"]), "payload_sha256": plan["payload_sha256"]}
        elif args.command == "check":
            result = check_operation(args.operation_id, development_root=development_root)
        elif args.command == "dry-run":
            result = scenario_dry_run(args.operation_id, mode=args.mode, environ=environ,
                                      transport=transport, development_root=development_root)
        elif args.command == "submit":
            result = submit_operation(args.operation_id, authorize_cost=args.authorize_cost,
                                      authorize_upload=args.authorize_upload, upload_only=args.upload_only,
                                      environ=environ, transport=transport, development_root=development_root)
        elif args.command == "status":
            result = status_operation(args.operation_id, environ=environ,
                                      transport=transport, development_root=development_root)
        elif args.command == "fetch":
            result = fetch_operation(args.operation_id, environ=environ,
                                     transport=transport, development_root=development_root)
        else:
            raise CloudAnimationError("COMMAND_UNSUPPORTED", f"unsupported command: {args.command}")
    except CloudAnimationError as error:
        print(json.dumps({"schema": ERROR_SCHEMA, "status": "REFUSED_OR_FAILED",
                          "code": error.code, "message": error.message}, ensure_ascii=False), file=sys.stderr)
        return 2
    except (OSError, ValueError) as error:
        print(json.dumps({"schema": ERROR_SCHEMA, "status": "FAILED",
                          "code": "LOCAL_OPERATION_FAILED", "message": "local operation failed; inspect paths and operation receipts"}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
