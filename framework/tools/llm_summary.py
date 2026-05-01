"""
LLMSummaryTool: Synthesises all numerical metrics via Gemini into a professional summary.

Reads ctx.metrics and ctx.figures, returns a rich technical narrative
that the ReporterAgent will embed in the final Markdown report.
"""
from __future__ import annotations

import json

from google import genai

from .base import SeismicTool, ToolContext, ToolResult


class LLMSummaryTool(SeismicTool):
    """
    Calls Gemini with all accumulated numerical metrics and figure paths
    to produce a cohesive geophysical interpretation.
    """

    def __init__(self, client: genai.Client, model: str = "gemini-2.5-flash"):
        super().__init__("LLMSummary")
        self.client = client
        self.model = model

    def run(self, ctx: ToolContext) -> ToolResult:
        metrics_str = json.dumps(ctx.metrics, indent=2, default=str)
        figures_str = "\n".join(ctx.figures) if ctx.figures else "None"

        prompt = f"""
You are a senior geophysicist reviewing the automated analysis of the Marmousi synthetic dataset.

## Numerical results from the analysis pipeline:
```json
{metrics_str}
```

## Figures generated:
{figures_str}

## Analysis type requested: {ctx.analysis_type}

Write a professional technical summary (400–600 words) that:
1. Interprets the velocity statistics in a petrophysical/lithological context.
2. Comments on the Vp/Vs ratio and Poisson's ratio implications for fluid content or lithology.
3. Describes the forward modelling outcome (if present) and what the synthetic data quality implies.
4. Interprets the FWI convergence and velocity error metrics (if present).
5. States clear, actionable geophysical conclusions.

Use academic language. Do NOT repeat the raw numbers verbatim — interpret them.
"""

        try:
            response = self.client.models.generate_content(
                model=self.model,
                contents=prompt,
            )
            insights = response.text
        except Exception as e:
            self.logger.error(f"LLM summary failed: {e}")
            return ToolResult(False, self.name, "LLM summary failed.", error=str(e))

        ctx.metrics["llm_summary"] = insights
        return ToolResult(True, self.name, "LLM summary generated.", data={"summary": insights})
