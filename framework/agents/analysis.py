from typing import Any
from google import genai
from framework.workspace import Workspace, MissionSignal
from framework.agents.base import BaseAgent


class AnalysisAgent(BaseAgent):
    """
    Analysis Agent: Triggered by DATA_READY, executes ML models and summarizes findings.
    """

    def _setup_subscriptions(self):
        self.workspace.subscribe(MissionSignal.DATA_READY, self.handle_signal)

    def handle_signal(self, signal: MissionSignal, data: Any = None):
        if signal == MissionSignal.DATA_READY:
            files = data.get("files", [])
            analysis_type = data.get("analysis", "general")
            self.logger.info(f"Analysis Agent triggered for: {analysis_type} on {len(files)} files.")
            self.run_analysis(files, analysis_type)

    def run_analysis(self, files: list, analysis_type: str):
        """
        Simulates ML processing and generates text-based insights.
        """
        file_names = [f.name for f in files]
        
        prompt = f"""
        You are an expert Geophysicist. 
        Perform a simulated {analysis_type} analysis on the following seismic data files:
        {file_names}
        
        Since this is a simulation, provide:
        1. A summary of the likely data content (based on file names).
        2. A set of simulated numerical findings (e.g., average porosity: 18%, high-velocity zones detected).
        3. A technical interpretation of these findings.
        
        Keep the tone professional and academic.
        """
        
        response = self.client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt
        )
        
        insights = response.text
        self.logger.info("Analysis complete.")
        
        # Update workspace state with results
        self.workspace.update_state(
            analysis_results={
                "type": analysis_type,
                "insights": insights,
                "files_processed": [str(f) for f in files]
            },
            status="ANALYSIS_COMPLETED"
        )
        
        self.workspace.emit(MissionSignal.ANALYSIS_COMPLETE, data={"insights": insights})
