import os
import sys
from pathlib import Path
from google import genai
from langsmith import wrappers

from framework.workspace import Workspace
from framework.agents.manager import ManagerAgent
from framework.agents.analysis import AnalysisAgent
from framework.agents.reporter import ReporterAgent
from framework.agents.latex_reporter import LaTeXReporterAgent
from framework.agents.verifier import VerifierAgent
from framework.guardrails import SeismicGuardrails

def load_local_env() -> None:
    env_path = Path(__file__).resolve().parent / ".env"
    if not env_path.exists():
        return

    for raw_line in env_path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)

def main():
    load_local_env()

    if not (os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")):
        raise RuntimeError(
            "Missing API key. Set GOOGLE_API_KEY or GEMINI_API_KEY in your environment or .env file."
        )

    # Initialize Gemini client and wrap with LangSmith
    gemini_client = genai.Client()
    client = wrappers.wrap_gemini(
        gemini_client,
        tracing_extra={
            "tags": ["gemini", "seismic-agent"],
            "metadata": {"integration": "google-genai"},
        },
    )

    # 1. Initialize Workspace
    workspace = Workspace(log_path="mission_log.json")

    # 2. Initialize Agents
    manager  = ManagerAgent("Manager", workspace, client)
    analysis = AnalysisAgent("Analyzer", workspace, client)
    verifier = VerifierAgent("Verifier", workspace, client, guardrails=SeismicGuardrails())
    reporter = ReporterAgent("Reporter", workspace, client)
    latex    = LaTeXReporterAgent("LaTeXReporter", workspace, client)

    # 3. Define a sample query
    query = "Analyze the Marmousi synthetic data for porosity prediction."
    
    # 4. Start the MAS workflow
    print(f"--- Starting Mission: {query} ---\n")
    manager.process_request(query)

    print("\n--- Workflow Lifecycle Summary ---")
    if workspace.state:
        print(f"Status: {workspace.state.status}")
        if workspace.state.report_path:
            print(f"Markdown Report: {workspace.state.report_path}")
    else:
        print("Mission did not start correctly.")

if __name__ == "__main__":
    main()
