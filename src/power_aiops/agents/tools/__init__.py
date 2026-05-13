"""
DynamicCodeAgent 工具系统。

提供可扩展的工具集，供 DynamicCodeAgent 在生成代码时使用。

Usage:
    from power_aiops.agents.tools import get_tool_registry, ToolResult

    # 获取工具注册表
    registry = get_tool_registry()

    # 列出所有工具
    registry.list_tools()

    # 执行工具
    result = registry.execute("file_read", path="README.md")
    if result.success:
        print(result.data)

    # 获取工具提示词（用于 LLM）
    prompt = registry.get_tool_prompt()
"""

from power_aiops.agents.tools.base import (
    Tool,
    ToolCategory,
    ToolMetadata,
    ToolRegistry,
    ToolResult,
    ToolSpec,
    get_tool_registry,
)

__all__ = [
    "Tool",
    "ToolCategory",
    "ToolMetadata",
    "ToolRegistry",
    "ToolResult",
    "ToolSpec",
    "get_tool_registry",
]