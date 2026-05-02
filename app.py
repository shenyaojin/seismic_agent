"""
Streamlit GUI for the Seismic Agent Multi-Agent System.

Run with:
    streamlit run app.py
"""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st
from google import genai
from langsmith import wrappers

from framework.workspace import Workspace, MissionSignal, MissionState
from framework.agents.manager import ManagerAgent
from framework.tools import ToolContext, resolve_pipeline
from framework.agents.latex_reporter import LaTeXReporterAgent

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="Seismic Agent MAS",
    page_icon="🌊",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_env() -> None:
    env_path = Path(__file__).parent / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


@st.cache_resource
def _get_client():
    _load_env()
    gemini_client = genai.Client()
    return wrappers.wrap_gemini(
        gemini_client,
        tracing_extra={"tags": ["gemini", "seismic-agent"]},
    )


def _downsample(arr: "np.ndarray", max_h: int = 500, max_w: int = 600) -> "np.ndarray":
    """Downsample a 2-D array to at most max_h × max_w for display."""
    h, w = arr.shape
    sh = max(1, h // max_h)
    sw = max(1, w // max_w)
    return arr[::sh, ::sw]


def _velocity_figure(
    models: dict,
    colorscale: str = "RdYlBu_r",
) -> go.Figure:
    """
    Build a Plotly figure with tabs (buttons) for Vp, Vs, and Vp/Vs ratio.
    Axes are in km. Hover shows depth, distance, and velocity value.
    """
    vp_full = models.get("vp")
    vs_full = models.get("vs")
    dx = models.get("dx", 1.25)
    dz = models.get("dz", 1.25)

    panels = []

    if vp_full is not None:
        vp = _downsample(vp_full)
        nz, nx = vp.shape
        x_km = np.linspace(0, vp_full.shape[1] * dx / 1000, nx)
        z_km = np.linspace(0, vp_full.shape[0] * dz / 1000, nz)
        panels.append(("Vp (m/s)", vp, x_km, z_km, colorscale, None, None))

    if vs_full is not None:
        vs = _downsample(vs_full)
        nz, nx = vs.shape
        x_km = np.linspace(0, vs_full.shape[1] * dx / 1000, nx)
        z_km = np.linspace(0, vs_full.shape[0] * dz / 1000, nz)
        panels.append(("Vs (m/s)", vs, x_km, z_km, colorscale, None, None))

    if vp_full is not None and vs_full is not None:
        nz_ = min(vp_full.shape[0], vs_full.shape[0])
        nx_ = min(vp_full.shape[1], vs_full.shape[1])
        ratio_full = np.where(
            vs_full[:nz_, :nx_] > 0,
            vp_full[:nz_, :nx_] / vs_full[:nz_, :nx_],
            np.nan,
        )
        ratio = _downsample(ratio_full)
        nz, nx = ratio.shape
        x_km = np.linspace(0, nx_ * dx / 1000, nx)
        z_km = np.linspace(0, nz_ * dz / 1000, nz)
        panels.append(("Vp/Vs ratio", ratio, x_km, z_km, "Viridis",
                        float(np.nanpercentile(ratio_full, 2)),
                        float(np.nanpercentile(ratio_full, 98))))

    if not panels:
        return go.Figure()

    # One trace per panel, toggle via updatemenus buttons
    fig = go.Figure()

    for i, (title, data, x_km, z_km, cscale, zmin, zmax) in enumerate(panels):
        if zmin is None:
            finite = data[np.isfinite(data)]
            zmin = float(np.percentile(finite, 2))
            zmax = float(np.percentile(finite, 98))

        hovertemplate = (
            "Distance: %{x:.2f} km<br>"
            "Depth: %{y:.2f} km<br>"
            f"{title}: " + "%{z:.0f}<extra></extra>"
        )

        fig.add_trace(go.Heatmap(
            z=data,
            x=x_km,
            y=z_km,
            colorscale=cscale,
            zmin=zmin,
            zmax=zmax,
            colorbar=dict(title=title, thickness=15, len=0.8),
            hovertemplate=hovertemplate,
            visible=(i == 0),
            name=title,
        ))

    # Build toggle buttons
    buttons = []
    for i, (title, *_) in enumerate(panels):
        visibility = [j == i for j in range(len(panels))]
        buttons.append(dict(
            label=title,
            method="update",
            args=[{"visible": visibility}, {"title": f"<b>{title}</b> — Marmousi Model"}],
        ))

    fig.update_layout(
        title=f"<b>{panels[0][0]}</b> — Marmousi Model",
        xaxis=dict(title="Distance (km)", showgrid=False),
        yaxis=dict(title="Depth (km)", autorange="reversed", showgrid=False),
        updatemenus=[dict(
            type="buttons",
            direction="right",
            x=0.0, xanchor="left",
            y=1.12, yanchor="top",
            buttons=buttons,
            bgcolor="#f0f2f6",
            bordercolor="#ccc",
            font=dict(size=13),
            pad=dict(r=10, t=5),
        )],
        height=520,
        margin=dict(l=60, r=20, t=90, b=60),
        plot_bgcolor="white",
        paper_bgcolor="white",
    )
    return fig


def _format_metrics(metrics: dict) -> list[dict]:
    """Flatten nested metrics dict into a list of {Metric, Value} rows."""
    rows = []

    def _walk(d: dict, prefix: str = "") -> None:
        for k, v in d.items():
            if k == "llm_summary":
                continue
            key = f"{prefix}{k}"
            if isinstance(v, dict):
                _walk(v, f"{key}.")
            elif isinstance(v, list):
                rows.append({"Metric": key, "Value": str(v)[:80]})
            else:
                val = f"{v:.4g}" if isinstance(v, float) else str(v)
                rows.append({"Metric": key, "Value": val})

    _walk(metrics)
    return rows


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------

with st.sidebar:
    st.title("🌊 Seismic Agent")
    st.caption("Multi-Agent System for Geophysical Analysis")
    st.divider()

    query = st.text_area(
        "Mission Query",
        value="Analyze the Marmousi synthetic data for porosity prediction.",
        height=100,
    )

    analysis_type = st.selectbox(
        "Analysis Pipeline",
        options=[
            "porosity prediction",
            "velocity analysis",
            "structural interpretation",
            "forward modeling",
            "full waveform inversion",
        ],
        index=0,
    )

    st.divider()
    st.markdown("**Pipeline steps**")
    pipeline_map = {
        "porosity prediction":       ["SEGYLoader", "VelocityAnalysis", "LLMSummary"],
        "velocity analysis":         ["SEGYLoader", "VelocityAnalysis", "LLMSummary"],
        "structural interpretation": ["SEGYLoader", "VelocityAnalysis", "LLMSummary"],
        "forward modeling":          ["SEGYLoader", "VelocityAnalysis", "ForwardModeling", "LLMSummary"],
        "full waveform inversion":   ["SEGYLoader", "VelocityAnalysis", "ForwardModeling", "FWI", "LLMSummary"],
    }
    for step in pipeline_map.get(analysis_type, []):
        st.markdown(f"- `{step}`")

    st.divider()
    run_btn = st.button("▶ Run Analysis", type="primary", use_container_width=True)

# ---------------------------------------------------------------------------
# Main area
# ---------------------------------------------------------------------------

st.title("Geophysical Analysis Dashboard")

if not run_btn:
    st.info("Configure your mission in the sidebar and click **▶ Run Analysis** to start.")
    st.stop()

# ---- Run pipeline ----

client = _get_client()
outputs_dir = Path("outputs")
outputs_dir.mkdir(exist_ok=True)

# Override analysis_type in the query so the Manager picks the right pipeline
# by injecting it via a modified query hint passed to AnalysisAgent directly.

workspace = Workspace(log_path="mission_log.json")

# --- Step 1: Manager (data discovery) ---
col_prog, col_fig = st.columns([1, 1])

with col_prog:
    st.subheader("Pipeline Progress")
    manager_status = st.status("🔍 Manager: discovering data...", expanded=True)

    with manager_status:
        manager = ManagerAgent("Manager", workspace, client)

        # Intercept DATA_READY to grab files + kick off our own pipeline
        data_ready_payload: dict = {}

        def _on_data_ready(signal, data=None):
            data_ready_payload.update(data or {})

        workspace.subscribe(MissionSignal.DATA_READY, _on_data_ready)
        manager.process_request(query)

        if workspace.state and workspace.state.status in ("AWAITING_CLARIFICATION", "INITIALIZED"):
            st.error("Manager could not find matching data files.")
            st.stop()

        files = data_ready_payload.get("files", [])
        st.write(f"✅ Found **{len(files)}** data file(s)")
        for f in files:
            st.markdown(f"  - `{Path(f).name}`")

    manager_status.update(label="✅ Manager: data verified", state="complete")

# --- Step 2: Tool chain ---
ctx = ToolContext(
    files=[Path(f) if not isinstance(f, Path) else f for f in files],
    analysis_type=analysis_type,
    outputs_dir=outputs_dir,
)

chain = resolve_pipeline(analysis_type, client)
tool_names = [t.name for t in chain.tools]
n_tools = len(tool_names)

tool_statuses: dict = {}

with col_prog:
    for name in tool_names:
        tool_statuses[name] = st.status(f"⏳ {name}...", expanded=False)

fig_placeholder = col_fig.empty()

tool_results = []
figures_so_far: list[str] = []

def _on_start(name: str):
    tool_statuses[name].update(label=f"🔄 {name} running...", state="running", expanded=True)

def _on_done(result):
    icon = "✅" if result.success else "❌"
    tool_statuses[result.tool_name].update(
        label=f"{icon} {result.tool_name}: {result.summary[:60]}{'…' if len(result.summary) > 60 else ''}",
        state="complete" if result.success else "error",
        expanded=False,
    )
    # Refresh figures column after each tool
    new_figs = [f for f in ctx.figures if f not in figures_so_far]
    if new_figs:
        figures_so_far.extend(new_figs)
        with fig_placeholder.container():
            col_fig.subheader("Generated Figures")
            for fig_path in figures_so_far:
                p = Path(fig_path)
                if p.exists():
                    col_fig.image(str(p), caption=p.stem.replace("_", " ").title(), use_container_width=True)

tool_results = chain.execute(ctx, on_tool_start=_on_start, on_tool_done=_on_done)

# Final figure render
with col_fig:
    st.subheader("Generated Figures")
    if ctx.figures:
        for fig_path in ctx.figures:
            p = Path(fig_path)
            if p.exists():
                st.image(str(p), caption=p.stem.replace("_", " ").title(), use_container_width=True)
    else:
        st.info("No figures generated.")

fig_placeholder.empty()

# ---------------------------------------------------------------------------
# Interactive velocity model viewer
# ---------------------------------------------------------------------------

if ctx.velocity_models and ("vp" in ctx.velocity_models or "vs" in ctx.velocity_models):
    st.divider()
    st.subheader("Interactive Velocity Model")

    viewer_col, ctrl_col = st.columns([5, 1])

    with ctrl_col:
        st.markdown("**Colorscale**")
        colorscale = st.radio(
            "colorscale",
            options=["RdYlBu_r", "Viridis", "Seismic", "Hot_r"],
            index=0,
            label_visibility="collapsed",
        )
        st.caption("Vp/Vs ratio always uses Viridis.")

    with viewer_col:
        plotly_fig = _velocity_figure(ctx.velocity_models, colorscale=colorscale)
        st.plotly_chart(plotly_fig, use_container_width=True)

    vp = ctx.velocity_models.get("vp")
    vs = ctx.velocity_models.get("vs")
    if vp is not None:
        dx = ctx.velocity_models.get("dx", 1.25)
        dz = ctx.velocity_models.get("dz", 1.25)
        nz, nx = vp.shape
        m1, m2, m3 = st.columns(3)
        m1.metric("Model extent (x)", f"{nx * dx / 1000:.1f} km")
        m2.metric("Model extent (z)", f"{nz * dz / 1000:.1f} km")
        m3.metric("Grid spacing", f"{dx} m")

# ---------------------------------------------------------------------------
# Metrics table
# ---------------------------------------------------------------------------

st.divider()
st.subheader("Quantitative Results")
metrics = {k: v for k, v in ctx.metrics.items() if k != "llm_summary"}
if metrics:
    rows = _format_metrics(metrics)
    st.dataframe(rows, use_container_width=True, hide_index=True)
else:
    st.info("No metrics available.")

# ---------------------------------------------------------------------------
# LLM Summary
# ---------------------------------------------------------------------------

st.divider()
st.subheader("Geophysical Interpretation")
summary = ctx.metrics.get("llm_summary", "")
if summary:
    st.markdown(summary)
else:
    st.info("No LLM summary generated.")

# ---------------------------------------------------------------------------
# Generate reports and offer downloads
# ---------------------------------------------------------------------------

st.divider()
st.subheader("Download Reports")

ts = datetime.now().strftime("%Y%m%d_%H%M%S")
mission_id = workspace.state.mission_id if workspace.state else "N/A"

# Update workspace state for reporter
if workspace.state:
    workspace.update_state(
        analysis_results={
            "type": analysis_type,
            "insights": summary,
            "files_processed": [str(f) for f in ctx.files],
            "figures": ctx.figures,
            "metrics": ctx.metrics,
        },
        status="ANALYSIS_COMPLETED",
    )

# Markdown report
md_content = f"""# Geophysical Analysis Report
**Mission ID:** {mission_id}
**Date:** {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
**Analysis Type:** {analysis_type}

## Mission Objective
{query}

## Data Sources
{", ".join(Path(p).name for p in (workspace.state.data_paths if workspace.state else []))}

## Analysis Insights
{summary}
"""
md_path = outputs_dir / f"geophysical_report_{ts}.md"
md_path.write_text(md_content)

col_dl1, col_dl2 = st.columns(2)

with col_dl1:
    st.download_button(
        label="⬇ Download Markdown",
        data=md_content,
        file_name=f"geophysical_report_{ts}.md",
        mime="text/markdown",
        use_container_width=True,
    )

# PDF report
with col_dl2:
    with st.spinner("Compiling PDF..."):
        latex_agent = LaTeXReporterAgent("LaTeXReporter", workspace, client)
        latex_agent.generate_report(insights=summary, figures=ctx.figures)

    pdf_path = outputs_dir / f"geophysical_report_{ts}.pdf"
    # LaTeXReporterAgent saves with its own timestamp — find the latest pdf
    pdfs = sorted(outputs_dir.glob("*.pdf"), key=lambda p: p.stat().st_mtime, reverse=True)
    if pdfs:
        pdf_bytes = pdfs[0].read_bytes()
        st.download_button(
            label="⬇ Download PDF",
            data=pdf_bytes,
            file_name=pdfs[0].name,
            mime="application/pdf",
            use_container_width=True,
        )
    else:
        st.warning("PDF compilation failed.")
