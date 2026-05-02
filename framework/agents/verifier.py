"""
VerifierAgent: Critic / quality-control agent for the seismic analysis pipeline.

Subscribes to ANALYSIS_COMPLETE.
Runs guardrail checks + LLM cross-check, computes a confidence score,
optionally retries the LLM summary, then emits VERIFICATION_COMPLETE
so downstream reporters can proceed.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from google import genai

from framework.workspace import Workspace, MissionSignal
from framework.agents.base import BaseAgent
from framework.guardrails import SeismicGuardrails


class VerifierAgent(BaseAgent):
    """
    Critic agent: validates analysis outputs before reports are generated.

    Confidence scoring:
      - Start at 1.0
      - Deduct 0.15 per guardrail violation
      - Deduct 0.20 if LLM cross-check reports issues
      - Floor at 0.0

    If confidence < CONFIDENCE_THRESHOLD, re-runs the LLM summary once
    with a corrective prompt that lists the detected violations.
    """

    CONFIDENCE_THRESHOLD: float = 0.6
    MAX_RETRIES: int = 1

    def __init__(
        self,
        name: str,
        workspace: Workspace,
        client: genai.Client,
        guardrails: Optional[SeismicGuardrails] = None,
    ):
        self.guardrails = guardrails or SeismicGuardrails()
        self._retry_count: int = 0
        super().__init__(name, workspace, client)

    def _setup_subscriptions(self) -> None:
        self.workspace.subscribe(MissionSignal.ANALYSIS_COMPLETE, self.handle_signal)

    def handle_signal(self, signal: MissionSignal, data: Any = None) -> None:
        if signal == MissionSignal.ANALYSIS_COMPLETE:
            self.logger.info("VerifierAgent triggered.")
            self._retry_count = 0
            self._run_verification(data or {})

    # ------------------------------------------------------------------ #
    # Main verification flow                                               #
    # ------------------------------------------------------------------ #

    def _run_verification(self, analysis_data: Dict[str, Any]) -> None:
        state = self.workspace.state
        if not state:
            self.logger.error("No workspace state; skipping verification.")
            self.workspace.emit(MissionSignal.VERIFICATION_COMPLETE, data=analysis_data)
            return

        analysis_results = state.analysis_results or {}
        metrics = analysis_results.get("metrics", {})
        summary = metrics.get("llm_summary", "")
        loaded_files: List[str] = analysis_results.get("files_processed", [])

        # Step 1: Guardrail rule checks
        guardrail_result = self.guardrails.run_all(metrics, summary, loaded_files)
        if guardrail_result.violations:
            for v in guardrail_result.violations:
                self.logger.warning(f"[Guardrail] {v}")
        else:
            self.logger.info("[Guardrail] All checks passed.")

        # Step 2: LLM cross-check
        llm_check = self._llm_crosscheck(metrics, summary)
        if not llm_check.get("passed", True):
            for issue in llm_check.get("issues", []):
                self.logger.warning(f"[LLM-Check] {issue}")
        else:
            self.logger.info("[LLM-Check] Summary consistent with metrics.")

        # Step 3: Confidence score
        confidence = self._compute_confidence(
            len(guardrail_result.violations), llm_check
        )
        self.logger.info(f"Confidence score: {confidence:.2f}")

        # Step 4: Retry if low confidence
        if confidence < self.CONFIDENCE_THRESHOLD and self._retry_count < self.MAX_RETRIES:
            self._retry_count += 1
            self.logger.warning(
                f"Confidence {confidence:.2f} below threshold "
                f"{self.CONFIDENCE_THRESHOLD}. Retrying LLM summary."
            )
            new_summary = self._retry_llm_summary(metrics, guardrail_result.violations)
            if new_summary:
                metrics["llm_summary"] = new_summary
                summary = new_summary
                # Re-evaluate with corrected summary
                guardrail_result = self.guardrails.run_all(metrics, summary, loaded_files)
                llm_check = self._llm_crosscheck(metrics, summary)
                confidence = self._compute_confidence(
                    len(guardrail_result.violations), llm_check
                )
                self.logger.info(f"Post-retry confidence score: {confidence:.2f}")

        # Step 5: Persist verification record in workspace state
        verification_record: Dict[str, Any] = {
            "guardrail_passed": guardrail_result.passed,
            "violations": guardrail_result.violations,
            "llm_check_passed": llm_check.get("passed", True),
            "llm_check_issues": llm_check.get("issues", []),
            "confidence_score": round(confidence, 4),
            "retried": self._retry_count > 0,
        }

        updated_results = {**analysis_results, "verification": verification_record}
        # Also propagate possibly-updated summary back
        if "metrics" in updated_results:
            updated_results["metrics"] = metrics

        self.workspace.update_state(
            analysis_results=updated_results,
            status="VERIFICATION_COMPLETE",
        )

        # Step 6: Emit VERIFICATION_COMPLETE, transparently forwarding all data
        emit_data = {**analysis_data, "verification": verification_record}
        # Ensure reporters still get the (possibly corrected) summary + figures
        emit_data["insights"] = summary
        self.workspace.emit(MissionSignal.VERIFICATION_COMPLETE, data=emit_data)

    # ------------------------------------------------------------------ #
    # Helpers                                                              #
    # ------------------------------------------------------------------ #

    def _llm_crosscheck(self, metrics: Dict[str, Any], summary: str) -> Dict[str, Any]:
        """Ask Gemini to flag factual inconsistencies between metrics and summary."""
        metrics_str = json.dumps(
            {k: v for k, v in metrics.items()
             if k not in ("llm_summary", "tool_call_log", "_correction_note")},
            indent=2,
            default=str,
        )
        prompt = f"""You are a quality-control reviewer for a geophysical analysis system.

## Numerical metrics produced by the analysis pipeline:
```json
{metrics_str}
```

## LLM-generated summary to review:
{summary}

Identify factual inconsistencies between the metrics and the summary
(e.g., the summary claims a velocity range that contradicts the numbers,
or mentions parameters that were never computed).

Respond ONLY as JSON: {{"passed": true, "issues": []}}
or {{"passed": false, "issues": ["description of issue 1", ...]}}
"""
        try:
            response = self.client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
                config={"response_mime_type": "application/json"},
            )
            return json.loads(response.text)
        except Exception as e:
            # Fail-open: if the LLM call fails, do not block the pipeline
            self.logger.error(f"LLM cross-check failed: {e}")
            return {"passed": True, "issues": []}

    def _compute_confidence(
        self, n_violations: int, llm_check: Dict[str, Any]
    ) -> float:
        penalty = n_violations * 0.15
        if not llm_check.get("passed", True):
            penalty += 0.20
        return max(0.0, 1.0 - penalty)

    def _retry_llm_summary(
        self, metrics: Dict[str, Any], violations: List[str]
    ) -> Optional[str]:
        """Re-run LLMSummaryTool with a corrective note injected into the context."""
        from framework.tools.base import ToolContext
        from framework.tools.llm_summary import LLMSummaryTool

        violation_str = "\n".join(f"- {v}" for v in violations)
        correction_note = (
            "CORRECTION REQUIRED: The previous summary had the following issues:\n"
            f"{violation_str}\n\n"
            "Rewrite the summary strictly based on the numerical metrics provided. "
            "Do not fabricate numbers or make claims contradicting the data."
        )

        tmp_ctx = ToolContext()
        tmp_ctx.metrics = {
            k: v for k, v in metrics.items()
            if k not in ("llm_summary", "_correction_note")
        }
        tmp_ctx.metrics["_correction_note"] = correction_note
        tmp_ctx.analysis_type = "corrected summary"

        tool = LLMSummaryTool(self.client)
        result = tool.run(tmp_ctx)
        if result.success:
            # Clean up the injected key before returning
            tmp_ctx.metrics.pop("_correction_note", None)
            return tmp_ctx.metrics.get("llm_summary")
        return None
