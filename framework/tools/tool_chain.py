"""
ToolChain: Composable pipeline of SeismicTool instances.

Usage
-----
chain = ToolChain([SEGYLoaderTool(), VelocityAnalysisTool(), LLMSummaryTool(client)])
ctx   = ToolContext(files=..., analysis_type=..., outputs_dir=...)
results = chain.execute(ctx)

Preset pipelines (keyed by analysis_type) are defined in ANALYSIS_PIPELINES.
The AnalysisAgent resolves the right pipeline automatically.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable, Dict, List

from google import genai

from .base import SeismicTool, ToolContext, ToolResult
from .segy_loader import SEGYLoaderTool
from .velocity_analysis import VelocityAnalysisTool
from .forward_modeling import ForwardModelingTool
from .fwi import FWITool
from .llm_summary import LLMSummaryTool


logger = logging.getLogger("ToolChain")


class ToolChain:
    """
    Sequential pipeline of SeismicTools.

    Each tool sees the same ToolContext and enriches it.
    If a tool fails and `stop_on_failure=True`, the pipeline halts.
    """

    def __init__(self, tools: List[SeismicTool], stop_on_failure: bool = False):
        self.tools = tools
        self.stop_on_failure = stop_on_failure

    def execute(self, ctx: ToolContext) -> List[ToolResult]:
        results: List[ToolResult] = []
        for tool in self.tools:
            logger.info(f"Running tool: {tool.name}")
            try:
                result = tool.run(ctx)
            except Exception as exc:
                result = ToolResult(False, tool.name, str(exc), error=str(exc))
                logger.error(f"Tool {tool.name} raised: {exc}", exc_info=True)

            results.append(result)
            logger.info(f"  → {'OK' if result.success else 'FAIL'}: {result.summary[:120]}")

            if not result.success and self.stop_on_failure:
                logger.warning("Stopping pipeline on failure.")
                break

        return results

    def add_tool(self, tool: SeismicTool) -> "ToolChain":
        """Append a tool to the pipeline (fluent API)."""
        self.tools.append(tool)
        return self


# ---------------------------------------------------------------------------
# Preset pipelines
# Factory functions take a genai.Client so tools that need it can be created.
# ---------------------------------------------------------------------------

PipelineFactory = Callable[[genai.Client], ToolChain]

def _pipeline_velocity_analysis(client: genai.Client) -> ToolChain:
    return ToolChain([
        SEGYLoaderTool(),
        VelocityAnalysisTool(),
        LLMSummaryTool(client),
    ])


def _pipeline_forward_modeling(client: genai.Client) -> ToolChain:
    return ToolChain([
        SEGYLoaderTool(),
        VelocityAnalysisTool(),
        ForwardModelingTool(n_shots=3, t_max=1.5),
        LLMSummaryTool(client),
    ])


def _pipeline_fwi(client: genai.Client) -> ToolChain:
    return ToolChain([
        SEGYLoaderTool(),
        VelocityAnalysisTool(),
        ForwardModelingTool(n_shots=3, t_max=1.5),
        FWITool(n_iterations=5),
        LLMSummaryTool(client),
    ])


def _pipeline_general(client: genai.Client) -> ToolChain:
    """Default fallback: velocity analysis + LLM summary."""
    return _pipeline_velocity_analysis(client)


# Mapping from analysis_type keywords → pipeline factory
# Add new entries here to extend the system.
ANALYSIS_PIPELINES: Dict[str, PipelineFactory] = {
    "porosity prediction":          _pipeline_velocity_analysis,
    "lithology classification":     _pipeline_velocity_analysis,
    "velocity analysis":            _pipeline_velocity_analysis,
    "structural interpretation":    _pipeline_velocity_analysis,
    "forward modeling":             _pipeline_forward_modeling,
    "synthetic seismic":            _pipeline_forward_modeling,
    "full waveform inversion":      _pipeline_fwi,
    "fwi":                          _pipeline_fwi,
    "inversion":                    _pipeline_fwi,
    "general":                      _pipeline_general,
}


def resolve_pipeline(analysis_type: str, client: genai.Client) -> ToolChain:
    """
    Return the best-matching ToolChain for a given analysis_type string.
    Falls back to the 'general' pipeline if no keyword matches.
    """
    analysis_lower = analysis_type.lower().strip()

    # Exact match
    if analysis_lower in ANALYSIS_PIPELINES:
        return ANALYSIS_PIPELINES[analysis_lower](client)

    # Substring match
    for key, factory in ANALYSIS_PIPELINES.items():
        if key in analysis_lower or analysis_lower in key:
            logger.info(f"Pipeline resolved via substring match: '{key}'")
            return factory(client)

    logger.warning(f"No pipeline for '{analysis_type}', using general.")
    return ANALYSIS_PIPELINES["general"](client)
