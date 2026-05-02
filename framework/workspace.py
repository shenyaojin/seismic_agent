import json
import logging
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional


class MissionSignal(Enum):
    MISSION_CREATED = "MISSION_CREATED"
    DATA_LOCATED = "DATA_LOCATED"
    DATA_READY = "DATA_READY"
    ANALYSIS_READY = "ANALYSIS_READY"
    ANALYSIS_COMPLETE = "ANALYSIS_COMPLETE"
    REPORT_GENERATED = "REPORT_GENERATED"
    LATEX_REPORT_GENERATED = "LATEX_REPORT_GENERATED"
    VERIFICATION_COMPLETE = "VERIFICATION_COMPLETE"
    MISSION_FAILED = "MISSION_FAILED"


@dataclass
class MissionState:
    mission_id: str
    description: str
    plan: Optional[Dict[str, Any]] = None
    data_paths: List[str] = field(default_factory=list)
    analysis_results: Dict[str, Any] = field(default_factory=dict)
    report_path: Optional[str] = None
    status: str = "INITIALIZED"


class Workspace:
    """
    Central state management and event bus for the Multi-Agent System.
    """

    def __init__(self, log_path: str = "mission_log.json"):
        self.state: Optional[MissionState] = None
        self.subscribers: Dict[MissionSignal, List[Callable[[MissionSignal, Any], None]]] = {
            signal: [] for signal in MissionSignal
        }
        self.log_path = Path(log_path)
        self._setup_logging()

    def _setup_logging(self):
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] %(message)s",
            handlers=[logging.StreamHandler()],
        )
        self.logger = logging.getLogger("Workspace")

    def set_state(self, state: MissionState):
        self.state = state
        self._log_transition("STATE_INITIALIZED")

    def update_state(self, **kwargs):
        if not self.state:
            raise RuntimeError("MissionState not initialized.")
        for key, value in kwargs.items():
            if hasattr(self.state, key):
                setattr(self.state, key, value)
            else:
                self.logger.warning(f"Unknown state attribute: {key}")
        self._log_transition(f"STATE_UPDATED: {list(kwargs.keys())}")

    def subscribe(self, signal: MissionSignal, callback: Callable[[MissionSignal, Any], None]):
        self.subscribers[signal].append(callback)
        self.logger.info(f"Agent subscribed to {signal.value}")

    def emit(self, signal: MissionSignal, data: Any = None):
        self.logger.info(f"Signal Emitted: {signal.value}")
        self._log_transition(f"SIGNAL_EMITTED: {signal.value}")
        for callback in self.subscribers[signal]:
            callback(signal, data)

    def _log_transition(self, event_type: str):
        log_entry = {
            "event": event_type,
            "state": asdict(self.state) if self.state else None,
        }
        
        # Append to mission_log.json
        try:
            logs = []
            if self.log_path.exists():
                with open(self.log_path, "r") as f:
                    logs = json.load(f)
            
            logs.append(log_entry)
            
            with open(self.log_path, "w") as f:
                json.dump(logs, f, indent=4)
        except Exception as e:
            self.logger.error(f"Failed to write mission log: {e}")
