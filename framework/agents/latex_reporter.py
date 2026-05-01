"""
LaTeXReporterAgent: Compiles analysis results into a compiled PDF report.

Subscribes to ANALYSIS_COMPLETE, generates a .tex file, and compiles it
with pdflatex. Figures produced by the tool chain are embedded automatically.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from google import genai

from framework.workspace import Workspace, MissionSignal
from framework.agents.base import BaseAgent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _escape(text: str) -> str:
    """Escape special LaTeX characters in plain text."""
    replacements = [
        ("\\", r"\textbackslash{}"),
        ("&",  r"\&"),
        ("%",  r"\%"),
        ("$",  r"\$"),
        ("#",  r"\#"),
        ("_",  r"\_"),
        ("{",  r"\{"),
        ("}",  r"\}"),
        ("~",  r"\textasciitilde{}"),
        ("^",  r"\textasciicircum{}"),
    ]
    for char, escaped in replacements:
        text = text.replace(char, escaped)
    return text


def _md_to_latex(text: str) -> str:
    """
    Minimal Markdown → LaTeX conversion sufficient for Gemini output.
    Handles: headers, bold, italic, bullet lists, numbered lists, code spans.
    """
    lines = text.split("\n")
    out: list[str] = []
    in_itemize = False
    in_enumerate = False

    for raw in lines:
        line = raw.rstrip()

        # Close open lists when a non-list line appears
        if in_itemize and not line.startswith("- ") and not line.startswith("* "):
            out.append(r"\end{itemize}")
            in_itemize = False
        if in_enumerate and not re.match(r"^\d+\.", line):
            out.append(r"\end{enumerate}")
            in_enumerate = False

        # Headers
        if line.startswith("### "):
            out.append(r"\subsubsection*{" + _escape(line[4:]) + "}")
            continue
        if line.startswith("## "):
            out.append(r"\subsection*{" + _escape(line[3:]) + "}")
            continue
        if line.startswith("# "):
            out.append(r"\section*{" + _escape(line[2:]) + "}")
            continue

        # Bullet list (handle indented bullets like "    * " or "  - ")
        bullet_m = re.match(r"^[ \t]*[-*]\s+(.*)", line)
        if bullet_m:
            if not in_itemize:
                out.append(r"\begin{itemize}")
                in_itemize = True
            content = _inline(bullet_m.group(1))
            out.append(r"  \item " + content)
            continue

        # Numbered list
        m = re.match(r"^(\d+)\.\s+(.*)", line)
        if m:
            if not in_enumerate:
                out.append(r"\begin{enumerate}")
                in_enumerate = True
            out.append(r"  \item " + _inline(m.group(2)))
            continue

        # Blank line → paragraph break
        if line.strip() == "":
            out.append("")
            continue

        # Horizontal rule
        if line.strip().startswith("---"):
            out.append(r"\noindent\rule{\linewidth}{0.4pt}")
            continue

        # Escape raw LaTeX specials in the non-Markdown parts, then apply inline formatting
        out.append(_inline(_escape_non_md(line)))

    # Close any still-open lists
    if in_itemize:
        out.append(r"\end{itemize}")
    if in_enumerate:
        out.append(r"\end{enumerate}")

    return "\n".join(out)


def _escape_non_md(text: str) -> str:
    """Escape LaTeX special chars that appear OUTSIDE Markdown markers.

    We escape & % $ # { } ~ ^ but leave * _ \\ alone so _inline() can
    still parse bold/italic markers.
    """
    # Only escape the chars that cause LaTeX errors and are unlikely to be
    # Markdown syntax at line level.
    text = text.replace("&",  r"\&")
    text = text.replace("%",  r"\%")
    text = text.replace("$",  r"\$")
    text = text.replace("#",  r"\#")
    return text


def _inline(text: str) -> str:
    """Apply inline Markdown formatting (bold, italic, code).

    Order matters:
      1. Extract code spans first (protect their content from further parsing).
      2. Bold, then italic (using only ** / * delimiters; avoid _ to prevent
         false matches on underscores in filenames and identifiers).
      3. Re-insert code spans as \\texttt{}.
    """
    # 1. Extract code spans and replace with placeholders
    placeholders: list[str] = []

    def _stash(m: re.Match) -> str:
        placeholders.append(m.group(1))
        return f"\x00CODE{len(placeholders) - 1}\x00"

    text = re.sub(r"`(.+?)`", _stash, text)

    # 2. Bold **text** (must come before single-star italic)
    text = re.sub(r"\*\*(.+?)\*\*", lambda m: f"\\textbf{{{_escape(m.group(1))}}}", text)
    text = re.sub(r"__(.+?)__",     lambda m: f"\\textbf{{{_escape(m.group(1))}}}", text)

    # 3. Italic *text* only (skip _text_ to avoid mangling underscores)
    text = re.sub(r"\*([^*\n]+?)\*", lambda m: f"\\textit{{{_escape(m.group(1))}}}", text)

    # 4. Restore code placeholders
    for i, content in enumerate(placeholders):
        # Escape LaTeX special chars inside code
        safe = content.replace("_", r"\_").replace("%", r"\%").replace("&", r"\&")
        text = text.replace(f"\x00CODE{i}\x00", f"\\texttt{{{safe}}}")

    return text


def _metrics_table(metrics: dict) -> str:
    """Render a flat subset of ctx.metrics as a LaTeX longtable."""
    rows: list[tuple[str, str]] = []

    def _flatten(d: dict, prefix: str = "") -> None:
        for k, v in d.items():
            if k == "llm_summary":
                continue
            key = f"{prefix}{k}".replace("_", r"\_")
            if isinstance(v, dict):
                _flatten(v, prefix=f"{prefix}{k}.")
            elif isinstance(v, list):
                rows.append((key, _escape(str(v)[:80])))
            else:
                val = f"{v:.4g}" if isinstance(v, float) else str(v)
                rows.append((key, _escape(val)))

    _flatten(metrics)
    if not rows:
        return ""

    lines = [
        r"\begin{longtable}{p{0.55\linewidth} p{0.35\linewidth}}",
        r"\toprule",
        r"\textbf{Metric} & \textbf{Value} \\",
        r"\midrule",
        r"\endhead",
    ]
    for key, val in rows:
        lines.append(f"  {key} & {val} \\\\")
    lines += [r"\bottomrule", r"\end{longtable}"]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------

class LaTeXReporterAgent(BaseAgent):
    """
    Generates a compiled PDF geophysical report from analysis results.

    Subscribes to ANALYSIS_COMPLETE; produces <output_dir>/<timestamp>.pdf.
    """

    def __init__(
        self,
        name: str,
        workspace: Workspace,
        client: genai.Client,
        output_dir: str = "outputs/",
        compiler: str = "pdflatex",
    ):
        self.output_dir = Path(output_dir).resolve()
        self.output_dir.mkdir(exist_ok=True)
        self.compiler = compiler
        super().__init__(name, workspace, client)

    def _setup_subscriptions(self):
        self.workspace.subscribe(MissionSignal.ANALYSIS_COMPLETE, self.handle_signal)

    def handle_signal(self, signal: MissionSignal, data: Any = None):
        if signal == MissionSignal.ANALYSIS_COMPLETE:
            self.logger.info("LaTeX Reporter triggered.")
            self.generate_report(
                insights=data.get("insights", ""),
                figures=data.get("figures", []),
            )

    # ------------------------------------------------------------------

    def generate_report(self, insights: str, figures: list[str]) -> None:
        state = self.workspace.state
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        stem = f"geophysical_report_{ts}"

        metrics: dict = {}
        if state and state.analysis_results:
            metrics = state.analysis_results.get("metrics", {})

        tex = self._build_tex(state, insights, figures, metrics)

        pdf_path = self._compile(tex, stem)
        if pdf_path:
            self.logger.info(f"PDF report: {pdf_path}")
            self.workspace.update_state(status="LATEX_REPORT_GENERATED")
            self.workspace.emit(
                MissionSignal.LATEX_REPORT_GENERATED,
                data={"path": str(pdf_path)},
            )
            print(f"\n[LaTeX Reporter] PDF saved: {pdf_path}")
        else:
            self.logger.error("PDF compilation failed.")
            self.workspace.emit(
                MissionSignal.MISSION_FAILED,
                data={"error": "pdflatex compilation failed"},
            )

    # ------------------------------------------------------------------

    def _build_tex(
        self,
        state: Any,
        insights: str,
        figures: list[str],
        metrics: dict,
    ) -> str:
        mission_id  = state.mission_id  if state else "N/A"
        description = state.description if state else "N/A"
        data_paths  = state.data_paths  if state else []

        date_str = datetime.now().strftime("%B %d, %Y")

        # Figures section
        fig_blocks: list[str] = []
        for i, fig_path in enumerate(figures, 1):
            p = Path(fig_path)
            if not p.exists():
                continue
            caption = _escape(p.stem.replace("_", " ").title())
            fig_blocks.append(
                f"\\begin{{figure}}[H]\n"
                f"  \\centering\n"
                f"  \\includegraphics[width=0.92\\linewidth]{{{fig_path}}}\n"
                f"  \\caption{{{caption}}}\n"
                f"\\end{{figure}}\n"
            )
        figures_latex = "\n".join(fig_blocks) if fig_blocks else r"\textit{No figures generated.}"

        # Metrics table
        metrics_latex = _metrics_table(metrics) or r"\textit{No metrics available.}"

        # Main insights body
        body_latex = _md_to_latex(insights)

        # Data sources list
        if data_paths:
            src_items = "\n".join(
                r"  \item \texttt{" + _escape(Path(p).name) + "}"
                for p in data_paths
            )
            sources_latex = r"\begin{itemize}" + "\n" + src_items + "\n" + r"\end{itemize}"
        else:
            sources_latex = r"\textit{None}"

        return rf"""
\documentclass[12pt,a4paper]{{article}}

% --- Packages ---
\usepackage[margin=2.5cm]{{geometry}}
\usepackage{{graphicx}}
\usepackage{{booktabs}}
\usepackage{{longtable}}
\usepackage{{float}}
\usepackage{{hyperref}}
\usepackage{{xcolor}}
\usepackage{{titlesec}}
\usepackage{{fancyhdr}}
\usepackage{{amsmath}}
\usepackage{{microtype}}
\usepackage{{parskip}}

% --- Appearance ---
\definecolor{{accent}}{{HTML}}{{2E4057}}
\titleformat{{\section}}{{\large\bfseries\color{{accent}}}}{{}}{{0em}}{{}}[\titlerule]
\titleformat{{\subsection}}{{\normalsize\bfseries\color{{accent}}}}{{}}{{0em}}{{}}
\titleformat{{\subsubsection}}{{\normalsize\itshape}}{{}}{{0em}}{{}}

\pagestyle{{fancy}}
\fancyhf{{}}
\rhead{{\textcolor{{accent}}{{Seismic Agent MAS}}}}
\lhead{{\textcolor{{accent}}{{\textit{{Geophysical Analysis Report}}}}}}
\rfoot{{Page \thepage}}

\hypersetup{{
  colorlinks=true, linkcolor=accent, urlcolor=accent, citecolor=accent,
  pdftitle={{Geophysical Analysis Report}},
  pdfauthor={{Seismic Agent MAS}},
}}

% ============================================================
\begin{{document}}

% --- Title page ---
\begin{{titlepage}}
  \centering
  \vspace*{{\fill}}
  {{\Huge\bfseries\color{{accent}} Geophysical Analysis Report}}\\[1.2em]
  {{\large\itshape Seismic Agent Multi-Agent System}}\\[2.5em]
  \rule{{0.6\linewidth}}{{0.6pt}}\\[1.5em]
  \begin{{tabular}}{{rl}}
    \textbf{{Mission ID:}} & \texttt{{{_escape(mission_id)}}} \\[0.4em]
    \textbf{{Date:}}       & {date_str} \\
  \end{{tabular}}
  \vspace*{{\fill}}
\end{{titlepage}}

\tableofcontents
\newpage

% ============================================================
\section{{Mission Objective}}
{_escape(description)}

% ============================================================
\section{{Data Sources}}
{sources_latex}

% ============================================================
\section{{Quantitative Results}}
\begin{{center}}
{metrics_latex}
\end{{center}}

% ============================================================
\section{{Generated Figures}}
{figures_latex}

% ============================================================
\section{{Geophysical Analysis \& Interpretation}}
{body_latex}

% ============================================================
\section*{{Document Information}}
\footnotesize
Generated automatically by the \textbf{{Seismic Agent Multi-Agent System}} on {date_str}.
All numerical results were produced by the analysis tool chain (SEGYLoader,
VelocityAnalysis, ForwardModeling, FWI) and interpreted by a large language model.

\end{{document}}
"""

    # ------------------------------------------------------------------

    def _compile(self, tex_source: str, stem: str) -> Path | None:
        """Write .tex to a temp dir, compile twice, copy PDF to output_dir."""
        if not shutil.which(self.compiler):
            self.logger.error(f"Compiler '{self.compiler}' not found in PATH.")
            return None

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            tex_file = tmp_path / f"{stem}.tex"
            tex_file.write_text(tex_source, encoding="utf-8")

            cmd = [
                self.compiler,
                "-interaction=nonstopmode",
                "-output-directory", str(tmp_path),
                str(tex_file),
            ]

            for pass_num in (1, 2):   # two passes for TOC
                result = subprocess.run(
                    cmd, capture_output=True, text=True, cwd=tmp_path
                )
                if result.returncode != 0:
                    self.logger.error(
                        f"pdflatex pass {pass_num} failed:\n{result.stdout[-2000:]}"
                    )
                    # Save .tex for debugging
                    debug_tex = self.output_dir / f"{stem}.tex"
                    debug_tex.write_text(tex_source, encoding="utf-8")
                    self.logger.info(f"LaTeX source saved for debugging: {debug_tex}")
                    return None

            pdf_tmp = tmp_path / f"{stem}.pdf"
            if not pdf_tmp.exists():
                return None

            pdf_out = self.output_dir / f"{stem}.pdf"
            shutil.copy2(pdf_tmp, pdf_out)
            return pdf_out
