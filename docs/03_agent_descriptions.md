# Agent Descriptions

## 1  ManagerAgent

**File:** `framework/agents/manager.py`
**Subscribes to:** *(initiator — no signal subscription)*
**Emits:** `DATA_READY`

### Responsibilities
1. Accept a plain-English query from the user.
2. Call Gemini 2.5 Flash to extract `dataset_keyword` and `analysis_type` (JSON mode).
3. Scan `data_seismic/` for files whose names contain the keyword.
4. If no exact match: ask Gemini to rank available filenames by relevance.
5. If still no match: generate a clarification message and set status to
   `AWAITING_CLARIFICATION`.
6. Otherwise: update `MissionState.data_paths` and emit `DATA_READY`.

---

## 2  AnalysisAgent

**File:** `framework/agents/analysis.py`
**Subscribes to:** `DATA_READY`
**Emits:** `ANALYSIS_COMPLETE`

### Responsibilities
1. Receive files and `analysis_type` from the `DATA_READY` signal.
2. Call `resolve_pipeline(analysis_type, client)` to select the correct `ToolChain`.
3. Execute the pipeline via `chain.execute(ctx, on_tool_start, on_tool_done)` where
   the callbacks are provided by `ToolCallLogger`.
4. Store `metrics`, `figures`, and `llm_summary` in `workspace.state.analysis_results`.
5. Emit `ANALYSIS_COMPLETE` with `{insights, figures}`.

---

## 3  VerifierAgent  *(Critic Agent)*

**File:** `framework/agents/verifier.py`
**Subscribes to:** `ANALYSIS_COMPLETE`
**Emits:** `VERIFICATION_COMPLETE` (or `MISSION_FAILED` if workspace is missing)

### Responsibilities
1. **Guardrail checks** via `SeismicGuardrails.run_all()` — 7 rules covering
   velocity ranges, Vp/Vs ratio, Poisson's ratio, off-domain language, and
   phantom file references.
2. **LLM cross-check** — ask Gemini to flag factual inconsistencies between the
   numerical metrics and the LLM-generated summary.  *Fail-open*: if the LLM call
   fails, the pipeline continues.
3. **Confidence scoring:**
   ```
   confidence = 1.0 - (n_violations × 0.15) - (0.20 if LLM check failed)
   ```
4. If `confidence < 0.6` and `retry_count < 1`: re-run `LLMSummaryTool` with a
   corrective prompt listing detected violations.
5. Persist a `verification` record in `workspace.state.analysis_results`.
6. Forward all data downstream via `VERIFICATION_COMPLETE`.

---

## 4  ReporterAgent

**File:** `framework/agents/reporter.py`
**Subscribes to:** `VERIFICATION_COMPLETE`
**Emits:** `REPORT_GENERATED`

Writes a Markdown report to `outputs/geophysical_report_<ts>.md` containing
mission ID, objective, data sources, figure list, and the (possibly corrected)
LLM insights.

---

## 5  LaTeXReporterAgent

**File:** `framework/agents/latex_reporter.py`
**Subscribes to:** `VERIFICATION_COMPLETE`
**Emits:** `LATEX_REPORT_GENERATED`

1. Builds a full LaTeX document (`_build_tex`) with cover page, TOC, sections for
   metrics table, embedded figures, and the Markdown-converted insights body.
2. Runs `pdflatex` twice (for TOC) in a temp directory.
3. Copies the compiled PDF to `outputs/geophysical_report_<ts>.pdf`.
4. Saves the `.tex` source for debugging if compilation fails.

Markdown → LaTeX conversion handles headers, bold, italic, bullet lists,
numbered lists, code spans, and correctly escapes all LaTeX special characters.
