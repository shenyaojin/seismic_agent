"""
eval/run_eval.py – Batch evaluation runner for the Seismic Agent MAS.

Usage
-----
  # Run all 25 test cases:
  python eval/run_eval.py

  # Run only specific categories:
  python eval/run_eval.py --categories happy_path wrong_dataset

  # Dry-run (validate JSON, no LLM calls):
  python eval/run_eval.py --dry-run

  # Limit number of cases (useful for quick smoke tests):
  python eval/run_eval.py --limit 5

Output
------
  eval/results_<timestamp>.json   – raw results per test case
  eval/report_<timestamp>.md      – human-readable Markdown summary
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

# Add project root to sys.path so framework imports work
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from google import genai

from framework.cost_tracker import CostTracker
from framework.workspace import Workspace
from framework.agents.manager import ManagerAgent
from framework.agents.analysis import AnalysisAgent
from framework.agents.verifier import VerifierAgent
from framework.agents.reporter import ReporterAgent
from framework.agents.latex_reporter import LaTeXReporterAgent
from framework.guardrails import SeismicGuardrails


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TEST_CASES_PATH = Path(__file__).parent / "test_cases.json"
RESULTS_DIR     = Path(__file__).parent
OUTPUTS_DIR     = PROJECT_ROOT / "outputs"

SUCCESS_STATUSES = {
    "LATEX_REPORT_GENERATED",
    "REPORT_GENERATED",
    "VERIFICATION_COMPLETE",
}

# Error category labels
def _categorise_error(exc: Exception | None, status: str | None) -> str:
    if exc is None and status in SUCCESS_STATUSES:
        return "none"
    if exc is not None:
        return "exception"
    if status == "AWAITING_CLARIFICATION":
        return "planning_error"
    if status in ("ANALYSIS_COMPLETED",):
        # Analysis finished but verification/reporting didn't
        return "tool_failure"
    return "unknown"


# ---------------------------------------------------------------------------
# Single test-case runner
# ---------------------------------------------------------------------------

def run_one(tc: dict, gemini_client: genai.Client, dry_run: bool = False) -> dict:
    """
    Execute a single test case and return a result record.

    Returns
    -------
    dict with keys:
        id, category, query, expected_outcome,
        passed, status, error_category, error_message,
        duration_ms, cost_summary, confidence_score
    """
    result: dict = {
        "id":               tc["id"],
        "category":         tc["category"],
        "query":            tc["query"],
        "expected_outcome": tc["expected_outcome"],
        "notes":            tc.get("notes", ""),
        "passed":           False,
        "status":           None,
        "error_category":   "unknown",
        "error_message":    None,
        "duration_ms":      0.0,
        "cost_summary":     {},
        "confidence_score": None,
    }

    if dry_run:
        result["passed"] = True
        result["error_category"] = "none"
        result["status"] = "DRY_RUN"
        return result

    tracker = CostTracker()
    tracked_client = tracker.wrap_client(gemini_client)

    # Each test uses its own log file to avoid polluting the main mission_log
    log_path = RESULTS_DIR / f"_eval_log_{tc['id']}.json"

    exc_caught: Exception | None = None
    start = time.perf_counter()

    try:
        workspace = Workspace(log_path=str(log_path))
        manager   = ManagerAgent("Manager",         workspace, tracked_client)
        _         = AnalysisAgent("Analyzer",       workspace, tracked_client)
        _         = VerifierAgent("Verifier",       workspace, tracked_client,
                                  guardrails=SeismicGuardrails())
        _         = ReporterAgent("Reporter",       workspace, tracked_client)
        _         = LaTeXReporterAgent("LaTeXReporter", workspace, tracked_client)

        manager.process_request(tc["query"])

    except Exception as e:
        exc_caught = e

    duration_ms = (time.perf_counter() - start) * 1000

    # Clean up per-test log file
    try:
        log_path.unlink(missing_ok=True)
    except Exception:
        pass

    # Determine outcome
    status: str | None = None
    confidence: float | None = None

    if exc_caught is None:
        # Inspect workspace state (reference kept via closure – workspace is local, but
        # we can inspect through manager.workspace which is the same object)
        ws = manager.workspace
        if ws.state:
            status = ws.state.status
            verification = ws.state.analysis_results.get("verification", {}) if ws.state.analysis_results else {}
            confidence = verification.get("confidence_score")

    error_cat = _categorise_error(exc_caught, status)

    # Determine PASS / FAIL against expected outcome
    expected = tc["expected_outcome"]
    if expected == "success":
        passed = (exc_caught is None) and (status in SUCCESS_STATUSES)
    elif expected == "awaiting_clarification":
        passed = (exc_caught is None) and (status == "AWAITING_CLARIFICATION")
    elif expected == "no_crash":
        passed = exc_caught is None
    else:
        passed = exc_caught is None  # fallback

    result.update({
        "passed":           passed,
        "status":           status,
        "error_category":   error_cat,
        "error_message":    str(exc_caught) if exc_caught else None,
        "duration_ms":      round(duration_ms, 1),
        "cost_summary":     tracker.summary(),
        "confidence_score": confidence,
    })
    return result


# ---------------------------------------------------------------------------
# Main batch runner
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Seismic Agent evaluation runner")
    parser.add_argument(
        "--categories", nargs="+", default=None,
        help="Only run test cases from these categories",
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Stop after N test cases",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Validate test cases without making any LLM calls",
    )
    parser.add_argument(
        "--test-id", nargs="+", default=None,
        help="Run only the specified test case IDs (e.g. tc_001 tc_002)",
    )
    args = parser.parse_args()

    # Load .env
    env_path = PROJECT_ROOT / ".env"
    if env_path.exists():
        for raw in env_path.read_text().splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

    if not args.dry_run:
        if not (os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")):
            sys.exit(
                "ERROR: Set GOOGLE_API_KEY or GEMINI_API_KEY before running eval."
            )

    # Load test cases
    test_cases: list[dict] = json.loads(TEST_CASES_PATH.read_text())

    # Apply filters
    if args.categories:
        test_cases = [tc for tc in test_cases if tc["category"] in args.categories]
    if args.test_id:
        test_cases = [tc for tc in test_cases if tc["id"] in args.test_id]
    if args.limit:
        test_cases = test_cases[: args.limit]

    if not test_cases:
        sys.exit("No test cases match the given filters.")

    print(f"=== Seismic Agent Evaluation ===")
    print(f"Test cases : {len(test_cases)}")
    print(f"Dry run    : {args.dry_run}")
    print()

    gemini_client = genai.Client() if not args.dry_run else None

    results: list[dict] = []
    for i, tc in enumerate(test_cases, 1):
        label = f"[{i:2d}/{len(test_cases)}] {tc['id']} ({tc['category']})"
        print(f"{label} ... ", end="", flush=True)
        r = run_one(tc, gemini_client, dry_run=args.dry_run)
        results.append(r)
        status_str = "PASS" if r["passed"] else "FAIL"
        cost_str   = f"${r['cost_summary'].get('estimated_usd', 0):.4f}" if r["cost_summary"] else ""
        time_str   = f"{r['duration_ms'] / 1000:.1f}s"
        print(f"{status_str}  {r['status'] or '—':<35} {time_str}  {cost_str}")

    # Persist raw results
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    results_path = RESULTS_DIR / f"results_{ts}.json"
    results_path.write_text(json.dumps(results, indent=2))
    print(f"\nRaw results → {results_path}")

    # Generate Markdown report
    from eval.report import generate_report  # noqa: E402 – imported here to avoid circular
    report_path = RESULTS_DIR / f"report_{ts}.md"
    generate_report(results, report_path)
    print(f"Report      → {report_path}")

    # Print quick summary
    n_pass = sum(1 for r in results if r["passed"])
    n_fail = len(results) - n_pass
    total_usd = sum(r["cost_summary"].get("estimated_usd", 0) for r in results)
    total_ms  = sum(r["duration_ms"] for r in results)
    print(f"\nPassed: {n_pass}/{len(results)}  "
          f"Failed: {n_fail}  "
          f"Total cost: ${total_usd:.4f}  "
          f"Total time: {total_ms / 1000:.1f}s")

    sys.exit(0 if n_fail == 0 else 1)


if __name__ == "__main__":
    main()
