# System Architecture

## Workspace (Event Bus + State Machine)

`framework/workspace.py`

The `Workspace` class is the backbone of the MAS.  It holds a single `MissionState`
dataclass and exposes a publish/subscribe API.

```
MissionSignal (enum)
  MISSION_CREATED
  DATA_READY
  ANALYSIS_COMPLETE
  VERIFICATION_COMPLETE
  REPORT_GENERATED
  LATEX_REPORT_GENERATED
  MISSION_FAILED

MissionState (dataclass)
  mission_id : str          – random hex token
  description : str         – original user query
  plan : dict               – LLM-parsed intent
  data_paths : List[str]    – resolved file paths
  analysis_results : dict   – metrics, figures, verification record
  report_path : str         – path to saved Markdown report
  status : str              – last known status string
```

Every state transition is appended to `mission_log.json` for full auditability.

## File Layout

```
seismic_agent/
├── main.py                     entry-point (CLI run)
├── app.py                      Streamlit GUI
├── generate_materials.py       this script
├── data_seismic/               SEGY velocity models
├── outputs/                    figures, Markdown & PDF reports
├── docs/                       generated deliverables (this folder)
├── eval/
│   ├── test_cases.json         25 evaluation cases
│   ├── run_eval.py             batch runner
│   └── report.py              Markdown report generator
└── framework/
    ├── workspace.py
    ├── guardrails.py
    ├── cost_tracker.py
    ├── tool_call_logger.py
    ├── agents/
    │   ├── base.py
    │   ├── manager.py
    │   ├── analysis.py
    │   ├── verifier.py
    │   ├── reporter.py
    │   └── latex_reporter.py
    └── tools/
        ├── base.py             ToolContext, ToolResult, SeismicTool ABC
        ├── segy_loader.py
        ├── velocity_analysis.py
        ├── forward_modeling.py
        ├── fwi.py
        ├── llm_summary.py
        └── tool_chain.py      ToolChain + ANALYSIS_PIPELINES registry
```

## Design Patterns

| Pattern | Where used |
|---------|-----------|
| Observer (pub/sub) | Workspace.emit / subscribe |
| Chain of Responsibility | ToolChain sequential pipeline |
| Strategy | ANALYSIS_PIPELINES factory dict |
| Proxy | CostTracker._TrackedClient wraps genai.Client |
| Template Method | SeismicTool ABC (run()) |

## LangSmith Tracing

The `genai.Client` is wrapped with `langsmith.wrappers.wrap_gemini` before being
passed to any agent, giving full LLM call tracing, span tagging, and latency
visibility in the LangSmith dashboard at no code cost.
