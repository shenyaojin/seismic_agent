"""
ToolCallLogger: Per-tool-call audit log for the seismic analysis pipeline.

Writes one JSON line per tool invocation to `tool_calls.jsonl` (append mode),
and accumulates records in ctx.metrics["tool_call_log"] for in-session reporting.
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

from framework.tools.base import ToolResult


@dataclass
class ToolCallRecord:
    tool_name: str
    mission_id: str
    start_time: str       # ISO8601 UTC
    end_time: str         # ISO8601 UTC
    duration_ms: float
    success: bool
    summary: str
    error: Optional[str]


class ToolCallLogger:
    """
    Creates `on_tool_start` / `on_tool_done` callbacks for ToolChain.execute().

    Usage:
        logger = ToolCallLogger()
        on_start, on_done = logger.make_callbacks(mission_id, ctx.metrics)
        chain.execute(ctx, on_tool_start=on_start, on_tool_done=on_done)
    """

    def __init__(self, log_path: str = "tool_calls.jsonl"):
        self.log_path = Path(log_path)
        self._starts: Dict[str, float] = {}   # tool_name → wall-clock start (time.time())

    def make_callbacks(
        self,
        mission_id: str,
        ctx_metrics: Dict[str, Any],
    ) -> Tuple[Callable[[str], None], Callable[[ToolResult], None]]:
        """Return (on_start, on_done) closures ready for ToolChain.execute()."""

        def on_start(tool_name: str) -> None:
            self._starts[tool_name] = time.time()

        def on_done(result: ToolResult) -> None:
            t_end = time.time()
            t_start = self._starts.pop(result.tool_name, t_end)
            duration_ms = (t_end - t_start) * 1000

            now_utc = datetime.now(timezone.utc)
            start_iso = datetime.fromtimestamp(t_start, tz=timezone.utc).isoformat()
            end_iso = now_utc.isoformat()

            record = ToolCallRecord(
                tool_name=result.tool_name,
                mission_id=mission_id,
                start_time=start_iso,
                end_time=end_iso,
                duration_ms=round(duration_ms, 2),
                success=result.success,
                summary=result.summary[:200],
                error=result.error,
            )

            # 1. Append to .jsonl file
            try:
                with open(self.log_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(asdict(record)) + "\n")
            except Exception:
                pass   # log failure must never crash the pipeline

            # 2. Accumulate in ctx.metrics for in-session reporting
            if "tool_call_log" not in ctx_metrics:
                ctx_metrics["tool_call_log"] = []
            ctx_metrics["tool_call_log"].append(asdict(record))

        return on_start, on_done
