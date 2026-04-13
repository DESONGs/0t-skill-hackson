from __future__ import annotations

import hashlib
import json
import re
import shutil
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Mapping

import yaml

from ot_skill_enterprise.enterprise_bridge import EnterpriseBridge
from ot_skill_enterprise.enterprise_bridge.paths import ensure_bridge_import_paths
from ot_skill_enterprise.service_locator import project_root as resolve_project_root

from .models import (
    PackageBuildResult,
    PackageValidationResult,
    PromotionRecord,
    SkillCandidate,
)

ensure_bridge_import_paths()
from skill_contract.parsers.package import load_skill_package  # noqa: E402
from skill_contract.validators.package import validate_skill_package as validate_contract_skill_package  # noqa: E402
from skill_contract.validators.package_structure import validate_package_structure  # noqa: E402


SUPPORTED_PACKAGE_KINDS = {"prompt", "script", "provider-adapter"}
ADAPTER_TARGETS = ("generic",)


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(subvalue) for key, subvalue in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, date):
        return value.isoformat()
    return value


def _stable_payload(value: Any) -> str:
    return json.dumps(_json_safe(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", str(value or "").strip().lower()).strip("-")
    return slug or "candidate"


def _short_hash(value: Any, *, length: int = 10) -> str:
    digest = hashlib.sha256(_stable_payload(value).encode("utf-8")).hexdigest()
    return digest[:length]


def _candidate_payload(value: SkillCandidate | Mapping[str, Any]) -> SkillCandidate:
    if isinstance(value, SkillCandidate):
        return value
    return SkillCandidate.from_mapping(value)


def _package_kind(candidate: SkillCandidate, override: str | None = None) -> str:
    kind = str(override or candidate.candidate_type or "prompt").strip().lower()
    if kind not in SUPPORTED_PACKAGE_KINDS:
        return "prompt"
    return kind


def _package_root(project_root: Path, candidate: SkillCandidate, kind: str, output_root: Path | None = None) -> Path:
    if output_root is not None:
        return Path(output_root).expanduser().resolve()
    skill_name = candidate.candidate_slug or f"{_slugify(candidate.target_skill_name)}-{_short_hash(candidate.candidate_id, length=8)}"
    return (project_root / ".ot-workspace" / "candidates" / skill_name).resolve()


def _tree_sha256(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _report_entries(report: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    issues = getattr(report, "issues", None) or []
    failures: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    for issue in issues:
        payload = issue.model_dump(mode="json") if hasattr(issue, "model_dump") else dict(issue)
        if str(payload.get("severity") or "").lower() == "warning":
            warnings.append(payload)
        else:
            failures.append(payload)
    return failures, warnings


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def _write_yaml(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(_json_safe(payload), sort_keys=False, allow_unicode=True), encoding="utf-8")


def _wallet_style_profile(candidate: SkillCandidate) -> dict[str, Any] | None:
    payload = candidate.generation_spec.get("wallet_style_profile")
    if isinstance(payload, Mapping):
        return dict(payload)
    payload = candidate.metadata.get("wallet_style_profile")
    if isinstance(payload, Mapping):
        return dict(payload)
    return None


def _wallet_strategy_spec(candidate: SkillCandidate) -> dict[str, Any] | None:
    payload = candidate.generation_spec.get("strategy_spec")
    if isinstance(payload, Mapping):
        return dict(payload)
    payload = candidate.metadata.get("strategy_spec")
    if isinstance(payload, Mapping):
        return dict(payload)
    return None


def _wallet_execution_intent(candidate: SkillCandidate) -> dict[str, Any] | None:
    payload = candidate.generation_spec.get("execution_intent")
    if isinstance(payload, Mapping):
        return dict(payload)
    payload = candidate.metadata.get("execution_intent")
    if isinstance(payload, Mapping):
        return dict(payload)
    return None


def _wallet_preprocessed(candidate: SkillCandidate) -> dict[str, Any] | None:
    payload = candidate.generation_spec.get("preprocessed_wallet")
    if isinstance(payload, Mapping):
        return dict(payload)
    return None


def _wallet_token_catalog(candidate: SkillCandidate) -> dict[str, dict[str, Any]]:
    catalog: dict[str, dict[str, Any]] = {}
    preprocessed = _wallet_preprocessed(candidate) or {}
    for collection_name in ("focus_tokens", "recent_trade_samples"):
        for entry in list(preprocessed.get(collection_name) or []):
            if not isinstance(entry, Mapping):
                continue
            symbol = str(entry.get("symbol") or "").strip()
            address = str(entry.get("token_address") or "").strip()
            if symbol and address and symbol not in catalog:
                catalog[symbol] = {
                    "symbol": symbol,
                    "token_address": address,
                    "chain": preprocessed.get("chain"),
                }
    return catalog


def _render_skill_md(candidate: SkillCandidate, package_kind: str) -> str:
    package_name = candidate.candidate_slug
    style_profile = _wallet_style_profile(candidate)
    frontmatter = {
        "name": package_name,
        "description": candidate.change_summary,
        "version": "1.0.0",
        "owner": "mainagent",
        "status": "experimental",
        "tags": [
            "generated",
            "candidate",
            package_kind,
            candidate.target_skill_kind,
        ],
        "metadata": {
            "candidate_id": candidate.candidate_id,
            "runtime_session_id": candidate.runtime_session_id,
            "source_run_id": candidate.source_run_id,
            "source_evaluation_id": candidate.source_evaluation_id,
            "target_skill_name": candidate.target_skill_name,
            "target_skill_kind": candidate.target_skill_kind,
            "candidate_type": candidate.candidate_type,
        },
    }
    if style_profile is not None:
        execution_rule_lines = [f"- {rule}" for rule in list(style_profile.get("execution_rules") or [])]
        anti_pattern_lines = [f"- {rule}" for rule in list(style_profile.get("anti_patterns") or [])] or ["- No anti-patterns captured"]
        body_lines = [
            f"# {candidate.target_skill_name}",
            "",
            candidate.change_summary,
            "",
            "## Wallet Style Signature",
            "",
            f"- Wallet: {style_profile.get('wallet') or candidate.metadata.get('wallet_address') or 'unknown'}",
            f"- Chain: {style_profile.get('chain') or candidate.metadata.get('chain') or 'unknown'}",
            f"- Style label: {style_profile.get('style_label') or 'wallet-style'}",
            f"- Execution tempo: {style_profile.get('execution_tempo') or 'unknown'}",
            f"- Risk appetite: {style_profile.get('risk_appetite') or 'unknown'}",
            f"- Conviction profile: {style_profile.get('conviction_profile') or 'unknown'}",
            f"- Stablecoin bias: {style_profile.get('stablecoin_bias') or 'unknown'}",
            "",
            "## Execution Rules",
            "",
            *execution_rule_lines,
            "",
            "## Anti Patterns",
            "",
            *anti_pattern_lines,
            "",
            "## Runtime Notes",
            "",
            "- This package is generated for the hackathon wallet-style distillation flow.",
            "- Promotion copies the package into local skills and makes it discoverable immediately.",
        ]
    else:
        body_lines = [
            f"# {candidate.target_skill_name}",
            "",
            "Generated candidate package for the v3 candidate/promotion surface.",
            "",
            "## Purpose",
            "",
            f"- Candidate type: {package_kind}",
            f"- Source run: {candidate.source_run_id or 'unknown'}",
            f"- Source evaluation: {candidate.source_evaluation_id or 'unknown'}",
            "",
            "## Runtime Notes",
            "",
            "- This package follows the shared skill contract.",
            "- The package can be discovered by the current control-plane bridge after promotion.",
        ]
    return "---\n" + yaml.safe_dump(frontmatter, sort_keys=False, allow_unicode=True).strip() + "\n---\n" + "\n".join(body_lines).strip() + "\n"


def _build_manifest(candidate: SkillCandidate, package_kind: str, package_root: Path) -> dict[str, Any]:
    style_profile = _wallet_style_profile(candidate)
    strategy_spec = _wallet_strategy_spec(candidate)
    execution_intent = _wallet_execution_intent(candidate)
    return {
        "schema_version": "v1",
        "name": package_root.name,
        "description": candidate.change_summary,
        "version": "1.0.0",
        "owner": "mainagent",
        "kind": package_kind,
        "updated_at": date.today().isoformat(),
        "status": "experimental",
        "maturity_tier": "scaffold",
        "review_cadence": "per-release",
        "target_platforms": list(ADAPTER_TARGETS),
        "factory_components": {
            "prompt": ["instructions"],
            "script": ["scripts"],
            "provider-adapter": ["scripts", "adapters"],
        }.get(package_kind, ["scripts"]),
        "risk_level": "low",
        "default_runtime_profile": package_kind,
        "generated_from": {
            "candidate_id": candidate.candidate_id,
            "candidate_slug": candidate.candidate_slug,
            "runtime_session_id": candidate.runtime_session_id,
            "source_run_id": candidate.source_run_id,
            "source_evaluation_id": candidate.source_evaluation_id,
            "target_skill_name": candidate.target_skill_name,
            "target_skill_kind": candidate.target_skill_kind,
            "candidate_type": candidate.candidate_type,
        },
        "metadata": {
            "skill_family": candidate.metadata.get("skill_family"),
            "wallet_style_profile": style_profile,
            "strategy_spec": strategy_spec,
            "execution_intent": execution_intent,
        },
        "package_root": str(package_root),
    }


def _build_actions(candidate: SkillCandidate, package_kind: str) -> dict[str, Any]:
    action_id = "primary"
    is_wallet_style_script = package_kind == "script" and _wallet_style_profile(candidate) is not None
    if package_kind == "prompt":
        return {
            "schema_version": "actions.v1",
            "skill": candidate.candidate_slug,
            "default_action": action_id,
            "actions": [
                {
                    "id": action_id,
                    "title": "Primary Prompt",
                    "kind": "instruction",
                    "entry": "instructions/primary.md",
                    "timeout_sec": 300,
                    "sandbox": "read-only",
                    "allow_network": False,
                    "default": True,
                    "side_effects": [],
                    "idempotency": "exact",
                }
            ],
        }
    if package_kind == "provider-adapter":
        return {
            "schema_version": "actions.v1",
            "skill": candidate.candidate_slug,
            "default_action": action_id,
            "actions": [
                {
                    "id": action_id,
                    "title": "Provider Adapter Bridge",
                    "kind": "script",
                    "entry": "scripts/primary.py",
                    "runtime": "python3",
                    "timeout_sec": 300,
                    "sandbox": "workspace-write",
                    "allow_network": False,
                    "default": True,
                    "side_effects": ["workspace"],
                    "idempotency": "best_effort",
                }
            ],
        }
    return {
        "schema_version": "actions.v1",
        "skill": candidate.candidate_slug,
        "default_action": action_id,
        "actions": (
            [
                {
                    "id": action_id,
                    "title": "Primary Script",
                    "kind": "script",
                    "entry": "scripts/primary.py",
                    "runtime": "python3",
                    "timeout_sec": 300,
                    "sandbox": "workspace-write",
                    "allow_network": False,
                    "default": True,
                    "side_effects": ["workspace"],
                    "idempotency": "best_effort",
                }
            ]
            + (
                [
                    {
                        "id": "execute",
                        "title": "Execute Plan",
                        "kind": "script",
                        "entry": "scripts/execute.py",
                        "runtime": "python3",
                        "timeout_sec": 300,
                        "sandbox": "workspace-write",
                        "allow_network": True,
                        "default": False,
                        "side_effects": ["workspace", "network"],
                        "idempotency": "best_effort",
                    }
                ]
                if is_wallet_style_script
                else []
            )
        ),
    }


def _build_interface(candidate: SkillCandidate, package_kind: str) -> dict[str, Any]:
    short_description = candidate.change_summary or f"{candidate.target_skill_name} generated candidate"
    return {
        "interface": {
            "display_name": candidate.target_skill_name,
            "short_description": short_description,
            "default_prompt": short_description,
        },
        "compatibility": {
            "canonical_format": "agent-skills",
            "adapter_targets": list(ADAPTER_TARGETS),
            "activation": {
                "mode": "manual",
                "paths": [],
            },
            "execution": {
                "context": "inline" if package_kind == "prompt" else "fork",
                "shell": "bash",
            },
            "trust": {
                "source_tier": "local",
                "remote_inline_execution": "forbid",
                "remote_metadata_policy": "explicit-providers-only",
            },
            "degradation": {target: "manual" for target in ADAPTER_TARGETS},
        },
    }


def _write_type_specific_files(package_root: Path, candidate: SkillCandidate, package_kind: str) -> tuple[str, ...]:
    generated: list[str] = []
    style_profile = _wallet_style_profile(candidate)
    strategy_spec = _wallet_strategy_spec(candidate)
    execution_intent = _wallet_execution_intent(candidate)
    token_catalog = _wallet_token_catalog(candidate)
    if package_kind == "prompt":
        instructions_dir = package_root / "instructions"
        instructions_dir.mkdir(parents=True, exist_ok=True)
        (instructions_dir / "primary.md").write_text(
            "\n".join(
                [
                    f"# {candidate.target_skill_name}",
                    "",
                    candidate.change_summary,
                    "",
                    "## Instructions",
                    "",
                    "Use this prompt package as the operational baseline.",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        generated.append("instructions/primary.md")
        return tuple(generated)

    scripts_dir = package_root / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    if style_profile is not None:
        references_dir = package_root / "references"
        references_dir.mkdir(parents=True, exist_ok=True)
        _write_json(references_dir / "style_profile.json", style_profile)
        _write_json(references_dir / "strategy_spec.json", strategy_spec or {})
        _write_json(references_dir / "execution_intent.json", execution_intent or {})
        _write_json(references_dir / "token_catalog.json", token_catalog)
        generated.append("references/style_profile.json")
        generated.append("references/strategy_spec.json")
        generated.append("references/execution_intent.json")
        generated.append("references/token_catalog.json")
        wrapper_body = [
            "from __future__ import annotations",
            "",
            "import json",
            "import math",
            "from pathlib import Path",
            "import re",
            "import sys",
            "",
            "",
            f"PROFILE = json.loads({repr(json.dumps(_json_safe(style_profile), ensure_ascii=False))})",
            f"STRATEGY = json.loads({repr(json.dumps(_json_safe(strategy_spec or {}), ensure_ascii=False))})",
            f"EXECUTION_INTENT = json.loads({repr(json.dumps(_json_safe(execution_intent or {}), ensure_ascii=False))})",
            f"TOKEN_CATALOG = json.loads({repr(json.dumps(_json_safe(token_catalog), ensure_ascii=False))})",
            "ENTRY_FACTORS = list((STRATEGY.get(\"metadata\") or {}).get(\"entry_factors\") or [])",
            "QUOTE_TOKENS = {\"USDT\", \"USDC\", \"DAI\", \"FDUSD\", \"TUSD\", \"WBNB\", \"BNB\", \"WETH\", \"ETH\"}",
            "CHAIN_ROUTE_TOKENS = {",
            "    \"bsc\": {",
            "        \"WBNB\": \"0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c\",",
            "        \"USDT\": \"0x55d398326f99059ff775485246999027b3197955\",",
            "        \"USDC\": \"0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d\",",
            "    },",
            "}",
            "CHAIN_ROUTE_PRICE_HINTS = {",
            "    \"bsc\": {",
            "        \"USDT\": 1.0,",
            "        \"USDC\": 1.0,",
            "        \"WBNB\": 600.0,",
            "        \"BNB\": 600.0,",
            "    },",
            "}",
            "",
            "",
            "def _load_context() -> dict:",
            "    if len(sys.argv) > 1:",
            "        candidate = sys.argv[1]",
            "        path = Path(candidate).expanduser()",
            "        if path.exists() and path.is_file():",
            "            return json.loads(path.read_text(encoding=\"utf-8\"))",
            "        return json.loads(candidate)",
            "    if not sys.stdin.isatty():",
            "        raw = sys.stdin.read().strip()",
            "        if raw:",
            "            return json.loads(raw)",
            "    return {}",
            "",
            "",
            "def _non_quote_tokens(values) -> list[str]:",
            "    items: list[str] = []",
            "    for value in values or []:",
            "        token = str(value or \"\").strip()",
            "        if token and token.upper() not in QUOTE_TOKENS and token not in items:",
            "            items.append(token)",
            "    return items",
            "",
            "",
            "def _extract_max_leg_usd(context: dict) -> float:",
            "    explicit = context.get(\"max_leg_usd\")",
            "    try:",
            "        if explicit is not None:",
            "            return max(50.0, float(explicit))",
            "    except (TypeError, ValueError):",
            "        pass",
            "    matches: list[float] = []",
            "    for text in [*(PROFILE.get(\"execution_rules\") or []), PROFILE.get(\"sizing_note\") or \"\"]:",
            "        for raw in re.findall(r\"\\$?([0-9]+(?:\\.[0-9]+)?)\", str(text)):",
            "            try:",
            "                value = float(raw)",
            "            except ValueError:",
            "                continue",
            "            if value >= 50:",
            "                matches.append(value)",
            "    return min(matches) if matches else 300.0",
            "",
            "",
            "def _resolve_token(symbol: str) -> dict:",
            "    token = dict(TOKEN_CATALOG.get(symbol) or {})",
            "    metadata = dict(EXECUTION_INTENT.get(\"metadata\") or {})",
            "    if not token and symbol == str(metadata.get(\"default_target_token\") or \"\").strip():",
            "        token = {",
            "            \"symbol\": symbol,",
            "            \"token_address\": metadata.get(\"default_target_token_address\"),",
            "            \"chain\": metadata.get(\"chain\") or PROFILE.get(\"chain\"),",
            "        }",
            "    return token",
            "",
            "",
            "def _pick_target_token(context: dict) -> str:",
            "    explicit = str(context.get(\"target_token\") or \"\").strip()",
            "    if explicit:",
            "        return explicit",
            "    candidates = _non_quote_tokens(context.get(\"candidate_tokens\") or [])",
            "    if candidates:",
            "        return candidates[0]",
            "    preferred = _non_quote_tokens(PROFILE.get(\"preferred_tokens\") or [])",
            "    if preferred:",
            "        return preferred[0]",
            "    return str((PROFILE.get(\"preferred_tokens\") or [\"watchlist\"])[0])",
            "",
            "",
            "def _prefer_wrapped_native_source() -> bool:",
            "    stable_bias = str(PROFILE.get(\"stablecoin_bias\") or \"\").lower()",
            "    if any(marker in stable_bias for marker in (\"zero\", \"fully deployed\", \"no stable\")):",
            "        return True",
            "    rules = \" \".join(str(item) for item in PROFILE.get(\"execution_rules\") or [])",
            "    workflow = str(EXECUTION_INTENT.get(\"preferred_workflow\") or \"\")",
            "    route_prefs = \" \".join(str(item) for item in EXECUTION_INTENT.get(\"route_preferences\") or [])",
            "    combined = f\"{rules} {workflow} {route_prefs}\".upper()",
            "    return \"WBNB\" in combined or \"BNB\" in combined",
            "",
            "",
            "def _resolve_source_token(route_symbols: list[str], chain: str) -> dict:",
            "    chain_routes = CHAIN_ROUTE_TOKENS.get(chain, {})",
            "    chain_price_hints = CHAIN_ROUTE_PRICE_HINTS.get(chain, {})",
            "    metadata = dict(EXECUTION_INTENT.get(\"metadata\") or {})",
            "    stable_first = [symbol for symbol in route_symbols if symbol.upper() in {\"USDT\", \"USDC\", \"DAI\", \"FDUSD\", \"TUSD\"}]",
            "    prefer_native = _prefer_wrapped_native_source()",
            "    ordered = list(route_symbols)",
            "    if prefer_native:",
            "        native_first = [symbol for symbol in ordered if symbol.upper() in {\"WBNB\", \"BNB\", \"WETH\", \"ETH\"}]",
            "        ordered = native_first + [symbol for symbol in ordered if symbol not in native_first]",
            "    elif stable_first:",
            "        ordered = stable_first + [symbol for symbol in ordered if symbol not in stable_first]",
            "    default_symbol = str(metadata.get(\"default_source_token\") or \"\").strip()",
            "    default_address = str(metadata.get(\"default_source_token_address\") or \"\").strip()",
            "    if prefer_native:",
            "        for symbol in ordered:",
            "            if symbol.upper() in {\"WBNB\", \"BNB\", \"WETH\", \"ETH\"} and symbol in chain_routes:",
            "                return {\"symbol\": symbol, \"token_address\": chain_routes[symbol], \"chain\": chain, \"unit_price_usd\": chain_price_hints.get(symbol)}",
            "    if stable_first and stable_first[0] in chain_routes:",
            "        return {\"symbol\": stable_first[0], \"token_address\": chain_routes[stable_first[0]], \"chain\": chain, \"unit_price_usd\": chain_price_hints.get(stable_first[0])}",
            "    if default_symbol and default_address:",
            "        return {\"symbol\": default_symbol, \"token_address\": default_address, \"chain\": chain, \"unit_price_usd\": chain_price_hints.get(default_symbol)}",
            "    for symbol in ordered:",
            "        if symbol in TOKEN_CATALOG:",
            "            token = dict(TOKEN_CATALOG[symbol])",
            "            if symbol in chain_price_hints and not token.get(\"unit_price_usd\"):",
            "                token[\"unit_price_usd\"] = chain_price_hints.get(symbol)",
            "            return token",
            "        if symbol in chain_routes:",
            "            return {\"symbol\": symbol, \"token_address\": chain_routes[symbol], \"chain\": chain, \"unit_price_usd\": chain_price_hints.get(symbol)}",
            "    return {}",
            "",
            "",
            "def _infer_route(context: dict, target_token: str) -> list[str]:",
            "    explicit = [str(item or \"\").strip() for item in context.get(\"preferred_route\") or [] if str(item or \"\").strip()]",
            "    if explicit:",
                "        return explicit if explicit[-1] == target_token else [*explicit, target_token]",
            "    route: list[str] = []",
            "    rules = \" \".join(str(item) for item in PROFILE.get(\"execution_rules\") or [])",
            "    for quote in (\"WBNB\", \"USDC\", \"USDT\"):",
            "        if quote in rules and quote not in route:",
            "            route.append(quote)",
            "    for quote in context.get(\"available_routes\") or []:",
            "        text = str(quote or \"\").strip()",
            "        if text and text.upper() in QUOTE_TOKENS and text not in route:",
            "            route.append(text)",
            "    if not route:",
            "        route = [\"USDC\"]",
            "    if route[-1] != target_token:",
            "        route.append(target_token)",
            "    return route",
            "",
            "",
            "def _recommend(context: dict) -> dict:",
            "    market_bias = str(context.get(\"market_bias\") or \"range\").lower()",
            "    dominant_actions = [str(item).lower() for item in PROFILE.get(\"dominant_actions\") or []]",
            "    market_context = dict(context.get(\"market_context\") or {})",
            "    macro = dict(market_context.get(\"macro\") or {})",
            "    signal_context = dict(context.get(\"signal_context\") or {})",
            "    active_entry_factors = {str(item.get('factor_type') or '').strip() for item in signal_context.get('top_entry_factors') or [] if isinstance(item, dict)}",
            "    action = \"watch\"",
            "    if macro.get('regime') == 'risk_off' and (signal_context.get('hard_blocks') or []):",
            "        action = 'watch'",
            "    elif market_bias in {\"bullish\", \"up\"} and PROFILE.get(\"risk_appetite\") in {\"aggressive\", \"balanced\"}:",
            "        action = \"buy\"",
            "    elif market_bias in {\"bearish\", \"down\"} and \"sell\" in dominant_actions:",
            "        action = \"sell\"",
            "    elif dominant_actions and dominant_actions[0] in {\"buy\", \"swap\"}:",
            "        action = \"buy\"",
            "    if ENTRY_FACTORS and not active_entry_factors.intersection({str(item.get('factor_type') or '').strip() for item in ENTRY_FACTORS if isinstance(item, dict)}):",
            "        action = 'watch' if action == 'buy' else action",
            "    confidence = min(0.95, float(PROFILE.get(\"confidence\") or 0.4) * 0.85 + (0.08 if action != \"watch\" else 0.0))",
            "    return {",
            "        \"action\": action,",
            "        \"confidence\": round(confidence, 4),",
            "        \"rationale\": [",
            "            PROFILE.get(\"summary\") or \"wallet-style profile available\",",
            "            STRATEGY.get(\"summary\") or \"strategy summary unavailable\",",
            "            *(PROFILE.get(\"execution_rules\") or []),",
            "        ],",
            "        \"guardrails\": PROFILE.get(\"anti_patterns\") or [],",
            "    }",
            "",
            "",
            "def _build_trade_plan(context: dict, recommendation: dict) -> dict:",
            "    target_token = _pick_target_token(context)",
            "    target_meta = _resolve_token(target_token)",
            "    max_leg_usd = _extract_max_leg_usd(context)",
            "    desired_notional = context.get(\"desired_notional_usd\")",
            "    try:",
            "        desired_notional_value = float(desired_notional) if desired_notional is not None else max_leg_usd * 2",
            "    except (TypeError, ValueError):",
            "        desired_notional_value = max_leg_usd * 2",
            "    desired_notional_value = max(max_leg_usd, desired_notional_value)",
            "    leg_count = max(1, math.ceil(desired_notional_value / max_leg_usd))",
            "    per_leg_usd = round(desired_notional_value / leg_count, 2)",
            "    chain = str(PROFILE.get(\"chain\") or context.get(\"chain\") or \"bsc\")",
            "    execution_windows = [str(item) for item in PROFILE.get(\"active_windows\") or [] if str(item).strip()]",
            "    burst_profile = str(context.get(\"burst_profile\") or (\"same-minute-burst\" if \"burst\" in str(PROFILE.get(\"execution_tempo\") or \"\").lower() else \"staggered\"))",
            "    route_symbols = _infer_route(context, target_token)",
            "    source_meta = _resolve_source_token(route_symbols, chain)",
            "    return {",
            "        \"mode\": \"style-simulated-trade\",",
            "        \"chain\": chain,",
            "        \"wallet_address\": PROFILE.get(\"wallet\"),",
            "        \"entry_action\": recommendation.get(\"action\"),",
            "        \"target_token\": target_token,",
            "        \"target_token_address\": target_meta.get(\"token_address\"),",
            "        \"route\": route_symbols,",
            "        \"desired_notional_usd\": round(desired_notional_value, 2),",
            "        \"max_leg_usd\": round(max_leg_usd, 2),",
            "        \"leg_count\": leg_count,",
            "        \"per_leg_usd\": per_leg_usd,",
            "        \"execution_source_symbol\": source_meta.get(\"symbol\"),",
            "        \"execution_source_address\": source_meta.get(\"token_address\"),",
            "        \"execution_source_unit_price_usd\": source_meta.get(\"unit_price_usd\"),",
            "        \"execution_source_readable_amount\": round(per_leg_usd / float(source_meta.get(\"unit_price_usd\")), 8) if source_meta.get(\"unit_price_usd\") else None,",
            "        \"execution_windows\": execution_windows,",
            "        \"burst_profile\": burst_profile,",
            "        \"tempo\": PROFILE.get(\"execution_tempo\"),",
            "        \"guardrails\": PROFILE.get(\"anti_patterns\") or [],",
            "        \"rules\": PROFILE.get(\"execution_rules\") or [],",
            "        \"execution_intent_mode\": EXECUTION_INTENT.get(\"mode\"),",
            "    }",
            "",
            "",
            "def main() -> int:",
            "    context = _load_context()",
            "    recommendation = _recommend(context)",
            "    trade_plan = _build_trade_plan(context, recommendation)",
            "    payload = {",
            "        \"ok\": True,",
            "        \"action\": \"primary\",",
            f"        \"summary\": {json.dumps(candidate.change_summary, ensure_ascii=False)},",
            "        \"style_profile\": PROFILE,",
            "        \"strategy\": STRATEGY,",
            "        \"execution_intent\": EXECUTION_INTENT,",
            "        \"input_context\": context,",
            "        \"recommendation\": recommendation,",
            "        \"trade_plan\": trade_plan,",
            "        \"artifacts\": [],",
            "        \"metadata\": {\"skill_family\": \"wallet_style\"},",
            "    }",
            "    print(json.dumps(payload, ensure_ascii=False, indent=2))",
            "    return 0",
            "",
            "",
            "if __name__ == \"__main__\":",
            "    raise SystemExit(main())",
            "",
        ]
        execute_body = [
            "from __future__ import annotations",
            "",
            "import json",
            "from pathlib import Path",
            "import sys",
            "",
            "",
            "def _load_context() -> dict:",
            "    if len(sys.argv) > 1:",
            "        candidate = sys.argv[1]",
            "        path = Path(candidate).expanduser()",
            "        if path.exists() and path.is_file():",
            "            return json.loads(path.read_text(encoding=\"utf-8\"))",
            "        return json.loads(candidate)",
            "    if not sys.stdin.isatty():",
            "        raw = sys.stdin.read().strip()",
            "        if raw:",
            "            return json.loads(raw)",
            "    return {}",
            "",
            "",
            f"EXECUTION_INTENT = json.loads({repr(json.dumps(_json_safe(execution_intent or {}), ensure_ascii=False))})",
            "",
            "",
            "def main() -> int:",
            "    context = _load_context()",
            "    project_root = Path(__file__).resolve().parents[3]",
            "    sys.path.insert(0, str(project_root / 'src'))",
            "    from ot_skill_enterprise.execution import collect_execution_result, prepare_execution, run_dry_run, run_live",
            "    trade_plan = dict(context.get('trade_plan') or {})",
            "    execution_intent = dict(context.get('execution_intent') or EXECUTION_INTENT)",
            "    mode = str(context.get('mode') or 'prepare_only').strip() or 'prepare_only'",
            "    approval_granted = bool(context.get('approval_granted'))",
            "    if not trade_plan:",
            "        payload = {",
            "            'ok': False,",
            "            'action': 'execute',",
            "            'summary': 'trade_plan is required',",
            "            'execution_readiness': 'blocked_by_risk',",
            "            'artifacts': [],",
            "        }",
            "        print(json.dumps(payload, ensure_ascii=False, indent=2))",
            "        return 1",
            "    if mode == 'prepare_only':",
            "        prepared = prepare_execution(trade_plan, execution_intent, project_root=project_root)",
            "        result = collect_execution_result(prepared, mode='dry_run')",
            "    elif mode == 'dry_run':",
            "        result = run_dry_run(trade_plan, execution_intent, project_root=project_root)",
            "    elif mode == 'live':",
            "        live_intent = dict(execution_intent)",
            "        live_intent['requires_explicit_approval'] = not approval_granted",
            "        result = run_live(trade_plan, live_intent, project_root=project_root)",
            "    else:",
            "        result = {",
            "            'ok': False,",
            "            'mode': mode,",
            "            'execution_readiness': 'blocked_by_risk',",
            "            'prepared_execution': {},",
            "            'checks': [],",
            "            'execution': {},",
            "        }",
            "    payload = {",
            "        'ok': bool(result.get('ok')),",
            "        'action': 'execute',",
            f"        'summary': {json.dumps(candidate.change_summary, ensure_ascii=False)},",
            "        'execution_readiness': result.get('execution_readiness'),",
            "        'execution_intent': execution_intent,",
            "        'trade_plan': trade_plan,",
            "        'prepared_execution': result.get('prepared_execution'),",
            "        'checks': result.get('checks'),",
            "        'execution_result': result.get('execution'),",
            "        'artifacts': [],",
            "        'metadata': {'skill_family': 'wallet_style'},",
            "    }",
            "    print(json.dumps(payload, ensure_ascii=False, indent=2))",
            "    return 0 if payload['ok'] else 1",
            "",
            "",
            "if __name__ == '__main__':",
            "    raise SystemExit(main())",
            "",
        ]
    else:
        wrapper_body = [
            "from __future__ import annotations",
            "",
            "import json",
            "from pathlib import Path",
            "",
            "",
            "def main() -> int:",
            "    payload = {",
            f'        "ok": True,',
            f'        "action": "primary",',
            f'        "summary": {json.dumps(candidate.change_summary, ensure_ascii=False)},',
            '        "artifacts": [],',
            '        "metadata": {},',
            "    }",
            "    print(json.dumps(payload, ensure_ascii=False, indent=2))",
            "    return 0",
            "",
            "",
            "if __name__ == \"__main__\":",
            "    raise SystemExit(main())",
            "",
        ]
    (scripts_dir / "primary.py").write_text("\n".join(wrapper_body), encoding="utf-8")
    generated.append("scripts/primary.py")
    if style_profile is not None:
        (scripts_dir / "execute.py").write_text("\n".join(execute_body), encoding="utf-8")
        generated.append("scripts/execute.py")

    if package_kind == "provider-adapter":
        adapters_dir = package_root / "adapters"
        adapters_dir.mkdir(parents=True, exist_ok=True)
        adapter_body = [
            "from __future__ import annotations",
            "",
            "from dataclasses import dataclass",
            "",
            "",
            "@dataclass(slots=True)",
            "class GeneratedProviderAdapter:",
            f"    name: str = {json.dumps(candidate.target_skill_name, ensure_ascii=False)}",
            '    supported_actions: tuple[str, ...] = ("primary",)',
            "",
            "    def describe(self) -> dict[str, str]:",
            "        return {\"name\": self.name, \"kind\": \"provider-adapter\"}",
            "",
            "",
            "def build_provider_adapter() -> GeneratedProviderAdapter:",
            "    return GeneratedProviderAdapter()",
            "",
        ]
        (adapters_dir / "provider.py").write_text("\n".join(adapter_body), encoding="utf-8")
        generated.append("adapters/provider.py")
    return tuple(generated)


@dataclass(slots=True)
class SkillPackageCompiler:
    project_root: Path
    workspace_root: Path

    def _candidate_root(self, candidate: SkillCandidate, package_kind: str, output_root: Path | None = None) -> Path:
        return _package_root(self.project_root, candidate, package_kind, output_root=output_root)

    def compile(
        self,
        candidate: SkillCandidate | Mapping[str, Any],
        *,
        output_root: Path | None = None,
        package_kind: str | None = None,
        force: bool = True,
    ) -> PackageBuildResult:
        normalized = _candidate_payload(candidate)
        resolved_kind = _package_kind(normalized, package_kind)
        package_root = self._candidate_root(normalized, resolved_kind, output_root=output_root)
        if package_root.exists() and force:
            shutil.rmtree(package_root)
        package_root.mkdir(parents=True, exist_ok=True)

        skill_md = _render_skill_md(normalized, resolved_kind)
        manifest = _build_manifest(normalized, resolved_kind, package_root)
        actions = _build_actions(normalized, resolved_kind)
        interface = _build_interface(normalized, resolved_kind)

        (package_root / "SKILL.md").write_text(skill_md, encoding="utf-8")
        _write_json(package_root / "manifest.json", manifest)
        _write_yaml(package_root / "actions.yaml", actions)
        _write_yaml(package_root / "agents" / "interface.yaml", interface)
        generated_files = ["SKILL.md", "manifest.json", "actions.yaml", "agents/interface.yaml"]
        generated_files.extend(_write_type_specific_files(package_root, normalized, resolved_kind))

        bundle_sha256 = _tree_sha256(package_root)
        return PackageBuildResult(
            candidate=normalized,
            package_root=package_root,
            package_kind=resolved_kind,
            generated_files=tuple(dict.fromkeys(generated_files)),
            bundle_sha256=bundle_sha256,
            manifest=manifest,
            actions=actions,
            interface=interface,
            skill_md=skill_md,
        )

    def validate(
        self,
        package_root: Path | str,
        *,
        candidate: SkillCandidate | Mapping[str, Any] | None = None,
        action_id: str | None = None,
    ) -> PackageValidationResult:
        resolved_root = Path(package_root).expanduser().resolve()
        normalized_candidate = _candidate_payload(candidate or {"candidate_slug": resolved_root.name, "candidate_id": resolved_root.name})
        phases: list[dict[str, Any]] = []
        issues: list[dict[str, Any]] = []
        warnings: list[dict[str, Any]] = []

        structure_report = validate_package_structure(resolved_root)
        structure_failures, structure_warnings = _report_entries(structure_report)
        phases.append(
            {
                "phase": "package structure validate",
                "ok": bool(getattr(structure_report, "ok", False)),
                "failures": structure_failures,
                "warnings": structure_warnings,
            }
        )
        issues.extend(structure_failures)
        warnings.extend(structure_warnings)

        if structure_report.ok:
            contract_report = validate_contract_skill_package(resolved_root, action_id=action_id)
            contract_failures, contract_warnings = _report_entries(contract_report)
            phases.append(
                {
                    "phase": "manifest/actions/interface validate",
                    "ok": bool(getattr(contract_report, "ok", False)),
                    "failures": contract_failures,
                    "warnings": contract_warnings,
                }
            )
            issues.extend(contract_failures)
            warnings.extend(contract_warnings)
        else:
            phases.append({"phase": "manifest/actions/interface validate", "ok": False, "failures": [], "warnings": []})

        discovery_ok = False
        discovery_message = "runtime discovery not attempted"
        skills_root = (self.project_root / "skills").resolve()
        if resolved_root.is_relative_to(skills_root):
            discovered = EnterpriseBridge.from_project_root(self.project_root).discover_local_skill_packages()
            discovery_ok = any(item.root.resolve() == resolved_root for item in discovered)
            discovery_message = "runtime discovery validated" if discovery_ok else "runtime discovery missing from local skill registry"
        else:
            discovery_ok = True
            discovery_message = "runtime discovery deferred until promotion"
        phases.append(
            {
                "phase": "runtime discovery validate",
                "ok": discovery_ok,
                "message": discovery_message,
            }
        )
        if not discovery_ok:
            warnings.append({"code": "runtime_discovery_pending", "message": discovery_message})

        dry_run_ok = False
        dry_run_message = "dry-run pending"
        try:
            package = load_skill_package(resolved_root)
            action_ids = [action.id for action in package.actions.actions]
            dry_run_ok = bool(action_ids)
            dry_run_message = "dry-run succeeded" if dry_run_ok else "dry-run found no actions"
        except Exception as exc:  # noqa: BLE001
            dry_run_ok = False
            dry_run_message = str(exc)
        phases.append({"phase": "dry-run validate", "ok": dry_run_ok, "message": dry_run_message})
        if not dry_run_ok:
            issues.append({"code": "dry_run_failed", "message": dry_run_message})

        evaluation_ok = bool(normalized_candidate.source_run_id) and bool(normalized_candidate.runtime_session_id)
        evaluation_message = "candidate linked to source run and session" if evaluation_ok else "candidate missing source_run_id or runtime_session_id"
        phases.append({"phase": "evaluation validate", "ok": evaluation_ok, "message": evaluation_message})
        if not evaluation_ok:
            issues.append({"code": "candidate_missing_source_run", "message": evaluation_message})

        ok = bool(getattr(structure_report, "ok", False)) and all(bool(item.get("ok", False)) for item in phases[1:])
        return PackageValidationResult(
            candidate=normalized_candidate,
            package_root=resolved_root,
            package_kind=_package_kind(normalized_candidate),
            bundle_sha256=_tree_sha256(resolved_root) if resolved_root.exists() else "",
            ok=ok,
            phases=tuple(phases),
            issues=tuple(issues),
            warnings=tuple(warnings),
        )

    def promote(
        self,
        candidate: SkillCandidate | Mapping[str, Any],
        *,
        output_root: Path | None = None,
        package_kind: str | None = None,
        force: bool = True,
        action_id: str | None = None,
    ) -> PromotionRecord:
        normalized = _candidate_payload(candidate)
        build = self.compile(normalized, output_root=output_root, package_kind=package_kind, force=force)
        validation = self.validate(build.package_root, candidate=normalized, action_id=action_id)
        if not validation.ok:
            raise ValueError("candidate package validation failed")

        promoted_root = (self.project_root / "skills" / normalized.candidate_slug).resolve()
        if promoted_root.exists():
            if not force:
                raise ValueError(f"promoted skill already exists: {promoted_root}")
            shutil.rmtree(promoted_root)
        promoted_root.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(build.package_root, promoted_root)

        promoted_validation = self.validate(promoted_root, candidate=normalized, action_id=action_id)
        if not promoted_validation.ok:
            raise ValueError("promoted skill package failed runtime discovery or validation")

        promotion_id = f"promotion-{_short_hash({'candidate_id': normalized.candidate_id, 'bundle_sha256': build.bundle_sha256, 'package_root': str(promoted_root)})}"
        promotion_root = self.workspace_root / "evolution-registry" / "promotions"
        promotion_root.mkdir(parents=True, exist_ok=True)
        record = PromotionRecord(
            promotion_id=promotion_id,
            candidate=normalized,
            package_root=promoted_root,
            package_kind=build.package_kind,
            bundle_sha256=build.bundle_sha256,
            validation_status="passed",
            registry_status="promoted",
            package_name=promoted_root.name,
            runtime_session_id=normalized.runtime_session_id,
            metadata={
                "candidate_package_root": str(build.package_root),
                "validation": promoted_validation.to_dict(),
                "build": build.to_dict(),
            },
        )
        _write_json(promotion_root / f"{promotion_id}.json", record.to_dict())
        return record

    def promote_from_payload(
        self,
        payload: Mapping[str, Any],
        *,
        output_root: Path | None = None,
        package_kind: str | None = None,
        force: bool = True,
        action_id: str | None = None,
    ) -> PromotionRecord:
        return self.promote(
            SkillCandidate.from_mapping(payload),
            output_root=output_root,
            package_kind=package_kind,
            force=force,
            action_id=action_id,
        )


def build_skill_package_compiler(
    project_root: Path | None = None,
    workspace_root: Path | None = None,
) -> SkillPackageCompiler:
    resolved_project_root = Path(project_root).expanduser().resolve() if project_root is not None else resolve_project_root()
    resolved_workspace_root = Path(workspace_root).expanduser().resolve() if workspace_root is not None else (resolved_project_root / ".ot-workspace").resolve()
    return SkillPackageCompiler(project_root=resolved_project_root, workspace_root=resolved_workspace_root)
