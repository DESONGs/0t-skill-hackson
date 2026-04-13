from __future__ import annotations

import json
import mimetypes
import os
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from ot_skill_enterprise.control_plane.api import build_control_plane_api
from ot_skill_enterprise.style_distillation import build_wallet_style_distillation_service


def _json_safe(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def frontend_root() -> Path:
    return project_root() / "frontend"


def default_workspace_dir() -> Path:
    workspace = os.getenv("OT_FRONTEND_WORKSPACE") or os.getenv("OT_DEFAULT_WORKSPACE") or ".ot-workspace"
    return (project_root() / workspace).resolve()


def _workspace_scan_depth() -> int:
    raw = (os.getenv("OT_WORKSPACE_SCAN_DEPTH") or "1").strip()
    try:
        return max(1, int(raw))
    except ValueError:
        return 1


def _allowed_workspace_paths() -> list[Path]:
    raw = (os.getenv("OT_ALLOWED_WORKSPACES") or "").strip()
    if not raw:
        return []
    allowed: list[Path] = []
    root = project_root()
    for item in raw.split(","):
        value = item.strip()
        if not value:
            continue
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = (root / path).resolve()
        else:
            path = path.resolve()
        allowed.append(path)
    return allowed


def _is_workspace_dir(path: Path) -> bool:
    if not path.exists() or not path.is_dir():
        return False
    return path.name == ".ot-workspace" or (path / "evolution-registry").is_dir() or (path / "run-store").is_dir()


def _is_path_allowed(path: Path) -> bool:
    resolved = path.expanduser().resolve()
    allowed = _allowed_workspace_paths()
    if allowed:
        return any(resolved == item for item in allowed)
    root = project_root().resolve()
    return resolved == root or root in resolved.parents


def _workspace_id(path: Path) -> str:
    root = project_root().resolve()
    try:
        return str(path.resolve().relative_to(root))
    except ValueError:
        return str(path.resolve())


def _workspace_name(path: Path) -> str:
    workspace_id = _workspace_id(path)
    display_suffix = workspace_id
    if workspace_id.startswith("/"):
        parts = Path(workspace_id).parts
        display_suffix = "/".join(parts[-2:]) if len(parts) >= 2 else workspace_id
    return f"{path.name} ({display_suffix})"


def discover_workspaces() -> dict[str, Any]:
    try:
        candidates: list[Path] = []
        allowed = _allowed_workspace_paths()
        if allowed:
            candidates = [path for path in allowed if _is_workspace_dir(path)]
        else:
            root = project_root().resolve()
            max_depth = _workspace_scan_depth()
            queue: list[tuple[Path, int]] = [(root, 0)]
            while queue:
                current, depth = queue.pop(0)
                try:
                    entries = sorted(
                        [entry for entry in current.iterdir() if entry.is_dir()],
                        key=lambda entry: entry.name,
                    )
                except OSError:
                    continue
                for entry in entries:
                    entry_depth = depth + 1
                    if entry_depth <= max_depth and _is_workspace_dir(entry):
                        candidates.append(entry.resolve())
                    if entry_depth < max_depth:
                        queue.append((entry, entry_depth))
            default_workspace = default_workspace_dir()
            if _is_workspace_dir(default_workspace):
                candidates.append(default_workspace.resolve())
        unique_items: list[dict[str, str]] = []
        seen: set[str] = set()
        for path in candidates:
            resolved = path.resolve()
            if not _is_path_allowed(resolved) or not _is_workspace_dir(resolved):
                continue
            key = str(resolved)
            if key in seen:
                continue
            seen.add(key)
            unique_items.append(
                {
                    "id": _workspace_id(resolved),
                    "name": _workspace_name(resolved),
                    "path": str(resolved),
                }
            )
        unique_items.sort(key=lambda item: item["id"])
        return {"items": unique_items, "count": len(unique_items)}
    except Exception:
        return {"items": [], "count": 0}


def resolve_workspace_id(value: str | None) -> Path | None:
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    root = project_root().resolve()
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = (root / candidate).resolve()
    else:
        candidate = candidate.resolve()
    if not _is_path_allowed(candidate):
        raise ValueError(f"workspace_dir is not allowed: {raw}")
    if not _is_workspace_dir(candidate):
        raise ValueError(f"workspace_dir is not a valid workspace: {raw}")
    return candidate


def _runtime_api(workspace_dir: Path | None = None):
    workspace = workspace_dir or default_workspace_dir()
    return build_control_plane_api(workspace_dir=workspace)


def _style_service(workspace_dir: Path | None = None):
    workspace = workspace_dir or default_workspace_dir()
    return build_wallet_style_distillation_service(workspace_root=workspace)


def build_overview_payload(workspace_dir: Path | None = None) -> dict[str, Any]:
    api = _runtime_api(workspace_dir)
    style_distillations = build_style_distillation_payload(workspace_dir)
    runtimes = api.runtimes()
    runtime = api.runtime()
    sessions = api.sessions()
    active_runs = api.active_runs()
    evaluations = api.evaluations()
    candidates = api.candidates()
    promotions = api.promotions()
    candidate_surface = api.candidate_overview()
    evolution = api.evolution()
    workspace = Path(runtime["workspace_root"])
    return {
        "project": {
            "name": "0T Skill Enterprise",
            "tagline": "Runtime dashboard for runtime, sessions, candidates, and promotions.",
        },
        "dashboard": {
            "title": "Runtime Dashboard",
            "mode": runtime["mode"],
            "summary": "只读 runtime 控制面，聚焦注册运行时、会话、候选、晋升，以及地址风格蒸馏里的 Pi reflection lineage。",
        },
        "runtimes": _json_safe(runtimes),
        "runtime": _json_safe(runtime),
        "sessions": _json_safe(sessions),
        "active_runs": _json_safe(active_runs),
        "evaluations": _json_safe(evaluations),
        "candidates": _json_safe(candidates),
        "promotions": _json_safe(promotions),
        "candidate_surface": _json_safe(candidate_surface),
        "evolution": _json_safe(evolution),
        "style_distillations": _json_safe(style_distillations),
        "commands": [
            "ot-enterprise runtime list",
            "ot-enterprise runtime overview",
            "ot-enterprise runtime start --runtime pi",
            "ot-enterprise runtime run --runtime pi --prompt 'inspect repository'",
            "ot-enterprise runtime sessions",
            "ot-enterprise runtime active-runs",
            "ot-enterprise runtime record-run --payload-file <run.json>",
            "ot-enterprise candidate list",
            "ot-enterprise candidate compile --payload-file <candidate.json>",
            "ot-enterprise candidate validate --candidate-id <id>",
            "ot-enterprise candidate promote --candidate-id <id>",
            "ot-enterprise style list",
            "ot-enterprise style distill --wallet 0xabc --chain solana",
        ],
        "runtime_context": {
            "workspace_dir": str(workspace),
            "frontend_mode": "runtime-dashboard",
            "frontend_url_hint": f"http://{os.getenv('OT_FRONTEND_BIND_HOST', '127.0.0.1')}:{os.getenv('OT_FRONTEND_PORT', '8090')}",
            "active_run_count": runtime["active_run_count"],
            "session_count": runtime["session_count"],
            "candidate_count": candidate_surface["candidate_count"],
            "promotion_count": candidate_surface["promotion_count"],
            "style_distillation_count": style_distillations["count"],
        },
    }


def build_runtime_payload(workspace_dir: Path | None = None) -> dict[str, Any]:
    return build_overview_payload(workspace_dir)["runtime"]


def build_runtimes_payload(workspace_dir: Path | None = None) -> dict[str, Any]:
    return build_overview_payload(workspace_dir)["runtimes"]


def build_sessions_payload(workspace_dir: Path | None = None) -> dict[str, Any]:
    return build_overview_payload(workspace_dir)["sessions"]


def build_active_runs_payload(workspace_dir: Path | None = None) -> dict[str, Any]:
    return build_overview_payload(workspace_dir)["active_runs"]


def build_evolution_payload(workspace_dir: Path | None = None) -> dict[str, Any]:
    return build_overview_payload(workspace_dir)["evolution"]


def build_evaluations_payload(workspace_dir: Path | None = None) -> dict[str, Any]:
    return build_overview_payload(workspace_dir)["evaluations"]


def build_candidates_payload(workspace_dir: Path | None = None) -> dict[str, Any]:
    return build_overview_payload(workspace_dir)["candidates"]


def build_promotions_payload(workspace_dir: Path | None = None) -> dict[str, Any]:
    return build_overview_payload(workspace_dir)["promotions"]


def build_candidate_surface_payload(workspace_dir: Path | None = None) -> dict[str, Any]:
    return build_overview_payload(workspace_dir)["candidate_surface"]


def build_style_distillation_payload(workspace_dir: Path | None = None) -> dict[str, Any]:
    service = _style_service(workspace_dir)
    return service.list_jobs(limit=12)


def _workspace_from_query(query: dict[str, list[str]]) -> Path | None:
    if not query.get("workspace_dir"):
        return None
    return resolve_workspace_id(query["workspace_dir"][0])


def _read_json_if_exists(path: Path) -> dict[str, Any] | None:
    if not path.exists() or not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


@dataclass
class StaticAsset:
    path: Path
    content_type: str


def resolve_static_asset(request_path: str) -> StaticAsset | None:
    clean_path = request_path.lstrip("/") or "index.html"
    target = (frontend_root() / clean_path).resolve()
    if frontend_root() not in target.parents and target != frontend_root():
        return None
    if target.is_dir():
        target = target / "index.html"
    if not target.exists() or not target.is_file():
        return None
    content_type = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
    return StaticAsset(path=target, content_type=content_type)


class FrontendRequestHandler(BaseHTTPRequestHandler):
    server_version = "0TSkillEnterpriseFrontend/0.2"

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        return

    def _send_json(self, status_code: int, payload: Any) -> None:
        data = json.dumps(payload, ensure_ascii=False, indent=2, default=str).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_file(self, asset: StaticAsset) -> None:
        data = asset.path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", asset.content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _read_json_body(self) -> dict[str, Any]:
        content_length = int(self.headers.get("Content-Length", "0"))
        if content_length <= 0:
            return {}
        raw = self.rfile.read(content_length)
        if not raw:
            return {}
        payload = json.loads(raw.decode("utf-8"))
        return payload if isinstance(payload, dict) else {}

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        try:
            workspace_dir = _workspace_from_query(query)
        except ValueError as exc:
            self._send_json(400, {"error": "invalid_workspace", "detail": str(exc)})
            return

        if parsed.path == "/api/workspaces":
            self._send_json(200, discover_workspaces())
            return

        if parsed.path == "/api/overview":
            self._send_json(200, build_overview_payload(workspace_dir))
            return
        if parsed.path == "/api/runtime":
            self._send_json(200, build_runtime_payload(workspace_dir))
            return
        if parsed.path == "/api/runtimes":
            self._send_json(200, build_runtimes_payload(workspace_dir))
            return
        if parsed.path == "/api/sessions":
            self._send_json(200, build_sessions_payload(workspace_dir))
            return
        if parsed.path == "/api/active-runs":
            self._send_json(200, build_active_runs_payload(workspace_dir))
            return
        if parsed.path == "/api/evaluations":
            self._send_json(200, build_evaluations_payload(workspace_dir))
            return
        if parsed.path == "/api/candidates":
            self._send_json(200, build_candidates_payload(workspace_dir))
            return
        if parsed.path == "/api/promotions":
            self._send_json(200, build_promotions_payload(workspace_dir))
            return
        if parsed.path == "/api/evolution":
            self._send_json(200, build_evolution_payload(workspace_dir))
            return
        if parsed.path == "/api/candidate-surface":
            self._send_json(200, build_candidate_surface_payload(workspace_dir))
            return
        if parsed.path == "/api/style-distillations":
            self._send_json(200, build_style_distillation_payload(workspace_dir))
            return
        if parsed.path == "/api/evaluations":
            self._send_json(200, build_evaluations_payload(workspace_dir))
            return
        if parsed.path == "/api/candidates":
            self._send_json(200, build_candidates_payload(workspace_dir))
            return
        if parsed.path == "/api/promotions":
            self._send_json(200, build_promotions_payload(workspace_dir))
            return

        asset = resolve_static_asset(parsed.path)
        if asset is None:
            asset = resolve_static_asset("/index.html")
        if asset is None:
            self._send_json(404, {"error": "frontend asset not found"})
            return
        self._send_file(asset)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        try:
            workspace_dir = _workspace_from_query(query)
        except ValueError as exc:
            self._send_json(400, {"error": "invalid_workspace", "detail": str(exc)})
            return

        try:
            body = self._read_json_body()
        except json.JSONDecodeError:
            self._send_json(400, {"error": "invalid_json"})
            return

        if parsed.path == "/api/style-distillations":
            try:
                result = _style_service(workspace_dir).distill_wallet_style(
                    wallet=str(body.get("wallet") or ""),
                    chain=body.get("chain"),
                    skill_name=body.get("skill_name"),
                    extractor_prompt=body.get("extractor_prompt"),
                )
            except ValueError as exc:
                self._send_json(400, {"error": "invalid_request", "detail": str(exc)})
                return
            except Exception as exc:  # noqa: BLE001
                self._send_json(500, {"error": "style_distillation_failed", "detail": str(exc)})
                return
            self._send_json(200, result)
            return

        self._send_json(404, {"error": "unknown_route"})


def main() -> int:
    host = os.getenv("OT_FRONTEND_BIND_HOST", "127.0.0.1")
    port = int(os.getenv("OT_FRONTEND_PORT", "8090"))
    server = ThreadingHTTPServer((host, port), FrontendRequestHandler)
    print(f"0T frontend running at http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
