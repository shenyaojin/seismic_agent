# CSCI 576 – Seismic Agent MAS: Project Overview

## 1  Motivation

Geophysical interpretation of seismic data is labour-intensive and requires
expertise across multiple disciplines: data loading, quality-control, rock-physics
analysis, forward modelling, inversion, and report writing. A Multi-Agent System
(MAS) can decompose this workflow into specialised, autonomous agents that
collaborate through a shared event bus, enabling scalable and auditable analysis.

## 2  Problem Statement

Given one or more seismic velocity model files (SEGY format), the system must:

1. Understand a plain-English analyst query.
2. Locate the appropriate dataset on disk.
3. Run the correct geophysical analysis pipeline.
4. Verify that results are physically plausible.
5. Produce a PDF report with figures, metrics, and LLM-generated interpretation.

## 3  System Overview

The Seismic Agent MAS uses Google Gemini 2.5 Flash as its LLM backbone and is
structured around a central `Workspace` event bus.  Five specialised agents each
subscribe to one or more signals and emit downstream signals upon completion.

### Signal Chain

```
User Query
    │
    ▼
ManagerAgent ─── DATA_READY ───────────────────────────────►
                                                            │
                                          AnalysisAgent ◄──┘
                                                │
                                    ANALYSIS_COMPLETE
                                                │
                                   VerifierAgent ◄──────────
                                                │
                                 VERIFICATION_COMPLETE
                                       │            │
                              ReporterAgent    LaTeXReporterAgent
                                 (.md)              (.pdf)
```

## 4  Dataset

**Marmousi2 synthetic model** — a 13,601 × 2,801 grid at 1.25 m spacing
representing a complex faulted anticline.  Two files are used:

| File | Content |
|------|---------|
| `MODEL_P-WAVE_VELOCITY_1.25m.segy.tar.gz` | Vp model (m/s) |
| `MODEL_S-WAVE_VELOCITY_1.25m.segy.tar.gz` | Vs model (m/s) |

## 5  Key Results

| Metric | Value |
|--------|-------|
| Vp mean | ~2 700 m/s |
| Vs mean | ~1 100 m/s |
| Vp/Vs mean | ~2.49 |
| Poisson ratio mean | ~0.365 |
| Acoustic impedance range | 2.4 – 10.8 MRayl |
| Verifier confidence | ≥ 0.85 |
| PDF report generated | Yes |

## 6  Contributions

| Component | Description |
|-----------|-------------|
| ManagerAgent | LLM-driven query parsing + dataset discovery |
| AnalysisAgent | Executes configurable tool-chain pipelines |
| VerifierAgent | Domain guardrails + LLM cross-check + confidence score |
| ReporterAgent | Markdown report generation |
| LaTeXReporterAgent | Compiled PDF report via pdflatex |
| ToolCallLogger | Per-tool latency and token logging (JSONL) |
| SeismicGuardrails | 7 physical and semantic safety rules |
| CostTracker | Token counting and USD estimation via `usage_metadata` |
| Evaluation Framework | 25-case batch runner + Markdown report |
| Streamlit GUI | Interactive velocity-model viewer + run dashboard |

## 7  Technologies

Python 3.11 · Google Generative AI SDK (`google-genai`) · LangSmith tracing ·
segyio · NumPy · Matplotlib · Streamlit · Plotly · python-pptx · pdflatex
