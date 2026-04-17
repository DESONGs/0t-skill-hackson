from .models import ExecutionIntent, StrategyCondition, StrategySpec, StyleDistillationSummary
from .extractors import DEFAULT_EXTRACTION_PROMPT, WalletStyleExtractor

__all__ = [
    "DEFAULT_EXTRACTION_PROMPT",
    "ExecutionIntent",
    "StrategyCondition",
    "StrategySpec",
    "StyleDistillationSummary",
    "WalletStyleDistillationService",
    "WalletStyleExtractor",
    "build_wallet_style_distillation_service",
]


def __getattr__(name: str):
    if name in {"WalletStyleDistillationService", "build_wallet_style_distillation_service"}:
        from .service import WalletStyleDistillationService, build_wallet_style_distillation_service

        exported = {
            "WalletStyleDistillationService": WalletStyleDistillationService,
            "build_wallet_style_distillation_service": build_wallet_style_distillation_service,
        }
        return exported[name]
    raise AttributeError(name)
