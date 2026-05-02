from pathlib import Path
from typing import Any
from google import genai

from framework.workspace import Workspace, MissionSignal
from framework.agents.base import BaseAgent
from framework.tools import ToolChain, ToolContext, resolve_pipeline
from framework.tool_call_logger import ToolCallLogger


class AnalysisAgent(BaseAgent):
    """
    Analysis Agent: Triggered by DATA_READY.

    Resolves the appropriate ToolChain for the requested analysis_type,
    runs the pipeline, and emits ANALYSIS_COMPLETE with the LLM summary.
    """

    def __init__(self, name: str, workspace: Workspace, client: genai.Client,
                 outputs_dir: str = "outputs/"):
        self.outputs_dir = Path(outputs_dir).resolve()
        self.tool_logger = ToolCallLogger()
        super().__init__(name, workspace, client)

    def _setup_subscriptions(self):
        self.workspace.subscribe(MissionSignal.DATA_READY, self.handle_signal)

    def handle_signal(self, signal: MissionSignal, data: Any = None):
        if signal == MissionSignal.DATA_READY:
            files = data.get("files", [])
            analysis_type = data.get("analysis", "general")
            self.logger.info(f"Analysis Agent triggered: '{analysis_type}' on {len(files)} file(s).")
            self.run_analysis(files, analysis_type)

    def run_analysis(self, files: list, analysis_type: str):
        # Build shared context
        ctx = ToolContext(
            files=[Path(f) if not isinstance(f, Path) else f for f in files],
            analysis_type=analysis_type,
            outputs_dir=self.outputs_dir,
        )

        # Select and run the pipeline
        chain: ToolChain = resolve_pipeline(analysis_type, self.client)
        self.logger.info(f"Pipeline: {[t.name for t in chain.tools]}")

        mission_id = self.workspace.state.mission_id if self.workspace.state else "unknown"
        on_start, on_done = self.tool_logger.make_callbacks(mission_id, ctx.metrics)
        tool_results = chain.execute(ctx, on_tool_start=on_start, on_tool_done=on_done)

        # Collect summary from all tools
        run_log = "\n".join(
            f"[{'OK' if r.success else 'FAIL'}] {r.tool_name}: {r.summary}"
            for r in tool_results
        )
        self.logger.info(f"Pipeline complete.\n{run_log}")

        # Primary insight: LLM summary if available, else concatenate tool summaries
        insights = ctx.metrics.get("llm_summary") or run_log

        self.workspace.update_state(
            analysis_results={
                "type": analysis_type,
                "insights": insights,
                "files_processed": [str(f) for f in ctx.files],
                "figures": ctx.figures,
                "metrics": ctx.metrics,
                "tool_log": run_log,
            },
            status="ANALYSIS_COMPLETED",
        )

        self.workspace.emit(
            MissionSignal.ANALYSIS_COMPLETE,
            data={"insights": insights, "figures": ctx.figures},
        )
