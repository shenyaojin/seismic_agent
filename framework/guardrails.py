"""
SeismicGuardrails: Domain-specific safety rules for the seismic analysis pipeline.

Disallowed outputs / actions:
  1. Vp outside physical range 500–8000 m/s
  2. Vs outside physical range 0–5000 m/s
  3. Vp ≤ Vs (physically impossible for a solid)
  4. Vp/Vs ratio outside 1.0–6.0
  5. Poisson's ratio outside 0.0–0.5
  6. LLM summary contains financial / medical / legal claims
  7. LLM summary references filenames not present in the loaded dataset
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List


@dataclass
class GuardrailResult:
    passed: bool
    violations: List[str] = field(default_factory=list)


class SeismicGuardrails:
    # ------------------------------------------------------------------ #
    # Physical constants — override in tests via subclass or direct assign #
    # ------------------------------------------------------------------ #
    VP_MIN: float = 500.0
    VP_MAX: float = 8000.0
    VS_MIN: float = 0.0
    VS_MAX: float = 5000.0
    VPVS_MIN: float = 1.0
    VPVS_MAX: float = 6.0
    POISSON_MIN: float = 0.0
    POISSON_MAX: float = 0.5

    # Patterns that indicate out-of-domain claims in the LLM summary
    OFF_DOMAIN_PATTERNS: List[str] = [
        r"\b(invest(?:ment|ing|or)?|stock|portfolio|return on investment|roi)\b",
        r"\b(diagnos(?:is|e|tic)?|treatment|patient|medical|clinical|disease)\b",
        r"\b(legal advice|lawsuit|liability|attorney|court|malpractice)\b",
    ]

    # ------------------------------------------------------------------ #

    def check_velocity_metrics(self, metrics: Dict[str, Any]) -> GuardrailResult:
        """Validate physical plausibility of velocity statistics."""
        violations: List[str] = []

        vp = metrics.get("vp", {})
        vs = metrics.get("vs", {})
        ratio = metrics.get("vp_vs_ratio", {})
        poisson = metrics.get("poisson_ratio", {})

        # Rule 1 & 2: Vp / Vs range
        for key, stat, lo, hi, label in [
            ("min", vp.get("min"), self.VP_MIN, self.VP_MAX, "Vp min"),
            ("max", vp.get("max"), self.VP_MIN, self.VP_MAX, "Vp max"),
            ("mean", vp.get("mean"), self.VP_MIN, self.VP_MAX, "Vp mean"),
            ("min", vs.get("min"), self.VS_MIN, self.VS_MAX, "Vs min"),
            ("max", vs.get("max"), self.VS_MIN, self.VS_MAX, "Vs max"),
            ("mean", vs.get("mean"), self.VS_MIN, self.VS_MAX, "Vs mean"),
        ]:
            if stat is None:
                continue
            if not (lo <= stat <= hi):
                violations.append(
                    f"{label} = {stat:.1f} m/s is outside physical range [{lo}, {hi}]"
                )

        # Rule 3: Vp > Vs (mean as proxy)
        vp_mean = vp.get("mean")
        vs_mean = vs.get("mean")
        if vp_mean is not None and vs_mean is not None and vs_mean > 0:
            if vp_mean <= vs_mean:
                violations.append(
                    f"Vp mean ({vp_mean:.1f}) ≤ Vs mean ({vs_mean:.1f}): physically impossible"
                )

        # Rule 4: Vp/Vs ratio
        ratio_mean = ratio.get("mean")
        if ratio_mean is not None:
            if not (self.VPVS_MIN <= ratio_mean <= self.VPVS_MAX):
                violations.append(
                    f"Vp/Vs ratio mean = {ratio_mean:.2f} outside [{self.VPVS_MIN}, {self.VPVS_MAX}]"
                )

        # Rule 5: Poisson's ratio
        poisson_mean = poisson.get("mean")
        if poisson_mean is not None:
            if not (self.POISSON_MIN <= poisson_mean <= self.POISSON_MAX):
                violations.append(
                    f"Poisson's ratio mean = {poisson_mean:.3f} outside [{self.POISSON_MIN}, {self.POISSON_MAX}]"
                )

        return GuardrailResult(passed=len(violations) == 0, violations=violations)

    def check_llm_summary(
        self, summary: str, loaded_files: List[str]
    ) -> GuardrailResult:
        """Check summary for off-domain language and phantom file references."""
        violations: List[str] = []

        # Rule 6: Off-domain claims
        for pattern in self.OFF_DOMAIN_PATTERNS:
            m = re.search(pattern, summary, re.IGNORECASE)
            if m:
                violations.append(
                    f"Summary contains off-domain language: '{m.group()}'"
                )

        # Rule 7: Phantom file references
        # Extract file-like tokens (anything ending in a known seismic extension)
        found_names = re.findall(
            r"\b[\w.\-]+\.(?:segy|sgy|gz|las|tar)\b", summary, re.IGNORECASE
        )
        loaded_basenames = {Path(p).name.lower() for p in loaded_files}
        for fname in found_names:
            if fname.lower() not in loaded_basenames:
                violations.append(
                    f"Summary references file '{fname}' not in the loaded dataset"
                )

        return GuardrailResult(passed=len(violations) == 0, violations=violations)

    def run_all(
        self,
        metrics: Dict[str, Any],
        summary: str,
        loaded_files: List[str],
    ) -> GuardrailResult:
        """Run all guardrail checks and return a combined result."""
        r1 = self.check_velocity_metrics(metrics)
        r2 = self.check_llm_summary(summary, loaded_files)
        all_violations = r1.violations + r2.violations
        return GuardrailResult(passed=len(all_violations) == 0, violations=all_violations)
