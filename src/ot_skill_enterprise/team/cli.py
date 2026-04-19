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


def configure_parser(
    parser: argparse.ArgumentParser,
    *,
    command_dest: str = "command",
) -> argparse.ArgumentParser:
    parser.prog = "0t team"
    parser.description = "0T team orchestration entrypoint for skill optimization workflows"
    parser.add_argument("--workspace-dir", default=".ot-workspace")
    subparsers = parser.add_subparsers(dest=command_dest, required=True)

    subparsers.add_parser("doctor", help="Inspect team protocol readiness and adapter support")

    start = subparsers.add_parser("start", help="Start an optimization workflow session")
    start.add_argument("workflow", help="Workflow id, for example autoresearch")
    start.add_argument("--workspace", dest="workspace_id", required=True, help="Logical workspace identifier")
    start.add_argument("--skill", required=True, help="Skill slug or path to optimize")
    start.add_argument("--adapter", default="codex")
    start.add_argument("--data-source-adapter", dest="data_source_adapter_id", default=None)
    start.add_argument("--execution-adapter", dest="execution_adapter_id", default=None)
    start.add_argument("--objective", default=None)
    start.add_argument("--session-id", default=None)

    status = subparsers.add_parser("status", help="Inspect a session")
    status.add_argument("session_id")

    leaderboard = subparsers.add_parser("leaderboard", help="Show the current leaderboard")
    leaderboard.add_argument("session_id")

    review = subparsers.add_parser("review", help="Refresh recommendation state for a session")
    review.add_argument("session_id")

    approve = subparsers.add_parser("approve", help="Approve a recommended variant")
    approve.add_argument("session_id")
    approve.add_argument("--variant", required=True)
    approve.add_argument("--approved-by", default="human")
    approve.add_argument("--activate", action="store_true")

    archive = subparsers.add_parser("archive", help="Archive a session")
    archive.add_argument("session_id")

    handoff = subparsers.add_parser("handoff", help="Generate a role-specific handoff for Codex or Claude Code")
    handoff.add_argument("--session-id", required=True)
    handoff.add_argument("--role", required=True)
    handoff.add_argument("--adapter", default=None)

    work_items = subparsers.add_parser("work-items", help="List work items for a session")
    work_items.add_argument("session_id")

    submit = subparsers.add_parser("submit-work", help="Submit a work item result back into the team session")
    submit.add_argument("--session-id", required=True)
    submit.add_argument("--work-item-id", default=None)
    submit.add_argument("--role", default=None)
    submit.add_argument("--agent-id", default=None)
    submit.add_argument("--payload", default=None)
    submit.add_argument("--payload-file", default=None)

    return parser


def build_parser() -> argparse.ArgumentParser:
    return configure_parser(argparse.ArgumentParser())


def run_from_namespace(
    args: argparse.Namespace,
    *,
    command_attr: str = "command",
) -> int:
    from ot_skill_enterprise.env_bootstrap import load_local_env
    from ot_skill_enterprise.team.service import build_agent_team_service

    load_local_env()
    service = build_agent_team_service(workspace_root=Path(args.workspace_dir).expanduser().resolve())
    command = getattr(args, command_attr)

    if command == "doctor":
        print(json.dumps(service.doctor(), ensure_ascii=False, indent=2, default=str))
        return 0
    if command == "start":
        payload = service.start_session(
            args.workflow,
            workspace_id=args.workspace_id,
            skill_ref=args.skill,
            adapter_family=args.adapter,
            objective=args.objective,
            session_id=args.session_id,
            data_source_adapter_id=args.data_source_adapter_id,
            execution_adapter_id=args.execution_adapter_id,
        )
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        return 0
    if command == "status":
        print(json.dumps(service.status(args.session_id), ensure_ascii=False, indent=2, default=str))
        return 0
    if command == "leaderboard":
        print(json.dumps(service.leaderboard(args.session_id), ensure_ascii=False, indent=2, default=str))
        return 0
    if command == "review":
        print(json.dumps(service.review(args.session_id), ensure_ascii=False, indent=2, default=str))
        return 0
    if command == "approve":
        print(
            json.dumps(
                service.approve(
                    args.session_id,
                    variant_id=args.variant,
                    approved_by=args.approved_by,
                    activate=bool(args.activate),
                ),
                ensure_ascii=False,
                indent=2,
                default=str,
            )
        )
        return 0
    if command == "archive":
        print(json.dumps(service.archive(args.session_id), ensure_ascii=False, indent=2, default=str))
        return 0
    if command == "handoff":
        print(
            json.dumps(
                service.handoff(args.session_id, role_id=args.role, adapter_family=args.adapter),
                ensure_ascii=False,
                indent=2,
                default=str,
            )
        )
        return 0
    if command == "work-items":
        payload = service.status(args.session_id)
        print(json.dumps({"session_id": args.session_id, "work_items": payload["work_items"]}, ensure_ascii=False, indent=2, default=str))
        return 0
    if command == "submit-work":
        payload = _load_json_payload(args.payload, args.payload_file)
        print(
            json.dumps(
                service.submit_work(
                    args.session_id,
                    payload=payload,
                    work_item_id=args.work_item_id,
                    role_id=args.role,
                    agent_id=args.agent_id,
                ),
                ensure_ascii=False,
                indent=2,
                default=str,
            )
        )
        return 0

    raise SystemExit(f"unknown team command: {command}")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return run_from_namespace(args)


if __name__ == "__main__":
    raise SystemExit(main())
