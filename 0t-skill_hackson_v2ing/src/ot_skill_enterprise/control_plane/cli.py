from __future__ import annotations

import argparse
import json
from pathlib import Path


def _load_json_payload(inline_payload: str | None, payload_file: str | None) -> dict:
    if inline_payload and payload_file:
        raise SystemExit("only one of --payload or --payload-file may be provided")
    if payload_file:
        return json.loads(Path(payload_file).expanduser().read_text(encoding="utf-8"))
    if inline_payload:
        return json.loads(inline_payload)
    return {}


def _candidate_reference(candidate_id: str | None, payload: dict) -> str | dict:
    if not payload and candidate_id:
        return candidate_id
    if candidate_id and set(payload.keys()) <= {"candidate_id"}:
        return candidate_id
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ot-enterprise", description="0T runtime control plane entrypoint")
    subparsers = parser.add_subparsers(dest="command", required=True)

    runtime = subparsers.add_parser("runtime", help="Inspect runtime, sessions, and active runs")
    runtime_subparsers = runtime.add_subparsers(dest="runtime_command", required=True)

    list_runtimes = runtime_subparsers.add_parser("list", help="List registered runtimes")
    list_runtimes.add_argument("--workspace-dir", default=".ot-workspace")

    overview = runtime_subparsers.add_parser("overview", help="Print the runtime overview")
    overview.add_argument("--workspace-dir", default=".ot-workspace")
    overview.add_argument("--section", default=None, choices=("runtime", "runtimes", "sessions", "active_runs", "evaluations", "candidates", "promotions"))

    sessions = runtime_subparsers.add_parser("sessions", help="Print derived runtime sessions")
    sessions.add_argument("--workspace-dir", default=".ot-workspace")

    active_runs = runtime_subparsers.add_parser("active-runs", help="Print active runtime runs")
    active_runs.add_argument("--workspace-dir", default=".ot-workspace")

    start = runtime_subparsers.add_parser("start", help="Start a runtime session")
    start.add_argument("--workspace-dir", default=".ot-workspace")
    start.add_argument("--runtime", default="pi")
    start.add_argument("--session-id", default=None)
    start.add_argument("--cwd", default=None)
    start.add_argument("--input", default=None, help="Inline JSON payload for session inputs")

    run = runtime_subparsers.add_parser("run", help="Execute a runtime turn")
    run.add_argument("--workspace-dir", default=".ot-workspace")
    run.add_argument("--runtime", default="pi")
    run.add_argument("--session-id", default=None)
    run.add_argument("--cwd", default=None)
    run.add_argument("--prompt", "--input", dest="prompt", required=True, help="Prompt or task text for the runtime")
    run.add_argument("--payload", default=None, help="Inline JSON payload merged into the runtime input payload")
    run.add_argument("--full", action="store_true", help="Return the full transcript and pipeline payloads")

    record_run = runtime_subparsers.add_parser("record-run", help="Record an external agent run into the runtime store")
    record_run.add_argument("--workspace-dir", default=".ot-workspace")
    record_run.add_argument("--payload", default=None, help="Inline JSON payload describing the run")
    record_run.add_argument("--payload-file", default=None, help="Path to a JSON file describing the run")
    record_run.add_argument("--full", action="store_true", help="Return the full normalized run payload")

    candidate = subparsers.add_parser("candidate", help="Inspect and advance v3 candidates")
    candidate_subparsers = candidate.add_subparsers(dest="candidate_command", required=True)

    candidate_list = candidate_subparsers.add_parser("list", help="List candidate records")
    candidate_list.add_argument("--workspace-dir", default=".ot-workspace")

    candidate_overview = candidate_subparsers.add_parser("overview", help="Print candidate surface overview")
    candidate_overview.add_argument("--workspace-dir", default=".ot-workspace")

    candidate_compile = candidate_subparsers.add_parser("compile", help="Compile a candidate into a standard skill package")
    candidate_compile.add_argument("--workspace-dir", default=".ot-workspace")
    candidate_compile.add_argument("--candidate-id", default=None)
    candidate_compile.add_argument("--payload", default=None, help="Inline JSON payload describing the candidate")
    candidate_compile.add_argument("--payload-file", default=None, help="Path to a JSON payload describing the candidate")
    candidate_compile.add_argument("--package-kind", default=None, choices=("prompt", "script", "provider-adapter"))
    candidate_compile.add_argument("--output-root", default=None)
    candidate_compile.add_argument("--force", action="store_true", default=True)

    candidate_validate = candidate_subparsers.add_parser("validate", help="Validate a compiled candidate package")
    candidate_validate.add_argument("--workspace-dir", default=".ot-workspace")
    candidate_validate.add_argument("--candidate-id", default=None)
    candidate_validate.add_argument("--payload", default=None, help="Inline JSON payload describing the candidate")
    candidate_validate.add_argument("--payload-file", default=None, help="Path to a JSON payload describing the candidate")
    candidate_validate.add_argument("--package-root", default=None)
    candidate_validate.add_argument("--action-id", default=None)

    candidate_promote = candidate_subparsers.add_parser("promote", help="Promote a validated candidate into registry records")
    candidate_promote.add_argument("--workspace-dir", default=".ot-workspace")
    candidate_promote.add_argument("--candidate-id", default=None)
    candidate_promote.add_argument("--payload", default=None, help="Inline JSON payload describing the candidate")
    candidate_promote.add_argument("--payload-file", default=None, help="Path to a JSON payload describing the candidate")
    candidate_promote.add_argument("--package-root", default=None)
    candidate_promote.add_argument("--package-kind", default=None, choices=("prompt", "script", "provider-adapter"))
    candidate_promote.add_argument("--action-id", default=None)
    candidate_promote.add_argument("--force", action="store_true", default=True)

    style = subparsers.add_parser("style", help="Distill wallet styles into reusable local skills")
    style_subparsers = style.add_subparsers(dest="style_command", required=True)

    style_list = style_subparsers.add_parser("list", help="List recent wallet style distillation jobs")
    style_list.add_argument("--workspace-dir", default=".ot-workspace")
    style_list.add_argument("--limit", type=int, default=20)

    style_get = style_subparsers.add_parser("get", help="Get a wallet style distillation job by id")
    style_get.add_argument("--workspace-dir", default=".ot-workspace")
    style_get.add_argument("--job-id", required=True)

    style_distill = style_subparsers.add_parser("distill", help="Distill a target wallet into a local style skill")
    style_distill.add_argument("--workspace-dir", default=".ot-workspace")
    style_distill.add_argument("--wallet", required=True)
    style_distill.add_argument("--chain", default=None)
    style_distill.add_argument("--skill-name", default=None)
    style_distill.add_argument("--extractor-prompt", default=None)
    style_distill.add_argument("--live-execute", action="store_true", help="Run execution live after build using the promoted skill")
    style_distill.add_argument("--approval-granted", action="store_true", help="Explicitly authorize live execution when --live-execute is set")

    style_resume = style_subparsers.add_parser("resume", help="Resume a staged wallet style distillation job")
    style_resume.add_argument("--workspace-dir", default=".ot-workspace")
    style_resume.add_argument("--job-id", required=True)
    style_resume.add_argument("--live-execute", action="store_true", help="Run execution live while resuming the job")
    style_resume.add_argument("--approval-granted", action="store_true", help="Explicitly authorize live execution when --live-execute is set")

    return parser


def main(argv: list[str] | None = None) -> int:
    from ot_skill_enterprise.env_bootstrap import load_local_env

    load_local_env()
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "runtime":
        from ot_skill_enterprise.control_plane.api import build_control_plane_api
        from ot_skill_enterprise.runs.pipeline import RunIngestionPipeline
        from ot_skill_enterprise.runtime.service import build_runtime_service

        workspace_dir = Path(getattr(args, "workspace_dir", ".ot-workspace")).expanduser().resolve()
        api = build_control_plane_api(workspace_dir=workspace_dir)
        runtime_service = build_runtime_service(workspace_dir=workspace_dir)
        if args.runtime_command == "list":
            print(json.dumps(runtime_service.list_runtimes(), ensure_ascii=False, indent=2, default=str))
            return 0
        if args.runtime_command == "overview":
            payload = api.overview()
            if args.section:
                payload = payload["sections"][args.section]
            print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
            return 0
        if args.runtime_command == "sessions":
            print(json.dumps(api.sessions(), ensure_ascii=False, indent=2, default=str))
            return 0
        if args.runtime_command == "active-runs":
            print(json.dumps(api.active_runs(), ensure_ascii=False, indent=2, default=str))
            return 0
        if args.runtime_command == "start":
            input_payload = json.loads(args.input) if args.input else {}
            payload = runtime_service.start_session(
                runtime_id=args.runtime,
                session_id=args.session_id,
                cwd=args.cwd,
                inputs=input_payload,
                metadata={"source": "cli.start"},
            )
            print(json.dumps(payload.model_dump(mode="json"), ensure_ascii=False, indent=2, default=str))
            return 0
        if args.runtime_command == "run":
            input_payload = json.loads(args.payload) if args.payload else {}
            result = runtime_service.run(
                runtime_id=args.runtime,
                session_id=args.session_id,
                cwd=args.cwd,
                prompt=args.prompt,
                input_payload=input_payload,
                metadata={"source": "cli.run"},
            )
            print(json.dumps(result.as_dict(full=args.full), ensure_ascii=False, indent=2, default=str))
            return 0
        if args.runtime_command == "record-run":
            payload = _load_json_payload(args.payload, args.payload_file)
            try:
                result = RunIngestionPipeline(registry_root=workspace_dir / "evolution-registry").record(payload)
            except ValueError as exc:
                raise SystemExit(f"validation error: {exc}") from exc
            output = result.as_dict() if args.full else result.summary_dict()
            print(json.dumps(output, ensure_ascii=False, indent=2, default=str))
            return 0

    if args.command == "candidate":
        from ot_skill_enterprise.control_plane.candidates import build_candidate_surface_service
        from ot_skill_enterprise.control_plane.api import build_control_plane_api

        workspace_dir = Path(getattr(args, "workspace_dir", ".ot-workspace")).expanduser().resolve()
        service = build_candidate_surface_service(workspace_root=workspace_dir)
        api = build_control_plane_api(workspace_dir=workspace_dir)
        if args.candidate_command == "list":
            print(json.dumps(api.candidates(), ensure_ascii=False, indent=2, default=str))
            return 0
        if args.candidate_command == "overview":
            print(json.dumps(api.candidate_overview(), ensure_ascii=False, indent=2, default=str))
            return 0
        if args.candidate_command == "compile":
            payload = _load_json_payload(args.payload, args.payload_file)
            if args.candidate_id:
                payload.setdefault("candidate_id", args.candidate_id)
            if not payload and not args.candidate_id:
                raise SystemExit("compile error: provide --candidate-id, --payload, or --payload-file")
            candidate_ref = _candidate_reference(args.candidate_id, payload)
            result = service.compile_candidate(
                candidate_ref,
                output_root=Path(args.output_root).expanduser().resolve() if args.output_root else None,
                package_kind=args.package_kind,
                force=bool(args.force),
            )
            print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
            return 0
        if args.candidate_command == "validate":
            payload = _load_json_payload(args.payload, args.payload_file)
            if args.candidate_id:
                payload.setdefault("candidate_id", args.candidate_id)
            if not payload and not args.candidate_id:
                raise SystemExit("validation error: provide --candidate-id, --payload, or --payload-file")
            candidate_ref = _candidate_reference(args.candidate_id, payload)
            result = service.validate_candidate(
                candidate_ref,
                package_root=Path(args.package_root).expanduser().resolve() if args.package_root else None,
                action_id=args.action_id,
            )
            print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
            return 0
        if args.candidate_command == "promote":
            payload = _load_json_payload(args.payload, args.payload_file)
            if args.candidate_id:
                payload.setdefault("candidate_id", args.candidate_id)
            if not payload and not args.candidate_id:
                raise SystemExit("promotion error: provide --candidate-id, --payload, or --payload-file")
            candidate_ref = _candidate_reference(args.candidate_id, payload)
            result = service.promote_candidate(
                candidate_ref,
                package_root=Path(args.package_root).expanduser().resolve() if args.package_root else None,
                package_kind=args.package_kind,
                force=bool(args.force),
                action_id=args.action_id,
            )
            print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
            return 0

    if args.command == "style":
        from ot_skill_enterprise.style_distillation import build_wallet_style_distillation_service

        workspace_dir = Path(getattr(args, "workspace_dir", ".ot-workspace")).expanduser().resolve()
        service = build_wallet_style_distillation_service(workspace_root=workspace_dir)
        if args.style_command == "list":
            print(json.dumps(service.list_jobs(limit=args.limit), ensure_ascii=False, indent=2, default=str))
            return 0
        if args.style_command == "get":
            print(json.dumps(service.get_job(args.job_id), ensure_ascii=False, indent=2, default=str))
            return 0
        if args.style_command == "distill":
            result = service.distill_wallet_style(
                wallet=args.wallet,
                chain=args.chain,
                skill_name=args.skill_name,
                extractor_prompt=args.extractor_prompt,
                live_execute=bool(args.live_execute),
                approval_granted=bool(args.approval_granted),
            )
            print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
            return 0
        if args.style_command == "resume":
            result = service.resume_job(
                args.job_id,
                live_execute=bool(args.live_execute),
                approval_granted=bool(args.approval_granted),
            )
            print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
            return 0

    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
