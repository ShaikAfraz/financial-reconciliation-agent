"""
Corrupted Agent Provider for FR-5 Error Propagation Analysis.
Deliberately injects an upstream miscategorization at Step 1 to observe and quantify
how errors cascade through downstream budget checking, anomaly detection, variance, and reallocation.
"""

from typing import Dict, Any, List
from agent.providers.deterministic_provider import DeterministicAgentProvider
from agent.trace import TraceStep

class CorruptedAgentProvider(DeterministicAgentProvider):
    """
    Simulates early classification errors (e.g. misclassifying a $480 dining charge as Utilities).
    Directly measures downstream corruption rates across steps 2 to 5.
    """

    def __init__(self, corrupted_category: str):
        super().__init__()
        self.corrupted_category = corrupted_category
        self.has_injected = False

    def plan_step(
        self,
        prompt: str,
        context: Dict[str, Any],
        history: List[TraceStep]
    ) -> Dict[str, Any]:
        decision = super().plan_step(prompt, context, history)

        # If next action is a tool call that depends on category, ensure the corrupted category propagates
        if decision.get("action") == "tool_call":
            tool_name = decision["tool_name"]
            args = decision["arguments"]

            # If flag_anomaly is being called and categorization was already done, ensure corrupted category is passed
            if tool_name == "flag_anomaly" and self.corrupted_category:
                args["category"] = self.corrupted_category

            # If check_against_budget_rule is being called, ensure corrupted category is passed
            if tool_name == "check_against_budget_rule" and self.corrupted_category:
                args["category"] = self.corrupted_category

        return decision

    def mutate_observation_if_needed(self, tool_name: str, output: Dict[str, Any]) -> Dict[str, Any]:
        """Mutates the categorization output at step 1 to inject the miscategorization."""
        if tool_name == "categorize_transaction" and not self.has_injected:
            original_cat = output.get("category")
            output["original_unperturbed_category"] = original_cat
            output["category"] = self.corrupted_category
            output["confidence"] = 0.88
            output["matched_pattern"] = "INJECTED_CORRUPTED_PATTERN"
            output["reasoning"] = f"[ERROR INJECTION]: Deliberately misclassified as '{self.corrupted_category}' (was '{original_cat}')."
            self.has_injected = True
        return output
