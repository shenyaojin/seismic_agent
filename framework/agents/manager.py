import os
import re
from pathlib import Path
from typing import Any, List, Optional
from google import genai
from framework.workspace import Workspace, MissionSignal, MissionState
from framework.agents.base import BaseAgent


class ManagerAgent(BaseAgent):
    """
    Consolidated Manager Agent: Parses mission requests and verifies data paths.
    """

    def __init__(self, name: str, workspace: Workspace, client: genai.Client, data_dir: str = "data_seismic/"):
        super().__init__(name, workspace, client)
        self.data_dir = Path(data_dir).resolve()

    def _setup_subscriptions(self):
        # Manager is usually the initiator, but could listen for MISSION_FAILED or others.
        pass

    def handle_signal(self, signal: MissionSignal, data: Any = None):
        pass

    def process_request(self, user_query: str):
        """
        Main entry point for the Manager Agent.
        1. Parses the query with LLM.
        2. Verifies data files in data_seismic/.
        3. Updates state and emits DATA_READY.
        """
        self.logger.info(f"Processing mission request: {user_query}")
        
        # 1. Parse query with LLM
        mission_plan = self._parse_query(user_query)
        dataset_keyword = mission_plan.get("dataset_keyword", "").lower()
        analysis_type = mission_plan.get("analysis_type", "general")

        # Create initial mission state
        mission_id = f"mission_{os.urandom(4).hex()}"
        state = MissionState(
            mission_id=mission_id,
            description=user_query,
            plan=mission_plan,
            status="PARSED"
        )
        self.workspace.set_state(state)

        # 2. Verify Data
        relevant_files = self._find_relevant_files(dataset_keyword)
        
        if not relevant_files:
            self.logger.warning(f"No files found for keyword: {dataset_keyword}")
            clarification = self._generate_clarification(user_query, dataset_keyword)
            self.workspace.update_state(status="AWAITING_CLARIFICATION")
            print(f"\n[Manager Clarification]: {clarification}\n")
            return

        # 3. Update State and Emit DATA_READY
        self.logger.info(f"Verified {len(relevant_files)} files: {relevant_files}")
        self.workspace.update_state(
            data_paths=[str(f) for f in relevant_files],
            status="DATA_VERIFIED"
        )
        
        self.workspace.emit(MissionSignal.DATA_READY, data={"files": relevant_files, "analysis": analysis_type})

    def _parse_query(self, query: str) -> dict:
        """
        Uses Gemini to extract the dataset keyword and analysis type.
        """
        prompt = f"""
        Extract the dataset keyword and the analysis type from this geophysical request:
        "{query}"
        
        Return a JSON object with:
        - dataset_keyword: The core name of the dataset or well (e.g., 'Marmousi', 'Gullfaks').
        - analysis_type: What the user wants to do (e.g., 'porosity prediction', 'lithology classification', 'structural interpretation').
        
        Response format: {{"dataset_keyword": "...", "analysis_type": "..."}}
        """
        
        response = self.client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config={"response_mime_type": "application/json"}
        )
        
        import json
        try:
            return json.loads(response.text)
        except Exception:
            self.logger.error("Failed to parse LLM response into JSON.")
            return {"dataset_keyword": query, "analysis_type": "unknown"}

    def _find_relevant_files(self, keyword: str) -> List[Path]:
        """
        Scans data_seismic/ for files matching the keyword.
        Falls back to LLM-assisted matching if exact keyword search yields nothing.
        """
        if not self.data_dir.exists():
            self.logger.error(f"Data directory {self.data_dir} does not exist.")
            return []

        # Only consider actual data files (skip LICENSE, .DS_Store, etc.)
        data_extensions = {".segy", ".sgy", ".las", ".gz", ".tar"}
        all_files = [
            f for f in self.data_dir.iterdir()
            if f.is_file() and f.suffix.lower() in data_extensions
        ]

        # 1. Try exact case-insensitive keyword match
        exact_matches = [f for f in all_files if keyword.lower() in f.name.lower()]
        if exact_matches:
            return exact_matches

        # 2. Fallback: ask LLM which available files correspond to the requested dataset
        self.logger.info(f"No exact match for '{keyword}'. Using LLM to identify relevant files.")
        return self._llm_file_match(keyword, all_files)

    def _llm_file_match(self, keyword: str, available_files: List[Path]) -> List[Path]:
        """
        Uses Gemini to map the dataset keyword to available filenames.
        Returns a subset of available_files judged relevant by the LLM.
        """
        import json as _json

        file_names = [f.name for f in available_files]
        if not file_names:
            return []

        prompt = f"""
        A user requested a dataset described by the keyword: "{keyword}".

        Available seismic data files: {file_names}

        Identify which of these files are most likely part of the requested dataset.
        Consider that well-known synthetic datasets (e.g., Marmousi, SEG/EAGE salt model)
        often have files named after their physical properties (Vp, Vs, MODEL_P-WAVE, etc.).

        Return a JSON object:
        {{"matched_files": ["<filename1>", "<filename2>"]}}

        If none are relevant, return {{"matched_files": []}}.
        """

        try:
            response = self.client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
                config={"response_mime_type": "application/json"}
            )
            result = _json.loads(response.text)
            matched_names = set(result.get("matched_files", []))
            return [f for f in available_files if f.name in matched_names]
        except Exception as e:
            self.logger.error(f"LLM file matching failed: {e}")
            # Last resort: return all available seismic files
            return available_files

    def _generate_clarification(self, query: str, keyword: str) -> str:
        """
        Generates a clarification request if data is missing.
        """
        available_files = [f.name for f in self.data_dir.iterdir() if f.is_file()]
        prompt = f"""
        The user requested: "{query}"
        I extracted the keyword "{keyword}", but I couldn't find matching files in our data folder.
        
        Available files: {available_files}
        
        Write a professional request for the user to clarify their data requirement or suggest one of the available datasets if they might be relevant.
        """
        
        response = self.client.models.generate_content(
            model="gemini-2.0-flash",
            contents=prompt
        )
        return response.text
