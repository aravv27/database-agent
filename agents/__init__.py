"""
agents — Extensible agent system.

Each agent extends BaseAgent and defines its own system prompt,
response schema, and output formatting. Agents share the same
LLM client and context formatting utilities.
"""

from agents.base import BaseAgent, AgentRegistry

__all__ = ["BaseAgent", "AgentRegistry"]
