from abc import ABC, abstractmethod
from typing import Any
from google import genai
from framework.workspace import Workspace, MissionSignal


class BaseAgent(ABC):
    """
    Abstract Base Class for all agents in the Multi-Agent System.
    """

    def __init__(self, name: str, workspace: Workspace, client: genai.Client):
        self.name = name
        self.workspace = workspace
        self.client = client
        self.logger = workspace.logger
        self._setup_subscriptions()

    @abstractmethod
    def _setup_subscriptions(self):
        """
        Define which signals this agent listens to.
        """
        pass

    @abstractmethod
    def handle_signal(self, signal: MissionSignal, data: Any = None):
        """
        Logic for processing an incoming signal.
        """
        pass
