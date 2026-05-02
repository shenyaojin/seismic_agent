"""
eval/report.py – Markdown report generator for eval/run_eval.py results.

Can be used standalone:
  python eval/report.py eval/results_20250101_120000.json
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Core generator
# ---------------------------------------------------------------------------

def generate_report(results: list[dict], output_path: Path) -> None:
    """Write a Markdown evaluation report to *output_path*."""
    lines: list[str] = []
    _write = lines.append

    n_total = len(results)
    n_pass  = sum(1 for r in results if r["passed"])
    n_fail  = n_total - n_pass
    pass_rate = n_pass / n_total * 100 if n_total else 0

    total_input_tokens  = sum(r["cost_summary"].get("input_tokens", 0)  for r in results)
    total_output_tokens = sum(r["cost_summary"].get("output_tokens", 0) for r in results)
    total_tokens        = total_input_tokens + total_output_tokens
    total_usd           = sum(r["cost_summary"].get("estimated_usd", 0) for r in results)
    total_calls         = sum(r["cost_summary"].get("call_count", 0)    for r in results)

    durations_ms = [r["duration_ms"] for r in results if r["duration_ms"] > 0]
    avg_latency_ms = sum(durations_ms) / len(durations_ms) if durations_ms else 0
    max_latency_ms = max(durations_ms) if durations_ms else 0

    confidences = [r["confidence_score"] for r in results if r["confidence_score"] is not None]
    avg_confidence = sum(confidences) / len(confidences) if confidences else None

    # --- Title & timestamp ---
    _write(f"# Seismic Agent MAS – Evaluation Report")
    _write(f"")
    _write(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    _write(f"")
    _write(f"---")
    _write(f"")

    # --- Executive summary ---
    _write(f"## Executive Summary")
    _write(f"")
    _write(f"| Metric | Value |")
    _write(f"|--------|-------|")
    _write(f"| Total test cases | {n_total} |")
    _write(f"| Passed | **{n_pass}** |")
    _write(f"| Failed | {n_fail} |")
    _write(f"| Pass rate | **{pass_rate:.1f}%** |")
    _write(f"| Avg latency | {avg_latency_ms / 1000:.1f} s |")
    _write(f"| Max latency | {max_latency_ms / 1000:.1f} s |")
    _write(f"| Total LLM calls | {total_calls} |")
    _write(f"| Total tokens | {total_tokens:,} ({total_input_tokens:,} in / {total_output_tokens:,} out) |")
    _write(f"| Estimated cost | **${total_usd:.4f} USD** |")
    if avg_confidence is not None:
        _write(f"| Avg verifier confidence | {avg_confidence:.3f} |")
    _write(f"")

    # --- Results by category ---
    _write(f"## Results by Category")
    _write(f"")

    by_cat: dict[str, list[dict]] = defaultdict(list)
    for r in results:
        by_cat[r["category"]].append(r)

    for cat, items in sorted(by_cat.items()):
        cat_pass = sum(1 for r in items if r["passed"])
        _write(f"### {cat}  ({cat_pass}/{len(items)} passed)")
        _write(f"")
        _write(f"| ID | Query (truncated) | Expected | Status | Pass | Latency | Cost |")
        _write(f"|----|-------------------|----------|--------|------|---------|------|")
        for r in items:
            q = (r["query"][:55] + "…") if len(r["query"]) > 55 else r["query"]
            q = q.replace("|", "\\|")
            icon = "✅" if r["passed"] else "❌"
            lat  = f"{r['duration_ms'] / 1000:.1f}s"
            cost = f"${r['cost_summary'].get('estimated_usd', 0):.4f}"
            _write(
                f"| {r['id']} | {q} | {r['expected_outcome']} | "
                f"`{r['status'] or '—'}` | {icon} | {lat} | {cost} |"
            )
        _write(f"")

    # --- Error analysis ---
    error_counts: dict[str, int] = defaultdict(int)
    for r in results:
        if not r["passed"]:
            error_counts[r["error_category"]] += 1

    if error_counts:
        _write(f"## Error Analysis")
        _write(f"")
        _write(f"| Error Category | Count |")
        _write(f"|----------------|-------|")
        for cat, count in sorted(error_counts.items(), key=lambda x: -x[1]):
            _write(f"| `{cat}` | {count} |")
        _write(f"")

        _write(f"### Failed test details")
        _write(f"")
        for r in results:
            if r["passed"]:
                continue
            _write(f"**{r['id']}** (`{r['category']}`)  ")
            _write(f"Query: *{r['query'][:120]}*  ")
            _write(f"Status: `{r['status'] or 'N/A'}`  ")
            _write(f"Error category: `{r['error_category']}`  ")
            if r["error_message"]:
                _write(f"Error message: `{r['error_message'][:200]}`  ")
            _write(f"")

    # --- Cost details ---
    _write(f"## Cost Details")
    _write(f"")
    _write(f"| ID | Category | LLM Calls | Tokens In | Tokens Out | Cost USD |")
    _write(f"|----|----------|-----------|-----------|------------|----------|")
    for r in results:
        cs = r.get("cost_summary", {})
        _write(
            f"| {r['id']} | {r['category']} "
            f"| {cs.get('call_count', 0)} "
            f"| {cs.get('input_tokens', 0):,} "
            f"| {cs.get('output_tokens', 0):,} "
            f"| ${cs.get('estimated_usd', 0):.4f} |"
        )
    _write(f"")
    _write(f"*Pricing estimate: $0.10/1M input tokens, $0.40/1M output tokens (Gemini 2.5 Flash).*")
    _write(f"")

    # --- Verifier confidence ---
    if confidences:
        _write(f"## Verifier Confidence Scores")
        _write(f"")
        _write(f"| ID | Confidence |")
        _write(f"|----|------------|")
        for r in results:
            if r["confidence_score"] is not None:
                bar = "█" * int(r["confidence_score"] * 10)
                _write(f"| {r['id']} | {r['confidence_score']:.3f} `{bar:<10}` |")
        _write(f"")

    # --- Footer ---
    _write(f"---")
    _write(f"*Generated automatically by `eval/report.py` — Seismic Agent MAS.*")

    output_path.write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# CLI entry-point
# ---------------------------------------------------------------------------

def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python eval/report.py <results_*.json> [output.md]")
        sys.exit(1)

    results_path = Path(sys.argv[1])
    if not results_path.exists():
        sys.exit(f"File not found: {results_path}")

    results: list[dict[str, Any]] = json.loads(results_path.read_text())

    if len(sys.argv) >= 3:
        output_path = Path(sys.argv[2])
    else:
        output_path = results_path.with_suffix(".md")

    generate_report(results, output_path)
    print(f"Report written to {output_path}")


if __name__ == "__main__":
    main()
