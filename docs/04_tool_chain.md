# Tool Chain

## ToolContext (shared state)

```python
@dataclass
class ToolContext:
    files: List[Path]           # input SEGY files
    analysis_type: str          # e.g. "porosity prediction"
    outputs_dir: Path           # where to save figures

    velocity_models: dict       # {"vp": ndarray, "vs": ndarray}
    forward_data: dict          # {"shot_records": [...], "geometry": {...}}
    inversion_results: dict     # {"recovered_vp": ndarray, "residuals": [...]}
    figures: List[str]          # saved figure absolute paths
    metrics: dict               # all numerical outputs + "llm_summary"
```

## Tools

### SEGYLoaderTool
Loads Vp and Vs velocity models from SEGY / gzip-SEGY files using `segyio`.
- Handles both IBM float and IEEE float byte orders.
- Applies a velocity-range sanity check (rejects data outside 100–12000 m/s).
- Prioritises `MODEL_P-WAVE` / `MODEL_S-WAVE` files over bare `Vp/Vs` files.
- Saves a dual-panel velocity model figure (PNG) with downsampled display grid.

### VelocityAnalysisTool
Computes descriptive statistics over the loaded velocity models:
- Vp, Vs: min / max / mean / std / percentiles (P10, P50, P90)
- Vp/Vs ratio and Poisson's ratio (element-wise, then summarised)
- Acoustic Impedance (Vp × density proxy)

### ForwardModelingTool
Runs a 2-D acoustic finite-difference forward model:
- Configurable number of shots and record length (`n_shots`, `t_max`)
- Saves synthetic shot-record plots and an animated gather

### FWITool
Adjoint-state Full Waveform Inversion:
- Perturbs the true model, then iteratively minimises the misfit between
  observed (true) and predicted (perturbed) shot gathers.
- Reports per-iteration misfit, final velocity error, and relative improvement.

### LLMSummaryTool
Sends all `ctx.metrics` (JSON-serialised) to Gemini 2.5 Flash with a structured
prompt requesting a 400–600-word professional geophysical interpretation.
Writes the result to `ctx.metrics["llm_summary"]`.

## Pipeline Registry

| Key | Pipeline |
|-----|---------|
| `velocity analysis` | SEGYLoader → VelocityAnalysis → LLMSummary |
| `porosity prediction` | (same as above) |
| `lithology classification` | (same as above) |
| `structural interpretation` | (same as above) |
| `forward modeling` | SEGYLoader → VelocityAnalysis → ForwardModeling → LLMSummary |
| `synthetic seismic` | (same as above) |
| `full waveform inversion` | SEGYLoader → VelocityAnalysis → ForwardModeling → FWI → LLMSummary |
| `fwi` / `inversion` | (same as above) |
| `general` | (velocity analysis fallback) |

## ToolCallLogger

`framework/tool_call_logger.py`

Records every tool invocation to `tool_calls.jsonl` (one JSON object per line,
appended — never overwritten):

```json
{
  "tool_name": "SEGYLoader",
  "mission_id": "mission_e97ea94c",
  "start_time": "2026-05-02T15:16:32.481",
  "end_time":   "2026-05-02T15:16:33.348",
  "duration_ms": 866.8,
  "success": true,
  "summary": "Loaded Vp (13601×2801) and Vs (13601×2801).",
  "error": null
}
```

Also writes the log into `ctx.metrics["tool_call_log"]` so it appears in the
generated report.

## CostTracker

`framework/cost_tracker.py`

Wraps `genai.Client.models` via a transparent proxy and accumulates
`response.usage_metadata.prompt_token_count` / `candidates_token_count`.

```python
tracker = CostTracker()
client  = tracker.wrap_client(raw_gemini_client)
# ... run agents ...
print(tracker.summary())
# {'input_tokens': 12400, 'output_tokens': 890, 'call_count': 4, 'estimated_usd': 0.00124}
```

Pricing model: $0.10 / 1M input, $0.40 / 1M output (Gemini 2.5 Flash).
