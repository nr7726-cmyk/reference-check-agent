from __future__ import annotations

from dataclasses import dataclass


@dataclass
class AIDiagnostics:
    fallback_count: int = 0
    quota_limit_reached: int = 0

    def fallback(self) -> None:
        self.fallback_count += 1

    def quota_reached(self) -> None:
        self.quota_limit_reached += 1


AI_DIAGNOSTICS = AIDiagnostics()
