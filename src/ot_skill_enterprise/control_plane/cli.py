from __future__ import annotations

import argparse
import json
from pathlib import Path


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def _mapping(value: object) -> dict:
    return dict(value) if isinstance(value, dict) else {}


def _string(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


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


def _workspace_config_path(workspace_root: Path, workspace_id: str | None) -> Path | None:
    slug = _string(workspace_id)
    if not slug:
        return None
    return workspace_root / "workspaces" / slug / "workflow-config.json"


def _workspace_adapter_config(workspace_root: Path, workspace_id: str | None) -> dict[str, str]:
    path = _workspace_config_path(workspace_root, workspace_id)
    if path is None:
        return {}
    payload = _read_json(path)
    candidates = [
        payload,
        _mapping(payload.get("adapters")),
        _mapping(payload.get("adapter_ids")),
        _mapping(payload.get("workflow")),
        _mapping(_mapping(payload.get("workflow")).get("adapter_ids")),
        _mapping(_mapping(payload.get("workflow")).get("adapters")),
        _mapping(payload.get("nextgen")),
        _mapping(_mapping(payload.get("nextgen")).get("adapter_ids")),
        _mapping(_mapping(payload.get("nextgen")).get("adapters")),
    ]
    resolved: dict[str, str] = {}
    for candidate in candidates:
        if not candidate:
            continue
        data_source = _string(candidate.get("data_source") or candidate.get("data_source_adapter_id"))
        execution = _string(candidate.get("execution") or candidate.get("execution_adapter_id"))
        if data_source and "data_source" not in resolved:
            resolved["data_source"] = data_source
        if execution and "execution" not in resolved:
            resolved["execution"] = execution
    return resolved


def _load_kernel_session_request(workspace_root: Path, session_id: str | None) -> dict:
    if not _string(session_id):
        return {}
    session_path = workspace_root / "runtime-sessions" / str(session_id) / "workflow-kernel" / "session.json"
    return _mapping(_mapping(_read_json(session_path)).get("request"))


def _resolve_workflow_adapters(
    *,
    workspace_root: Path,
    workflow_id: str,
    workspace_id: str | None = None,
    session_id: str | None = None,
    data_source_adapter_id: str | None = None,
    execution_adapter_id: str | None = None,
) -> tuple[str | None, str | None, str | None]:
    session_request = _load_kernel_session_request(workspace_root, session_id)
    resolved_workspace_id = _string(workspace_id) or _string(session_request.get("workspace_id"))
    workspace_adapters = _workspace_adapter_config(workspace_root, resolved_workspace_id)
    resolved_data_source = (
        _string(data_source_adapter_id)
        or _string(session_request.get("data_source_adapter_id"))
        or workspace_adapters.get("data_source")
    )
    resolved_execution = (
        _string(execution_adapter_id)
        or _string(session_request.get("execution_adapter_id"))
        or workspace_adapters.get("execution")
    )
    if not resolved_data_source:
        raise SystemExit(
            "workflow execution requires --data-source-adapter or a workspace workflow-config.json with data_source/data_source_adapter_id"
        )
    if workflow_id in {"autonomous_research", "approval_convergence"} and not resolved_execution:
        raise SystemExit(
            "workflow execution requires --execution-adapter or a workspace workflow-config.json with execution/execution_adapter_id"
        )
    return resolved_workspace_id, resolved_data_source, resolved_execution


def _resolve_cli_workflow_adapters(
    args: argparse.Namespace,
    *,
    workspace_dir: Path,
) -> tuple[str | None, dict[str, str | None]]:
    workflow_command = str(getattr(args, "workflow_command", "") or "").strip().replace("-", "_")
    workspace_id, resolved_data_source, resolved_execution = _resolve_workflow_adapters(
        workspace_root=workspace_dir,
        workflow_id="approval_convergence" if workflow_command == "approval_convergence" else "autonomous_research" if workflow_command == "autonomous_research" else "distillation_seed",
        workspace_id=getattr(args, "workspace_id", None),
        session_id=getattr(args, "session_id", None),
        data_source_adapter_id=getattr(args, "data_source_adapter", None),
        execution_adapter_id=getattr(args, "execution_adapter", None),
    )
    return workspace_id, {
        "data_source": resolved_data_source,
        "execution": resolved_execution,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="0t", description="0T control plane entrypoint")
    subparsers = parser.add_subparsers(dest="command", required=True)

    workflow = subparsers.add_parser("workflow", help="Run next-architecture workflow paths")
    workflow_subparsers = workflow.add_subparsers(dest="workflow_command", required=True)

    workflow_overview = workflow_subparsers.add_parser("overview", help="Print the next-architecture workflow overview")
    workflow_overview.add_argument("--project-root", default=".")
    workflow_overview.add_argument("--workspace-dir", default=".ot-workspace")

    workflow_wallet = workflow_subparsers.add_parser("wallet-style-distillation", help="Run the default wallet-style distillation workflow")
    workflow_wallet.add_argument("--project-root", default=".")
    workflow_wallet.add_argument("--workspace-dir", default=".ot-workspace")
    workflow_wallet.add_argument("--workspace", dest="workspace_id", default=None)
    workflow_wallet.add_argument("--wallet", required=True)
    workflow_wallet.add_argument("--chain", default="bsc")
    workflow_wallet.add_argument("--skill-name", default=None)
    workflow_wallet.add_argument("--data-source-adapter", default=None)
    workflow_wallet.add_argument("--execution-adapter", default=None)

    workflow_seed = workflow_subparsers.add_parser("distillation-seed", help="Run the next-architecture distillation seed workflow")
    workflow_seed.add_argument("--project-root", default=".")
    workflow_seed.add_argument("--workspace-dir", default=".ot-workspace")
    workflow_seed.add_argument("--workspace", dest="workspace_id", default=None)
    workflow_seed.add_argument("--wallet", required=True)
    workflow_seed.add_argument("--chain", default="bsc")
    workflow_seed.add_argument("--skill-name", default=None)
    workflow_seed.add_argument("--data-source-adapter", default=None)
    workflow_seed.add_argument("--execution-adapter", default=None)

    workflow_research = workflow_subparsers.add_parser("autonomous-research", help="Run the composed autonomous research workflow")
    workflow_research.add_argument("--project-root", default=".")
    workflow_research.add_argument("--workspace-dir", default=".ot-workspace")
    workflow_research.add_argument("--workspace", dest="workspace_id", default=None)
    workflow_research.add_argument("--wallet", required=True)
    workflow_research.add_argument("--chain", default="bsc")
    workflow_research.add_argument("--skill-name", default=None)
    workflow_research.add_argument("--objective", default="improve strategy quality while preserving style and execution discipline")
    workflow_research.add_argument("--iteration-budget", type=int, default=1)
    workflow_research.add_argument("--max-variants", type=int, default=2)
    workflow_research.add_argument("--data-source-adapter", default=None)
    workflow_research.add_argument("--execution-adapter", default=None)
    workflow_research.add_argument("--candidate-variants-file", default=None, help="Path to a JSON array of candidate variant payloads")

    workflow_approval = workflow_subparsers.add_parser("approval-convergence", help="Run the approval convergence workflow for an existing session")
    workflow_approval.add_argument("--project-root", default=".")
    workflow_approval.add_argument("--workspace-dir", default=".ot-workspace")
    workflow_approval.add_argument("--workspace", dest="workspace_id", default=None)
    workflow_approval.add_argument("--session-id", required=True)
    workflow_approval.add_argument("--approved-by", default="human")
    workflow_approval.add_argument("--approval-granted", action="store_true")
    workflow_approval.add_argument("--activate", action="store_true")
    workflow_approval.add_argument("--data-source-adapter", default=None)
    workflow_approval.add_argument("--execution-adapter", default=None)

    from ot_skill_enterprise.team.cli import configure_parser as configure_team_parser

    team = subparsers.add_parser("team", help="Run 0T team coordination workflows")
    configure_team_parser(team, command_dest="team_command")

    architecture = subparsers.add_parser("architecture", help="Inspect next-architecture plugin and adapter scaffolding")
    architecture_subparsers = architecture.add_subparsers(dest="architecture_command", required=True)

    architecture_overview = architecture_subparsers.add_parser("overview", help="Print the next-architecture overview")
    architecture_overview.add_argument("--project-root", default=".")

    architecture_plugins = architecture_subparsers.add_parser("plugins", help="Print registered next-architecture workflow plugins")
    architecture_plugins.add_argument("--project-root", default=".")

    architecture_adapters = architecture_subparsers.add_parser("adapters", help="Print registered next-architecture adapters")
    architecture_adapters.add_argument("--project-root", default=".")

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

    prepare = runtime_subparsers.add_parser("prepare", help="Prepare runtime prerequisites for uv-based startup")
    prepare.add_argument("--workspace-dir", default=".ot-workspace")
    prepare.add_argument("--skip-verify", action="store_true", help="Skip pi runtime verification after prepare")

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
    style_distill.add_argument("--data-source-adapter", default=None)
    style_distill.add_argument("--execution-adapter", default=None)
    style_distill.add_argument("--extractor-prompt", default=None)
    style_distill.add_argument("--max-attempts", type=int, default=3, help="Maximum automatic distillation attempts per wallet (clamped to 3)")
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

    if args.command == "team":
        from ot_skill_enterprise.team.cli import run_from_namespace

        return run_from_namespace(args, command_attr="team_command")

    if args.command == "workflow":
        from ot_skill_enterprise.nextgen import (
            WorkflowRunRequest,
            build_next_architecture_service,
            build_nextgen_workflow_service,
        )

        project_root = Path(getattr(args, "project_root", ".")).expanduser().resolve()
        workspace_dir = Path(getattr(args, "workspace_dir", ".ot-workspace")).expanduser().resolve()
        if args.workflow_command == "overview":
            from ot_skill_enterprise.nextgen import build_nextgen_kernel_bridge

            architecture_service = build_next_architecture_service(project_root=project_root)
            workflow_service = build_nextgen_workflow_service(
                project_root=project_root,
                workspace_root=workspace_dir,
            )
            kernel_bridge = build_nextgen_kernel_bridge(
                project_root=project_root,
                workspace_root=workspace_dir,
            )
            print(
                json.dumps(
                    {
                        "status": "ready",
                        "architecture": architecture_service.overview(),
                        "workflow_registry": workflow_service.plugin_registry.describe(),
                        "kernel_launch_plan": kernel_bridge.launch_plan(),
                    },
                    ensure_ascii=False,
                    indent=2,
                    default=str,
                )
            )
            return 0
        workspace_id, resolved_adapters = _resolve_cli_workflow_adapters(args, workspace_dir=workspace_dir)
        workflow_service = build_nextgen_workflow_service(
            project_root=project_root,
            workspace_root=workspace_dir,
            data_source_adapter_id=resolved_adapters["data_source"],
        )

        candidate_variants: list[dict] = []
        candidate_variants_file = getattr(args, "candidate_variants_file", None)
        if candidate_variants_file:
            loaded = json.loads(Path(candidate_variants_file).expanduser().read_text(encoding="utf-8"))
            if not isinstance(loaded, list):
                raise SystemExit("candidate variants file must contain a JSON array")
            candidate_variants = [dict(item) for item in loaded if isinstance(item, dict)]
        if args.workflow_command == "approval-convergence":
            request = WorkflowRunRequest(
                workflow_id="approval_convergence",
                session_id=args.session_id,
                workspace_id=workspace_id,
                workspace_dir=str(workspace_dir),
                data_source_adapter_id=resolved_adapters["data_source"],
                execution_adapter_id=resolved_adapters["execution"],
                metadata={
                    "approval_granted": bool(args.approval_granted),
                    "activation_requested": bool(args.activate),
                    "activate": bool(args.activate),
                    "approved_by": args.approved_by,
                    "workspace_adapters": resolved_adapters,
                },
                operator_hints={
                    "approval_granted": bool(args.approval_granted),
                    "activation_requested": bool(args.activate),
                },
            )
            result = workflow_service.run_approval_convergence(request)
            print(
                json.dumps(
                    {
                        "status": "ready",
                        "result": result.model_dump(mode="json"),
                    },
                    ensure_ascii=False,
                    indent=2,
                    default=str,
                )
            )
            return 0

        request = WorkflowRunRequest(
            workflow_id=(
                "distillation_seed"
                if args.workflow_command in {"distillation-seed", "wallet-style-distillation"}
                else "autonomous_research"
            ),
            workspace_id=workspace_id,
            wallet=args.wallet,
            chain=args.chain,
            skill_name=args.skill_name,
            workspace_dir=str(workspace_dir),
            objective=getattr(args, "objective", "improve strategy quality while preserving style and execution discipline"),
            iteration_budget=int(getattr(args, "iteration_budget", 1)),
            max_variants=int(getattr(args, "max_variants", 2)),
            candidate_variants=candidate_variants,
            data_source_adapter_id=resolved_adapters["data_source"],
            execution_adapter_id=resolved_adapters["execution"],
            metadata={"workspace_adapters": resolved_adapters},
        )
        if args.workflow_command in {"distillation-seed", "wallet-style-distillation"}:
            result = workflow_service.run_distillation_seed(request)
            print(
                json.dumps(
                    {
                        "status": "ready",
                        "result": result.model_dump(mode="json"),
                    },
                    ensure_ascii=False,
                    indent=2,
                    default=str,
                )
            )
            return 0
        if args.workflow_command == "autonomous-research":
            result = workflow_service.run_autonomous_research(request)
            print(
                json.dumps(
                    {
                        "status": "ready",
                        "result": result.model_dump(mode="json"),
                    },
                    ensure_ascii=False,
                    indent=2,
                    default=str,
                )
            )
            return 0

    if args.command == "architecture":
        from ot_skill_enterprise.nextgen import build_next_architecture_service

        project_root = Path(getattr(args, "project_root", ".")).expanduser().resolve()
        service = build_next_architecture_service(project_root=project_root)
        if args.architecture_command == "overview":
            print(json.dumps(service.overview(), ensure_ascii=False, indent=2, default=str))
            return 0
        if args.architecture_command == "plugins":
            print(json.dumps(service.plugins(), ensure_ascii=False, indent=2, default=str))
            return 0
        if args.architecture_command == "adapters":
            print(json.dumps(service.adapters(), ensure_ascii=False, indent=2, default=str))
            return 0

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
        if args.runtime_command == "prepare":
            from ot_skill_enterprise.runtime.prepare import prepare_runtime_environment

            payload = prepare_runtime_environment(
                workspace_dir=workspace_dir,
                verify_pi=not bool(args.skip_verify),
            )
            print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
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
        from ot_skill_enterprise.nextgen.kernel_bridge import configured_workflow_runtime
        from ot_skill_enterprise.nextgen.workflows import WorkflowRunRequest, build_nextgen_workflow_service

        workspace_dir = Path(getattr(args, "workspace_dir", ".ot-workspace")).expanduser().resolve()
        service = build_wallet_style_distillation_service(workspace_root=workspace_dir)
        if args.style_command == "list":
            print(json.dumps(service.list_jobs(limit=args.limit), ensure_ascii=False, indent=2, default=str))
            return 0
        if args.style_command == "get":
            print(json.dumps(service.get_job(args.job_id), ensure_ascii=False, indent=2, default=str))
            return 0
        if args.style_command == "distill":
            workflow_runtime = configured_workflow_runtime()
            if workflow_runtime == "ts-kernel" and not args.live_execute and args.extractor_prompt is None:
                workflow_data_source_adapter_id = getattr(args, "data_source_adapter", None) or "ave"
                workflow_execution_adapter_id = getattr(args, "execution_adapter", None) or "onchainos_cli"
                workflow_service = build_nextgen_workflow_service(
                    project_root=Path(".").expanduser().resolve(),
                    workspace_root=workspace_dir,
                    data_source_adapter_id=workflow_data_source_adapter_id,
                )
                result = workflow_service.run_distillation_seed(
                    WorkflowRunRequest(
                        workflow_id="distillation_seed",
                        wallet=args.wallet,
                        chain=args.chain or "bsc",
                        skill_name=args.skill_name,
                        workspace_dir=str(workspace_dir),
                        data_source_adapter_id=workflow_data_source_adapter_id,
                        execution_adapter_id=workflow_execution_adapter_id,
                        metadata={
                            "entry_command": "style.distill",
                            "max_attempts": int(args.max_attempts or 3),
                        },
                    )
                )
                workflow_payload = dict(result.metadata.get("raw_distillation_result") or {})
                workflow_payload.setdefault("kernel_dispatch", result.metadata.get("kernel_dispatch"))
                print(json.dumps(workflow_payload, ensure_ascii=False, indent=2, default=str))
                return 0
            try:
                result = service.distill_wallet_style(
                    wallet=args.wallet,
                    chain=args.chain,
                    skill_name=args.skill_name,
                    extractor_prompt=args.extractor_prompt,
                    live_execute=bool(args.live_execute),
                    approval_granted=bool(args.approval_granted),
                    max_attempts=int(args.max_attempts or 3),
                )
            except Exception as exc:  # noqa: BLE001
                report = getattr(exc, "report", None)
                if isinstance(report, dict):
                    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
                    return 1
                raise
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
