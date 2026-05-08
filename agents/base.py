"""
agents/base.py — Abstract base class for all agents.

Every agent must:
  1. Extend BaseAgent
  2. Set `name`, `description`, `system_prompt`, `response_schema`
  3. Implement `format_result(result) -> str` for terminal display

The base class handles:
  - NVIDIA NIM client setup (shared across agents)
  - Context formatting (converts retrieval context → text block)
  - LLM call with JSON structured output
  - Error handling + fallback
"""

import os
import json
import time
from abc import ABC, abstractmethod
from typing import Any, Optional

from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

# ── Shared LLM client (one per process) ────────────────────────────────────

_client = None


def get_llm_client():
    """Get or create the shared NVIDIA NIM client."""
    global _client
    if _client is None:
        _client = OpenAI(
            base_url="https://integrate.api.nvidia.com/v1",
            api_key=os.getenv("NVIDIA_API_KEY"),
        )
    return _client


DEFAULT_MODEL = "mistralai/mistral-nemotron"


# ── Base Agent ──────────────────────────────────────────────────────────────

class BaseAgent(ABC):
    """
    Abstract base for all agents.

    Subclasses must define:
        - name (str): unique agent identifier
        - description (str): what this agent does
        - system_prompt (str): the system message sent to the LLM
        - response_schema (dict): JSON schema the LLM must follow
        - format_result(result: dict) -> str: format output for terminal

    Optionally override:
        - model (str): which NVIDIA NIM model to use
        - temperature (float): LLM temperature
        - max_tokens (int): max response tokens
        - build_user_prompt(query, context) -> str: customize the user message
        - format_context(context) -> str: customize context formatting
        - post_process(result) -> dict: transform LLM output before returning
    """

    name: str = "base_agent"
    description: str = "Base agent — do not use directly"
    system_prompt: str = ""
    response_schema: dict = {}
    model: str = DEFAULT_MODEL
    temperature: float = 0.1
    max_tokens: int = 800
    needs_context: bool = True   # Set False for agents that don't need retrieval (e.g. schema design)

    def get_system_prompt(self, context: dict) -> str:
        """
        Return the system prompt. Override to select prompt based on context
        (e.g., switching between SQLite and PostgreSQL prompts by dialect).
        """
        return self.system_prompt


    def format_context(self, context: dict) -> str:
        """
        Convert a retrieval context package into a text block for the LLM.
        Override to customize per agent.
        """
        lines = ["=== Database Context ==="]

        # Focus tables with descriptions
        lines.append(f"\nFocus tables: {', '.join(context.get('focus_tables', []))}")
        for t in context.get("focus_tables", []):
            desc = context.get("descriptions", {}).get(t, "")
            lines.append(f"\n  [{t}]: {desc}")
            cols = context.get("columns", {}).get(t, [])
            for c in cols:
                pk = " PK" if c.get("primary_key") else ""
                null = " NULL" if c.get("nullable") else " NOT NULL"
                lines.append(f"    - {c['name']} {c['type']}{pk}{null}")

        # Join paths
        if context.get("join_paths"):
            lines.append("\nJoin paths:")
            for jp in context["join_paths"]:
                null_tag = " (optional)" if jp.get("nullable") else ""
                lines.append(
                    f"  {jp['from']}.{jp['fk_column']} -> "
                    f"{jp['to']}.{jp['ref_column']}{null_tag}"
                )

        # Auth linkage
        if context.get("auth_linkage"):
            lines.append(f"\nAuth linkage: {context['auth_linkage']}")

        # Patterns & hints
        if context.get("hints"):
            lines.append("\nGeneration hints:")
            for h in context["hints"]:
                lines.append(f"  - {h}")

        return "\n".join(lines)

    def build_user_prompt(self, query: str, context: dict) -> str:
        """
        Build the user message sent to the LLM.
        Override to customize per agent.
        """
        ctx_text = self.format_context(context)
        return (
            f"Respond with JSON matching this schema:\n"
            f"{json.dumps(self.response_schema, indent=2)}\n\n"
            f"{ctx_text}\n\n"
            f"=== User Request ===\n{query}"
        )

    def post_process(self, result: dict) -> dict:
        """
        Transform the raw LLM JSON output before returning.
        Override to add defaults, validate fields, etc.
        """
        return result

    @abstractmethod
    def format_result(self, result: dict) -> str:
        """
        Format the agent's result dict for terminal display.
        Must be implemented by every agent.
        """
        ...

    def ask(self, query: str, context: dict) -> dict:
        """
        Full agent cycle: build prompt -> call LLM -> parse JSON -> post-process.
        Returns the structured result dict, with '_usage' metrics attached.
        """
        client = get_llm_client()
        user_prompt = self.build_user_prompt(query, context)
        prompt_chars = len(user_prompt)

        try:
            t_call = time.perf_counter()
            response = client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": self.get_system_prompt(context)},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                response_format={"type": "json_object"},
            )
            latency_ms = round((time.perf_counter() - t_call) * 1000, 1)

            raw = response.choices[0].message.content.strip()
            result = json.loads(raw)
            result = self.post_process(result)

            result["_usage"] = {
                "prompt_tokens":      response.usage.prompt_tokens,
                "completion_tokens":  response.usage.completion_tokens,
                "total_tokens":       response.usage.total_tokens,
                "finish_reason":      response.choices[0].finish_reason,
                "latency_ms":         latency_ms,
                "prompt_chars":       prompt_chars,
            }
            return result

        except Exception as e:
            result = self._error_result(str(e))
            result["_usage"] = {
                "prompt_tokens":      0,
                "completion_tokens":  0,
                "total_tokens":       0,
                "finish_reason":      "error",
                "latency_ms":         0.0,
                "prompt_chars":       prompt_chars,
            }
            return result

    def _error_result(self, error_msg: str) -> dict:
        """Return a standardized error result. Override if your schema differs."""
        return {"_error": error_msg}

    def print_result(self, result: dict):
        """Format and print the result to terminal."""
        output = self.format_result(result)
        print(output)


# ── Agent Registry ──────────────────────────────────────────────────────────

class AgentRegistry:
    """
    Registry of available agents.
    Agents are registered by name and can be switched at runtime.
    """

    def __init__(self):
        self._agents: dict[str, BaseAgent] = {}
        self._active: Optional[str] = None

    def register(self, agent: BaseAgent):
        """Register an agent instance."""
        self._agents[agent.name] = agent
        # First registered agent becomes active by default
        if self._active is None:
            self._active = agent.name

    def get(self, name: str) -> Optional[BaseAgent]:
        """Get an agent by name."""
        return self._agents.get(name)

    @property
    def active(self) -> Optional[BaseAgent]:
        """Get the currently active agent."""
        if self._active:
            return self._agents.get(self._active)
        return None

    def set_active(self, name: str) -> bool:
        """Switch the active agent. Returns True if successful."""
        if name in self._agents:
            self._active = name
            return True
        return False

    def list_agents(self) -> list[str]:
        """List all registered agent names."""
        return list(self._agents.keys())

    def __contains__(self, name: str) -> bool:
        return name in self._agents

    def __getitem__(self, name: str) -> BaseAgent:
        return self._agents[name]
