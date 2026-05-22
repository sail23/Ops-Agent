"""
DynamicCodeAgent 工具系统。

提供可扩展的工具集，供 DynamicCodeAgent 在生成代码时使用。
工具通过注册机制动态管理，支持安全沙箱执行。

架构：
- Tool: 工具基类，定义工具接口
- ToolRegistry: 工具注册表，管理所有工具
- 具体工具实现: FileTools, SearchTools, ExecutionTools 等

Usage:
    from power_aiops.agents.tools import get_tool_registry

    registry = get_tool_registry()
    registry.list_tools()  # 列出所有工具
    registry.execute("file_read", {"path": "/path/to/file"})  # 执行工具
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, TypeVar

logger = logging.getLogger(__name__)

# 工具返回值类型
T = TypeVar("T")


class ToolCategory(Enum):
    """工具分类."""

    FILE = "file"
    SEARCH = "search"
    EXECUTION = "execution"
    ANALYSIS = "analysis"
    MONITOR = "monitor"
    REPORT = "report"
    NOTIFY = "notify"


@dataclass
class ToolMetadata:
    """工具元数据."""

    name: str
    description: str
    category: ToolCategory
    parameters: dict[str, Any] = field(default_factory=dict)
    examples: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)


@dataclass
class ToolResult:
    """工具执行结果."""

    success: bool
    data: Any = None
    error: str = ""
    execution_time_ms: float = 0
    tool_name: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


class Tool(ABC):
    """工具基类.

    所有工具必须继承此类并实现 execute 方法。
    """

    def __init__(self) -> None:
        self._logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")

    @property
    @abstractmethod
    def metadata(self) -> ToolMetadata:
        """返回工具元数据."""
        ...

    @abstractmethod
    def execute(self, **kwargs) -> ToolResult:
        """执行工具.

        Args:
            **kwargs: 工具参数

        Returns:
            ToolResult: 执行结果
        """
        ...

    @property
    def name(self) -> str:
        """工具名称."""
        return self.metadata.name

    @property
    def category(self) -> ToolCategory:
        """工具分类."""
        return self.metadata.category

    def validate_params(self, **kwargs) -> tuple[bool, str]:
        """验证参数.

        Args:
            **kwargs: 待验证的参数

        Returns:
            (is_valid, error_message)
        """
        required = self.metadata.parameters.get("required", [])
        for param in required:
            if param not in kwargs or kwargs[param] is None:
                return False, f"Missing required parameter: {param}"
        return True, ""


@dataclass
class ToolSpec:
    """工具规格说明，用于 LLM 调用.

    这个结构用于生成工具调用的自然语言描述。
    """

    name: str
    description: str
    parameters: list[dict[str, Any]]

    def to_markdown(self) -> str:
        """转换为 Markdown 格式的工具说明."""
        lines = [
            f"### {self.name}",
            f"{self.description}",
            "",
            "**参数**:",
        ]
        for param in self.parameters:
            required = "必填" if param.get("required") else "可选"
            lines.append(f"- `{param['name']}` ({param.get('type', 'any')}) [{required}]: {param.get('description', '')}")
        return "\n".join(lines)


class ToolRegistry:
    """工具注册表.

    管理所有可用工具，提供查找和执行功能。
    """

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}
        self._categories: dict[ToolCategory, list[str]] = {}
        self._logger = logging.getLogger(f"{__name__}.ToolRegistry")

    def register(self, tool: Tool) -> None:
        """注册工具.

        Args:
            tool: 工具实例
        """
        name = tool.name
        if name in self._tools:
            self._logger.warning(f"Tool {name} already registered, overwriting")

        self._tools[name] = tool
        category = tool.category
        if category not in self._categories:
            self._categories[category] = []
        if name not in self._categories[category]:
            self._categories[category].append(name)
        self._logger.info(f"Registered tool: {name} (category: {category.value})")

    def unregister(self, name: str) -> bool:
        """注销工具.

        Args:
            name: 工具名称

        Returns:
            是否成功注销
        """
        if name not in self._tools:
            return False

        tool = self._tools.pop(name)
        category = tool.category
        if category in self._categories and name in self._categories[category]:
            self._categories[category].remove(name)
        self._logger.info(f"Unregistered tool: {name}")
        return True

    def get(self, name: str) -> Tool | None:
        """获取工具.

        Args:
            name: 工具名称

        Returns:
            工具实例或 None
        """
        return self._tools.get(name)

    def list_tools(self, category: ToolCategory | None = None) -> list[str]:
        """列出工具.

        Args:
            category: 可选的分类过滤

        Returns:
            工具名称列表
        """
        if category:
            return self._categories.get(category, []).copy()
        return list(self._tools.keys())

    def list_by_category(self) -> dict[ToolCategory, list[str]]:
        """按分类列出所有工具.

        Returns:
            分类到工具名称的映射
        """
        return {cat: names.copy() for cat, names in self._categories.items()}

    def execute(self, name: str, **kwargs) -> ToolResult:
        """执行工具.

        Args:
            name: 工具名称
            **kwargs: 工具参数

        Returns:
            工具执行结果
        """
        import time

        start_time = time.time()
        tool = self._tools.get(name)

        if not tool:
            return ToolResult(
                success=False,
                error=f"Tool not found: {name}",
                tool_name=name,
                execution_time_ms=0,
            )

        # 参数验证
        is_valid, error_msg = tool.validate_params(**kwargs)
        if not is_valid:
            return ToolResult(
                success=False,
                error=error_msg,
                tool_name=name,
                execution_time_ms=0,
            )

        try:
            result = tool.execute(**kwargs)
            result.tool_name = name
            result.execution_time_ms = (time.time() - start_time) * 1000
            return result
        except Exception as e:
            self._logger.exception(f"Tool {name} execution failed")
            return ToolResult(
                success=False,
                error=f"Execution failed: {str(e)}",
                tool_name=name,
                execution_time_ms=(time.time() - start_time) * 1000,
            )

    def get_tool_specs(self) -> list[ToolSpec]:
        """获取所有工具的规格说明，用于 LLM 生成调用.

        Returns:
            工具规格列表
        """
        specs = []
        for tool in self._tools.values():
            spec = ToolSpec(
                name=tool.name,
                description=tool.metadata.description,
                parameters=[
                    {
                        "name": name,
                        "type": param.get("type", "any"),
                        "description": param.get("description", ""),
                        "required": name in tool.metadata.parameters.get("required", []),
                    }
                    for name, param in tool.metadata.parameters.get("properties", {}).items()
                ],
            )
            specs.append(spec)
        return specs

    def get_tool_prompt(self) -> str:
        """获取工具调用提示词.

        Returns:
            格式化的工具说明文本
        """
        lines = ["## 可用工具", ""]
        for spec in self.get_tool_specs():
            lines.append(spec.to_markdown())
            lines.append("")
        return "\n".join(lines)


# 全局工具注册表实例
_registry: ToolRegistry | None = None


def get_tool_registry() -> ToolRegistry:
    """获取全局工具注册表.

    Returns:
        工具注册表单例
    """
    global _registry
    if _registry is None:
        _registry = ToolRegistry()
        _register_default_tools(_registry)
    return _registry


def _register_default_tools(registry: ToolRegistry) -> None:
    """注册默认工具集.

    Args:
        registry: 工具注册表
    """
    # 延迟导入避免循环依赖
    from power_aiops.agents.tools.file_tools import FileReadTool, FileSearchTool, FileWriteTool
    from power_aiops.agents.tools.search_tools import GrepTool, SymbolSearchTool
    from power_aiops.agents.tools.execution_tools import SafeExecTool
    from power_aiops.agents.tools.analysis_tools import CodeAnalysisTool
    from power_aiops.agents.tools.report_tools import MarkdownToDocxTool, MarkdownToPdfTool, ReportTemplateTool
    from power_aiops.agents.tools.notify_tools import MarkdownToHtmlTool, TextTemplateTool, WebhookTool
    from power_aiops.agents.tools.monitor_tools import (
        PrometheusAlertsTool,
        PrometheusRulesTool,
        PrometheusQueryTool,
        PrometheusQueryRangeTool,
        MetricsSummaryTool,
        ConfigQueryTool,
    )

    # 注册文件操作工具
    registry.register(FileReadTool())
    registry.register(FileWriteTool())
    registry.register(FileSearchTool())

    # 注册搜索工具
    registry.register(GrepTool())
    registry.register(SymbolSearchTool())

    # 注册执行工具
    registry.register(SafeExecTool())

    # 注册分析工具
    registry.register(CodeAnalysisTool())

    # 注册报告导出工具
    registry.register(MarkdownToDocxTool())
    registry.register(MarkdownToPdfTool())
    registry.register(ReportTemplateTool())

    # 注册通知工具
    registry.register(WebhookTool())
    registry.register(MarkdownToHtmlTool())
    registry.register(TextTemplateTool())

    # 注册监控工具
    registry.register(PrometheusAlertsTool())
    registry.register(PrometheusRulesTool())
    registry.register(PrometheusQueryTool())
    registry.register(PrometheusQueryRangeTool())
    registry.register(MetricsSummaryTool())
    registry.register(ConfigQueryTool())
