"""
Base classes for the seismic analysis tool chain.

Each tool receives a ToolContext (shared mutable dict) and returns a ToolResult.
Tools are composable: the output of one feeds the next via the context.
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class ToolContext:
    """Shared state flowing through the pipeline."""
    files: List[Path] = field(default_factory=list)
    analysis_type: str = "general"
    outputs_dir: Path = Path("outputs")

    # Populated by tools as the pipeline progresses
    velocity_models: Dict[str, Any] = field(default_factory=dict)   # {"vp": ndarray, "vs": ndarray}
    forward_data: Dict[str, Any] = field(default_factory=dict)      # {"shot_records": [...], "geometry": {...}}
    inversion_results: Dict[str, Any] = field(default_factory=dict) # {"recovered_vp": ndarray, "residuals": [...]}
    figures: List[str] = field(default_factory=list)                 # saved figure paths
    metrics: Dict[str, Any] = field(default_factory=dict)           # numerical results for LLM


@dataclass
class ToolResult:
    success: bool
    tool_name: str
    summary: str
    data: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None


class SeismicTool(ABC):
    """
    Abstract base for all seismic analysis tools.

    Subclass and implement `run(ctx)`. Tools MUST:
      - Read what they need from `ctx`
      - Write their outputs back into `ctx`
      - Return a ToolResult describing what happened
    """

    def __init__(self, name: str):
        self.name = name
        self.logger = logging.getLogger(name)

    @abstractmethod
    def run(self, ctx: ToolContext) -> ToolResult:
        """Execute the tool, mutate ctx, return a result."""
        ...

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(name={self.name!r})"
