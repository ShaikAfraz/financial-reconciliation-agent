"""
Agent Orchestration Engine for Autonomous Financial Reconciliation.
Coordinates multi-turn reasoning loops, tool dispatching, trace capturing, and groundedness enforcement.
"""

import os
import time
from typing import Dict, Any, Optional
from agent.trace import ReasoningTrace
from agent.providers.base import BaseAgentProvider
from agent.providers.deterministic_provider import DeterministicAgentProvider
from agent.providers.gemini_provider import GeminiAgentProvider
from agent.providers.corrupted_provider import CorruptedAgentProvider
from tools.registry import execute_tool

class FinancialReconciliationAgent:
    """
    Autonomous multi-step reconciliation agent that sequences tool calls,
    computes variances, flags anomalies, and synthesizes budget recommendations.
    """

    def __init__(self, provider: Optional[BaseAgentProvider] = None, max_turns: int = 8):
        if provider is not None:
            self.provider = provider
        elif os.environ.get("GEMINI_API_KEY"):
            self.provider = GeminiAgentProvider()
        else:
            self.provider = DeterministicAgentProvider()

        self.max_turns = max_turns

    def run(self, prompt: str, context: Optional[Dict[str, Any]] = None) -> ReasoningTrace:
        """
        Executes autonomous reconciliation on a user prompt and returns the full reasoning trace.
        """
        trace = ReasoningTrace(prompt=prompt, context=context)

        for turn in range(1, self.max_turns + 1):
            t_start = time.time()
            decision = self.provider.plan_step(prompt, trace.context, trace.steps)

            action = decision.get("action")
            thought = decision.get("thought", "")

            if action == "finish":
                final_rec = decision.get("final_recommendation", "Reconciliation completed.")
                facts = decision.get("grounded_facts", [])
                trace.complete(final_recommendation=final_rec, grounded_facts=facts)
                break

            elif action == "tool_call":
                tool_name = decision.get("tool_name", "")
                arguments = decision.get("arguments", {})

                # Execute tool
                exec_result = execute_tool(tool_name, arguments)
                duration_ms = (time.time() - t_start) * 1000.0

                # Handle error injection if running under failure-mode analysis
                if isinstance(self.provider, CorruptedAgentProvider):
                    if exec_result.get("is_success") and "output" in exec_result:
                        exec_result["output"] = self.provider.mutate_observation_if_needed(
                            tool_name, exec_result["output"]
                        )

                # Record step in trace
                trace.add_step(
                    thought=thought,
                    tool_name=tool_name,
                    tool_arguments=arguments,
                    tool_output=exec_result.get("output", exec_result),
                    duration_ms=duration_ms
                )

            else:
                trace.error = f"Unrecognized agent action: {action}"
                trace.complete(final_recommendation="Agent terminated due to unrecognized decision state.")
                break
        else:
            # Reached max turns without finishing
            trace.error = f"Agent reached maximum execution turns ({self.max_turns})."
            trace.complete(final_recommendation="Reconciliation terminated: maximum reasoning steps exceeded.")

        return trace
