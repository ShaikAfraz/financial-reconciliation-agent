"""
Abstract Base Class for Agent Providers.
Allows pluggable execution via Google Gemini API, deterministic planner, or error injector.
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, List
from agent.trace import TraceStep

class BaseAgentProvider(ABC):
    @abstractmethod
    def plan_step(
        self,
        prompt: str,
        context: Dict[str, Any],
        history: List[TraceStep]
    ) -> Dict[str, Any]:
        """
        Evaluates the current task, context, and prior trace history to decide next action.
        
        Returns:
            Dict indicating either:
            - {"action": "tool_call", "thought": str, "tool_name": str, "arguments": dict}
            - {"action": "finish", "thought": str, "final_recommendation": str, "grounded_facts": list}
        """
        pass
