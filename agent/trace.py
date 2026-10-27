"""
Trace and Audit Logging system for the Autonomous Financial Reconciliation Agent.
Captures visible, verifiable reasoning traces for human review and automated evaluation (FR-3, FR-5).
"""

import json
import time
from typing import List, Dict, Any, Optional

class TraceStep:
    """Represents a single reasoning cycle (Thought -> Action -> Observation)."""
    def __init__(
        self,
        step_index: int,
        thought: str,
        tool_name: str,
        tool_arguments: Dict[str, Any],
        tool_output: Dict[str, Any],
        duration_ms: float = 0.0
    ):
        self.step_index = step_index
        self.thought = thought
        self.tool_name = tool_name
        self.tool_arguments = tool_arguments
        self.tool_output = tool_output
        self.duration_ms = duration_ms

    def to_dict(self) -> Dict[str, Any]:
        return {
            "step_index": self.step_index,
            "thought": self.thought,
            "tool_name": self.tool_name,
            "tool_arguments": self.tool_arguments,
            "tool_output": self.tool_output,
            "duration_ms": round(self.duration_ms, 2)
        }

class ReasoningTrace:
    """Complete multi-step execution trace for a reconciliation scenario."""
    def __init__(self, prompt: str, context: Optional[Dict[str, Any]] = None):
        self.prompt = prompt
        self.context = context or {}
        self.steps: List[TraceStep] = []
        self.final_recommendation: str = ""
        self.grounded_facts: List[str] = []
        self.verification: str = "none"
        self.is_complete: bool = False
        self.error: Optional[str] = None
        self.total_duration_ms: float = 0.0
        self._start_time: float = time.time()

    def add_step(
        self,
        thought: str,
        tool_name: str,
        tool_arguments: Dict[str, Any],
        tool_output: Dict[str, Any],
        duration_ms: float = 0.0
    ) -> TraceStep:
        step = TraceStep(
            step_index=len(self.steps) + 1,
            thought=thought,
            tool_name=tool_name,
            tool_arguments=tool_arguments,
            tool_output=tool_output,
            duration_ms=duration_ms
        )
        self.steps.append(step)
        return step

    def complete(
        self,
        final_recommendation: str,
        grounded_facts: Optional[List[str]] = None,
        verification: str = "none"
    ):
        self.final_recommendation = final_recommendation
        self.grounded_facts = grounded_facts or []
        self.verification = verification
        self.is_complete = True
        self.total_duration_ms = (time.time() - self._start_time) * 1000.0

    @property
    def tool_sequence(self) -> List[str]:
        return [s.tool_name for s in self.steps]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "prompt": self.prompt,
            "context": self.context,
            "tool_sequence": self.tool_sequence,
            "total_steps": len(self.steps),
            "steps": [s.to_dict() for s in self.steps],
            "final_recommendation": self.final_recommendation,
            "grounded_facts": self.grounded_facts,
            "verification": self.verification,
            "is_complete": self.is_complete,
            "error": self.error,
            "total_duration_ms": round(self.total_duration_ms, 2)
        }

    def format_ascii_trace(self) -> str:
        """Renders an elegant, terminal-friendly visual trace of the agent's thought process."""
        lines = []
        lines.append("=" * 76)
        lines.append(f"AGENT REASONING TRACE: {len(self.steps)} Tool Calls | {self.total_duration_ms:.1f}ms")
        lines.append(f"Prompt: {self.prompt}")
        lines.append("=" * 76)

        for s in self.steps:
            lines.append(f"\n[STEP {s.step_index}] -> Tool: {s.tool_name} ({s.duration_ms:.1f}ms)")
            lines.append(f"  THOUGHT:     {s.thought}")
            lines.append(f"  ACTION ARGS: {json.dumps(s.tool_arguments, indent=2)}")
            # Truncate output representation cleanly if very large
            out_str = json.dumps(s.tool_output, indent=2)
            if len(out_str) > 500:
                out_str = out_str[:500] + "\n    ... [truncated for display]"
            lines.append(f"  OBSERVATION:\n    {out_str}")
            lines.append("-" * 76)

        lines.append("\nFINAL AGENT RECOMMENDATION:")
        lines.append(self.final_recommendation)
        if self.grounded_facts:
            lines.append("\nGrounded Citations & Evidence:")
            for f in self.grounded_facts:
                lines.append(f"  • {f}")
        lines.append("=" * 76)
        return "\n".join(lines)
