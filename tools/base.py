"""
tools/base.py — Base class for all tools.

Every tool must:
  1. Extend BaseTool
  2. Set `name` and `description`
  3. Implement `execute(**kwargs) -> ToolResult`
  4. Optionally override `confirm(**kwargs) -> str` to show a
     preview before execution (user must approve).
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class ToolResult:
    """Standard return type from any tool execution."""
    success: bool
    data: Any = None
    message: str = ""
    error: Optional[str] = None
    metadata: dict = field(default_factory=dict)

    def __str__(self):
        if self.success:
            return self.message if self.message else str(self.data)
        return f"Error: {self.error}"


class BaseTool(ABC):
    """
    Abstract base class for all tools.

    Subclasses must implement:
        - name (str): unique tool identifier
        - description (str): what the tool does
        - execute(**kwargs) -> ToolResult: the actual work

    Optionally override:
        - confirm(**kwargs) -> str: return a human-readable preview
          of what the tool will do. If this returns a non-empty string,
          the user is prompted for confirmation before execute() runs.
        - requires_confirmation (bool): set to False to skip confirmation
    """

    name: str = "base_tool"
    description: str = "Base tool — do not use directly"
    requires_confirmation: bool = True

    def confirm(self, **kwargs) -> str:
        """
        Return a human-readable preview of what this tool will do.
        Override in subclasses to show specifics.
        Return empty string to skip confirmation even if requires_confirmation is True.
        """
        return ""

    @abstractmethod
    def execute(self, **kwargs) -> ToolResult:
        """Execute the tool. Must return a ToolResult."""
        ...

    def run(self, **kwargs) -> ToolResult:
        """
        Full run cycle: confirm (if needed) → execute.
        This is the method callers should use.
        """
        if self.requires_confirmation:
            preview = self.confirm(**kwargs)
            if preview:
                print(f"\n{'─' * 50}")
                print(f"🔧 Tool: {self.name}")
                print(f"{'─' * 50}")
                print(preview)
                print(f"{'─' * 50}")

                try:
                    answer = input("  Execute? [y/N]: ").strip().lower()
                except (EOFError, KeyboardInterrupt):
                    return ToolResult(success=False, error="Cancelled by user")

                if answer not in ("y", "yes"):
                    return ToolResult(success=False, error="Declined by user")

        return self.execute(**kwargs)


class ToolRegistry:
    """
    Registry of available tools.
    Tools are registered by name and can be looked up for execution.
    """

    def __init__(self):
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool):
        """Register a tool instance."""
        self._tools[tool.name] = tool

    def get(self, name: str) -> Optional[BaseTool]:
        """Get a tool by name."""
        return self._tools.get(name)

    def list_tools(self) -> list[str]:
        """List all registered tool names."""
        return list(self._tools.keys())

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    def __getitem__(self, name: str) -> BaseTool:
        return self._tools[name]
