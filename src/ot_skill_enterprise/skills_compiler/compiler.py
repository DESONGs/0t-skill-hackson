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
_MODULE_SRC_ROOT = Path(__file__).resolve().parents[2]
NO_STABLE_ARCHETYPE = "no_stable_archetype"
ARCHETYPE_FIELD_NAMES = (
    "primary_archetype",
    "secondary_archetypes",
    "behavioral_patterns",
    "archetype_confidence",
    "archetype_evidence_summary",
    "archetype_token_preference",
)


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


def _compact_text(value: Any, *, max_chars: int) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= max_chars:
        return text
    trimmed = text[: max_chars - 3].rsplit(" ", 1)[0].strip()
    if not trimmed:
        trimmed = text[: max_chars - 3].strip()
    return f"{trimmed}..."


def _compact_bullets(
    values: list[Any] | tuple[Any, ...] | None,
    *,
    limit: int,
    max_chars: int,
    overflow_note: str | None = None,
) -> list[str]:
    lines: list[str] = []
    for raw in list(values or [])[:limit]:
        text = _compact_text(raw, max_chars=max_chars)
        if text:
            lines.append(f"- {text}")
    if not lines:
        return []
    if overflow_note and len(list(values or [])) > limit:
        lines.append(f"- {overflow_note}")
    return lines


def _natural_join(values: list[str], *, conjunction: str = "and") -> str:
    items = [str(value).strip() for value in values if str(value or "").strip()]
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} {conjunction} {items[1]}"
    return f"{', '.join(items[:-1])}, {conjunction} {items[-1]}"


def _safe_text(value: Any) -> str:
    return str(value or "").strip()


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _first_text(*values: Any) -> str:
    for value in values:
        text = _safe_text(value)
        if text:
            return text
    return ""


def _unique_texts(values: Any) -> list[str]:
    items: list[str] = []
    for value in values or []:
        text = _safe_text(value)
        if text and text not in items:
            items.append(text)
    return items


def _humanize_label(value: str) -> str:
    parts = []
    for part in str(value or "").replace("-", " ").replace("_", " ").split():
        parts.append("frequency" if part == "freq" else part)
    return " ".join(parts).strip()


def _pattern_labels(patterns: Any) -> list[str]:
    labels: list[str] = []
    for item in patterns or []:
        if isinstance(item, Mapping):
            label = _first_text(
                item.get("pattern_label"),
                item.get("label"),
                item.get("name"),
                item.get("pattern"),
                item.get("pattern_type"),
            )
        else:
            label = _safe_text(item)
        if label and label not in labels:
            labels.append(label)
    return labels


def _pattern_bullets(patterns: Any, *, limit: int = 3) -> list[str]:
    bullets: list[str] = []
    for item in list(patterns or [])[:limit]:
        if isinstance(item, Mapping):
            label = _first_text(
                item.get("pattern_label"),
                item.get("label"),
                item.get("name"),
                item.get("pattern"),
                item.get("pattern_type"),
            )
            strength = item.get("strength")
            evidence = _unique_texts(item.get("evidence") or item.get("signals") or [])
            pieces: list[str] = []
            if label:
                pieces.append(_humanize_label(label))
            if strength is not None:
                pieces.append(f"strength={_safe_float(strength):.2f}")
            if evidence:
                pieces.append(f"evidence: {', '.join(evidence[:2])}")
            text = " | ".join(pieces)
        else:
            text = _safe_text(item)
        if text:
            bullets.append(f"- {text}")
    return bullets


def _human_summary_text(
    *,
    style_profile: Mapping[str, Any] | None,
    archetype: Mapping[str, Any] | None,
    fallback: str,
) -> str:
    profile = style_profile or {}
    archetype_payload = archetype or {}
    chain = _safe_text(profile.get("chain")).upper()
    primary = _safe_text(archetype_payload.get("primary_archetype"))
    secondary = [_humanize_label(item) for item in _unique_texts(archetype_payload.get("secondary_archetypes") or [])[:3]]
    tempo = _humanize_label(_safe_text(profile.get("execution_tempo")))
    risk = _humanize_label(_safe_text(profile.get("risk_appetite")))
    conviction = _humanize_label(_safe_text(profile.get("conviction_profile")))
    tokens = _unique_texts(
        profile.get("preferred_tokens")
        or archetype_payload.get("archetype_token_preference")
        or []
    )[:3]
    windows = [_humanize_label(item) for item in _unique_texts(profile.get("active_windows") or [])[:2]]
    confidence = _safe_float(archetype_payload.get("archetype_confidence"), 0.0)

    sentences: list[str] = []
    if primary and primary != NO_STABLE_ARCHETYPE:
        first = f"This {chain or 'wallet'} wallet behaves like a {_humanize_label(primary)} trader"
        if secondary:
            first += f" with {_natural_join(secondary)} secondary traits"
        sentences.append(first + ".")
    elif _safe_text(profile.get("style_label")):
        style_label = _humanize_label(_safe_text(profile.get("style_label")))
        prefix = f"This {chain or 'wallet'} wallet"
        sentences.append(f"{prefix} follows a {style_label} style.")

    posture_bits = [bit for bit in (tempo, risk, conviction) if bit]
    if tempo and risk and conviction:
        sentences.append(
            f"Its execution tempo is {tempo}, its risk posture is {risk}, "
            f"and its conviction profile is {conviction}."
        )
    elif tempo and risk:
        sentences.append(f"Its execution tempo is {tempo}, and its risk posture is {risk}.")
    elif tempo and conviction:
        sentences.append(f"Its execution tempo is {tempo}, and its conviction profile is {conviction}.")
    elif risk and conviction:
        sentences.append(f"Its risk posture is {risk}, and its conviction profile is {conviction}.")
    elif posture_bits:
        sentences.append(f"It is best described by a {posture_bits[0]} profile.")

    activity_parts: list[str] = []
    if tokens:
        activity_parts.append(f"rotates most often through {_natural_join(tokens)}")
    if windows:
        activity_parts.append(f"is most active during {_natural_join(windows)}")
    if activity_parts:
        sentences.append(f"It {_natural_join(activity_parts)}.")

    if confidence > 0:
        sentences.append(f"The archetype signal is rated at {confidence:.2f} confidence.")

    summary = " ".join(sentence.strip() for sentence in sentences if sentence.strip())
    return summary or _compact_text(fallback, max_chars=220)


def _is_placeholder_archetype(value: Any) -> bool:
    return _safe_text(value).lower() == NO_STABLE_ARCHETYPE


def _should_replace_archetype_field(field_name: str, current: Any, incoming: Any) -> bool:
    if incoming is None:
        return False
    if current is None:
        return True
    if field_name == "primary_archetype":
        current_text = _safe_text(current).lower()
        incoming_text = _safe_text(incoming).lower()
        if not current_text:
            return bool(incoming_text)
        if current_text == NO_STABLE_ARCHETYPE and incoming_text and incoming_text != NO_STABLE_ARCHETYPE:
            return True
        return False
    if field_name in {"secondary_archetypes", "behavioral_patterns", "archetype_token_preference"}:
        return not _unique_texts(current) and bool(_unique_texts(incoming))
    if field_name == "archetype_confidence":
        return _safe_float(current, 0.0) <= 0.0 and _safe_float(incoming, 0.0) > 0.0
    if field_name == "archetype_evidence_summary":
        return not _first_text(current) and bool(_first_text(incoming))
    return False


def _merge_archetype_source(target: dict[str, Any], payload: Mapping[str, Any]) -> None:
    nested = payload.get("archetype")
    if isinstance(nested, Mapping):
        _merge_archetype_source(target, nested)

    metadata = payload.get("metadata")
    if isinstance(metadata, Mapping):
        nested_metadata = metadata.get("archetype")
        if isinstance(nested_metadata, Mapping):
            _merge_archetype_source(target, nested_metadata)
        for field_name in ARCHETYPE_FIELD_NAMES:
            if field_name in metadata and _should_replace_archetype_field(field_name, target.get(field_name), metadata.get(field_name)):
                target[field_name] = metadata.get(field_name)
        for alias, target_name in (
            ("primary_label", "primary_archetype"),
            ("trading_archetype", "primary_archetype"),
            ("trading_archetype_label", "primary_archetype"),
            ("label", "primary_archetype"),
            ("confidence", "archetype_confidence"),
            ("evidence", "archetype_evidence_summary"),
            ("token_preference", "archetype_token_preference"),
            ("preferred_tokens", "archetype_token_preference"),
        ):
            if alias in metadata and _should_replace_archetype_field(target_name, target.get(target_name), metadata.get(alias)):
                target[target_name] = metadata.get(alias)

    for field_name in ARCHETYPE_FIELD_NAMES:
        if field_name in payload and _should_replace_archetype_field(field_name, target.get(field_name), payload.get(field_name)):
            target[field_name] = payload.get(field_name)

    alias_pairs = (
        ("primary_label", "primary_archetype"),
        ("trading_archetype", "primary_archetype"),
        ("trading_archetype_label", "primary_archetype"),
        ("label", "primary_archetype"),
        ("confidence", "archetype_confidence"),
        ("evidence", "archetype_evidence_summary"),
        ("token_preference", "archetype_token_preference"),
        ("preferred_tokens", "archetype_token_preference"),
    )
    for alias, target_name in alias_pairs:
        if alias in payload and _should_replace_archetype_field(target_name, target.get(target_name), payload.get(alias)):
            target[target_name] = payload.get(alias)


def _wallet_archetype(candidate: SkillCandidate) -> dict[str, Any] | None:
    style_profile = _wallet_style_profile(candidate) or {}
    sources: list[Mapping[str, Any]] = []
    for payload in (
        candidate.generation_spec.get("archetype"),
        candidate.metadata.get("archetype"),
        (style_profile.get("metadata") if isinstance(style_profile.get("metadata"), Mapping) else {}).get("archetype"),
        style_profile.get("metadata") if isinstance(style_profile.get("metadata"), Mapping) else None,
        style_profile.get("archetype"),
        style_profile,
    ):
        if isinstance(payload, Mapping):
            sources.append(payload)

    combined: dict[str, Any] = {}
    for payload in sources:
        _merge_archetype_source(combined, payload)

    primary = _first_text(
        combined.get("primary_archetype"),
        combined.get("primary_label"),
        combined.get("trading_archetype"),
        combined.get("style_label"),
    )
    secondary = _unique_texts(combined.get("secondary_archetypes") or [])
    raw_patterns = combined.get("behavioral_patterns") or []
    pattern_labels = _pattern_labels(raw_patterns)
    confidence = _safe_float(combined.get("archetype_confidence"), 0.0)
    if not confidence:
        confidence = _safe_float(combined.get("confidence"), 0.0)
    evidence_summary = _first_text(
        combined.get("archetype_evidence_summary"),
        combined.get("evidence_summary"),
        combined.get("evidence"),
    )
    token_preference = _unique_texts(
        combined.get("archetype_token_preference")
        or combined.get("token_preference")
        or combined.get("preferred_tokens")
        or []
    )
    if not primary and not secondary and not pattern_labels and not evidence_summary and not token_preference:
        return None
    if not primary:
        primary = NO_STABLE_ARCHETYPE
    if not evidence_summary and pattern_labels:
        evidence_summary = ", ".join(pattern_labels)
    persona_parts: list[str] = []
    display_primary = _first_text(combined.get("style_label"), primary)
    if primary == NO_STABLE_ARCHETYPE:
        persona_parts.append("no stable archetype yet")
    else:
        persona_parts.append(f"{_humanize_label(display_primary)} trader")
    if secondary:
        persona_parts.append(f"secondary patterns: {', '.join(_humanize_label(item) for item in secondary[:3])}")
    if pattern_labels:
        persona_parts.append(f"behavioral patterns: {', '.join(_humanize_label(item) for item in pattern_labels[:3])}")
    if token_preference:
        persona_parts.append(f"token preference: {', '.join(token_preference[:3])}")
    if confidence:
        persona_parts.append(f"confidence {confidence:.2f}")
    if evidence_summary:
        persona_parts.append(f"evidence: {_compact_text(evidence_summary, max_chars=96)}")
    summary = "; ".join(persona_parts)
    return {
        "primary_archetype": primary,
        "secondary_archetypes": secondary,
        "behavioral_patterns": _json_safe(raw_patterns),
        "behavioral_pattern_labels": pattern_labels,
        "archetype_confidence": round(confidence, 4) if confidence else 0.0,
        "archetype_evidence_summary": evidence_summary,
        "archetype_token_preference": token_preference,
        "summary": summary,
    }


def _augment_style_profile(style_profile: dict[str, Any] | None, archetype: dict[str, Any] | None) -> dict[str, Any] | None:
    if style_profile is None:
        return None
    merged = dict(style_profile)
    if archetype is not None:
        existing_archetype = dict(merged.get("archetype") or {}) if isinstance(merged.get("archetype"), Mapping) else {}
        for field_name in ARCHETYPE_FIELD_NAMES:
            if _should_replace_archetype_field(field_name, existing_archetype.get(field_name), archetype.get(field_name)):
                existing_archetype[field_name] = archetype.get(field_name)
        if _should_replace_archetype_field("primary_archetype", existing_archetype.get("primary_archetype"), archetype.get("primary_archetype")):
            existing_archetype["primary_archetype"] = archetype.get("primary_archetype")
        merged["archetype"] = existing_archetype or archetype
        for field_name in ARCHETYPE_FIELD_NAMES:
            value = archetype.get(field_name)
            if _should_replace_archetype_field(field_name, merged.get(field_name), value):
                merged[field_name] = value
        metadata = dict(merged.get("metadata") or {}) if isinstance(merged.get("metadata"), Mapping) else {}
        nested_metadata_archetype = dict(metadata.get("archetype") or {}) if isinstance(metadata.get("archetype"), Mapping) else {}
        for field_name in ARCHETYPE_FIELD_NAMES:
            value = archetype.get(field_name)
            if _should_replace_archetype_field(field_name, metadata.get(field_name), value):
                metadata[field_name] = value
            if _should_replace_archetype_field(field_name, nested_metadata_archetype.get(field_name), value):
                nested_metadata_archetype[field_name] = value
        if nested_metadata_archetype:
            metadata["archetype"] = nested_metadata_archetype
        if metadata:
            merged["metadata"] = metadata
    return merged


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
    archetype = _wallet_archetype(candidate)
    augmented_style_profile = _augment_style_profile(style_profile, archetype)
    description = _human_summary_text(
        style_profile=augmented_style_profile,
        archetype=archetype,
        fallback=candidate.change_summary,
    )
    frontmatter = {
        "name": package_name,
        "description": description,
        "version": "1.0.0",
        "owner": "mainagent",
        "status": "experimental",
        "tags": [
            "generated",
            "candidate",
            package_kind,
            candidate.target_skill_kind,
            f"archetype:{(archetype or {}).get('primary_archetype') or 'unknown'}",
        ],
        "metadata": {
            "candidate_id": candidate.candidate_id,
            "runtime_session_id": candidate.runtime_session_id,
            "source_run_id": candidate.source_run_id,
            "source_evaluation_id": candidate.source_evaluation_id,
            "target_skill_name": candidate.target_skill_name,
            "target_skill_kind": candidate.target_skill_kind,
            "candidate_type": candidate.candidate_type,
            "trading_archetype": archetype,
            "archetype_primary": (archetype or {}).get("primary_archetype"),
            "archetype_summary": (archetype or {}).get("summary"),
        },
    }
    if augmented_style_profile is not None:
        execution_rule_lines = _compact_bullets(
            augmented_style_profile.get("execution_rules") or [],
            limit=5,
            max_chars=96,
        ) or ["- No execution rules captured"]
        anti_pattern_lines = _compact_bullets(
            augmented_style_profile.get("anti_patterns") or [],
            limit=6,
            max_chars=96,
            overflow_note="Additional risk notes are preserved in references/style_profile.json.",
        ) or ["- No anti-patterns captured"]
        archetype_lines = [
            "- Trader class: no stable archetype yet; keep the package evidence-first and conservative."
            if not archetype or (archetype.get("primary_archetype") == NO_STABLE_ARCHETYPE)
            else f"- Trader class: {_humanize_label(str(archetype.get('primary_archetype') or 'unknown'))}."
        ]
        if archetype and archetype.get("summary"):
            archetype_lines.append(f"- Persona: {archetype.get('summary')}")
        if archetype and archetype.get("secondary_archetypes"):
            archetype_lines.append(
                f"- Secondary archetypes: {', '.join(_humanize_label(item) for item in archetype.get('secondary_archetypes')[:3])}"
            )
        if archetype and archetype.get("behavioral_pattern_labels"):
            archetype_lines.append(
                f"- Behavioral patterns: {', '.join(_humanize_label(item) for item in archetype.get('behavioral_pattern_labels')[:3])}"
            )
        if archetype and archetype.get("archetype_confidence"):
            archetype_lines.append(f"- Archetype confidence: {float(archetype.get('archetype_confidence')):.2f}")
        if archetype and archetype.get("archetype_evidence_summary"):
            archetype_lines.append(
                f"- Evidence summary: {_compact_text(archetype.get('archetype_evidence_summary'), max_chars=140)}"
            )
        if archetype and archetype.get("archetype_token_preference"):
            archetype_lines.append(
                f"- Token preference: {', '.join(_humanize_label(item) for item in archetype.get('archetype_token_preference')[:4])}"
            )
        body_lines = [
            f"# {candidate.target_skill_name}",
            "",
            description,
            "",
            "## Wallet Style Signature",
            "",
            f"- Wallet: {augmented_style_profile.get('wallet') or candidate.metadata.get('wallet_address') or 'unknown'}",
            f"- Chain: {augmented_style_profile.get('chain') or candidate.metadata.get('chain') or 'unknown'}",
            f"- Style label: {augmented_style_profile.get('style_label') or 'wallet-style'}",
            f"- Execution tempo: {augmented_style_profile.get('execution_tempo') or 'unknown'}",
            f"- Risk appetite: {augmented_style_profile.get('risk_appetite') or 'unknown'}",
            f"- Conviction profile: {augmented_style_profile.get('conviction_profile') or 'unknown'}",
            f"- Stablecoin bias: {augmented_style_profile.get('stablecoin_bias') or 'unknown'}",
            "",
            "## Trading Archetype",
            "",
            *archetype_lines,
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
    archetype = _wallet_archetype(candidate)
    style_profile_payload = _augment_style_profile(style_profile, archetype)
    strategy_spec = _wallet_strategy_spec(candidate)
    execution_intent = _wallet_execution_intent(candidate)
    description = _human_summary_text(
        style_profile=style_profile_payload,
        archetype=archetype,
        fallback=candidate.change_summary,
    )
    return {
        "schema_version": "v1",
        "name": package_root.name,
        "description": description,
        "version": "1.0.0",
        "owner": "mainagent",
        "kind": package_kind,
        "updated_at": date.today().isoformat(),
        "status": "experimental",
        "maturity_tier": "scaffold",
        "context_budget_tier": "production",
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
            "wallet_style_profile": style_profile_payload,
            "strategy_spec": strategy_spec,
            "execution_intent": execution_intent,
            "trading_archetype": archetype,
            "archetype_primary": (archetype or {}).get("primary_archetype"),
            "archetype_summary": (archetype or {}).get("summary"),
        },
        "references": {
            "style_profile": "references/style_profile.json",
            "strategy_spec": "references/strategy_spec.json",
            "execution_intent": "references/execution_intent.json",
            "token_catalog": "references/token_catalog.json",
            "archetype": "references/archetype.json",
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


def _write_type_specific_files(
    package_root: Path,
    candidate: SkillCandidate,
    package_kind: str,
    *,
    fallback_src_root: Path,
) -> tuple[str, ...]:
    generated: list[str] = []
    style_profile = _wallet_style_profile(candidate)
    archetype = _wallet_archetype(candidate)
    style_profile_payload = _augment_style_profile(style_profile, archetype)
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
    if style_profile_payload is not None:
        references_dir = package_root / "references"
        references_dir.mkdir(parents=True, exist_ok=True)
        _write_json(references_dir / "style_profile.json", style_profile_payload)
        _write_json(references_dir / "strategy_spec.json", strategy_spec or {})
        _write_json(references_dir / "execution_intent.json", execution_intent or {})
        _write_json(references_dir / "token_catalog.json", token_catalog)
        if archetype is not None:
            _write_json(references_dir / "archetype.json", archetype)
        generated.append("references/style_profile.json")
        generated.append("references/strategy_spec.json")
        generated.append("references/execution_intent.json")
        generated.append("references/token_catalog.json")
        if archetype is not None:
            generated.append("references/archetype.json")
        wrapper_body = [
            "from __future__ import annotations",
            "",
            "import json",
            "from pathlib import Path",
            "import sys",
            "",
            "",
            f"PROFILE = json.loads({repr(json.dumps(_json_safe(style_profile_payload), ensure_ascii=False))})",
            f"STRATEGY = json.loads({repr(json.dumps(_json_safe(strategy_spec or {}), ensure_ascii=False))})",
            f"EXECUTION_INTENT = json.loads({repr(json.dumps(_json_safe(execution_intent or {}), ensure_ascii=False))})",
            f"TOKEN_CATALOG = json.loads({repr(json.dumps(_json_safe(token_catalog), ensure_ascii=False))})",
            f"ARCHETYPE = json.loads({repr(json.dumps(_json_safe(archetype or {}), ensure_ascii=False))})",
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
            "def main() -> int:",
            "    context = _load_context()",
            "    project_root = Path(__file__).resolve().parents[3]",
            "    source_roots = [",
            "        project_root / 'src',",
            f"        Path({json.dumps(str(fallback_src_root.resolve()), ensure_ascii=False)}),",
            "    ]",
            "    for source_root in source_roots:",
            "        if source_root.is_dir():",
            "            resolved = str(source_root.resolve())",
            "            if resolved not in sys.path:",
            "                sys.path.insert(0, resolved)",
            "    from ot_skill_enterprise.skills_compiler.wallet_style_runtime import build_primary_payload",
            "    payload = build_primary_payload(",
            f"        summary={json.dumps(candidate.change_summary, ensure_ascii=False)},",
            "        profile=PROFILE,",
            "        strategy=STRATEGY,",
            "        execution_intent=EXECUTION_INTENT,",
            "        token_catalog=TOKEN_CATALOG,",
            "        context=context,",
            "        archetype=ARCHETYPE,",
            "    )",
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
            "    source_roots = [",
            "        project_root / 'src',",
            f"        Path({json.dumps(str(fallback_src_root.resolve()), ensure_ascii=False)}),",
            "    ]",
            "    for source_root in source_roots:",
            "        if source_root.is_dir():",
            "            resolved = str(source_root.resolve())",
            "            if resolved not in sys.path:",
            "                sys.path.insert(0, resolved)",
            "    from ot_skill_enterprise.env_bootstrap import load_local_env",
            "    from ot_skill_enterprise.execution import prepare_only_result, run_dry_run, run_live",
            "    load_local_env()",
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
            "        result = prepare_only_result(trade_plan, execution_intent, project_root=project_root)",
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
            "        'trade_plan': result.get('trade_plan') or trade_plan,",
            "        'prepared_execution': result.get('prepared_execution'),",
            "        'checks': result.get('checks'),",
            "        'execution_result': result.get('execution'),",
            "        'approval_required': result.get('approval_required'),",
            "        'approval_result': result.get('approval_result'),",
            "        'simulation_result': result.get('simulation_result'),",
            "        'broadcast_results': result.get('broadcast_results'),",
            "        'tx_hashes': result.get('tx_hashes'),",
            "        'live_cap_usd': result.get('live_cap_usd'),",
            "        'executed_leg_count': result.get('executed_leg_count'),",
            "        'artifacts': [],",
            "        'metadata': {'skill_family': 'wallet_style', **dict(result.get('metadata') or {})},",
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
        generated_files.extend(
            _write_type_specific_files(
                package_root,
                normalized,
                resolved_kind,
                fallback_src_root=_MODULE_SRC_ROOT,
            )
        )

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
