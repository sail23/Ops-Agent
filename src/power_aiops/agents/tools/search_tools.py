"""
代码搜索工具集。

提供代码内容搜索、符号搜索等功能。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from power_aiops.agents.tools.base import (
    Tool,
    ToolCategory,
    ToolMetadata,
    ToolResult,
)


class GrepTool(Tool):
    """代码内容搜索工具.

    在文件中搜索匹配正则表达式的内容。
    """

    def __init__(self, max_matches: int = 500) -> None:
        super().__init__()
        self._max_matches = max_matches

    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="grep",
            description="在代码文件中搜索匹配正则表达式的内容。返回匹配的行列号和上下文。",
            category=ToolCategory.SEARCH,
            parameters={
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "正则表达式搜索模式",
                    },
                    "path": {
                        "type": "string",
                        "description": "搜索路径（文件或目录）",
                    },
                    "file_pattern": {
                        "type": "string",
                        "description": "文件过滤模式（默认 *.py）",
                    },
                    "case_sensitive": {
                        "type": "boolean",
                        "description": "是否区分大小写（默认 False）",
                    },
                    "context": {
                        "type": "integer",
                        "description": "上下文行数（默认 0）",
                    },
                    "max_matches": {
                        "type": "integer",
                        "description": f"最大匹配数（默认 {max_matches}）",
                    },
                },
                "required": ["pattern", "path"],
            },
            examples=[
                "grep(pattern='def hello', path='src/')",
                "grep(pattern='class.*Error', path='.', file_pattern='*.py')",
                "grep(pattern='TODO', path='src/', context=2)",
            ],
            tags=["search", "grep", "regex"],
        )

    def execute(self, **kwargs) -> ToolResult:
        pattern = kwargs.get("pattern")
        path = kwargs.get("path")
        file_pattern = kwargs.get("file_pattern", "*.py")
        case_sensitive = kwargs.get("case_sensitive", False)
        context = kwargs.get("context", 0)
        max_matches = kwargs.get("max_matches", self._max_matches)

        try:
            search_path = Path(path).expanduser().resolve()

            # 验证正则表达式
            try:
                flags = 0 if case_sensitive else re.IGNORECASE
                compiled = re.compile(pattern, flags)
            except re.error as e:
                return ToolResult(success=False, error=f"Invalid regex: {e}")

            matches = []

            if search_path.is_file():
                files_to_search = [search_path]
            elif search_path.is_dir():
                files_to_search = list(search_path.rglob(file_pattern))
            else:
                return ToolResult(success=False, error=f"Path not found: {path}")

            # 搜索每个文件
            for file_path in files_to_search:
                if len(matches) >= max_matches:
                    break

                try:
                    with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                        lines = f.readlines()

                    file_matches = []
                    for i, line in enumerate(lines):
                        if compiled.search(line):
                            match_info = {
                                "file": str(file_path.relative_to(search_path.parent)),
                                "line": i + 1,
                                "content": line.rstrip(),
                            }

                            # 添加上下文
                            if context > 0:
                                start = max(0, i - context)
                                end = min(len(lines), i + context + 1)
                                match_info["context"] = {
                                    "before": [l.rstrip() for l in lines[start:i]],
                                    "after": [l.rstrip() for l in lines[i + 1:end]],
                                }

                            file_matches.append(match_info)

                            if len(matches) + len(file_matches) >= max_matches:
                                break

                    matches.extend(file_matches)

                except (PermissionError, UnicodeDecodeError):
                    continue

            total_matches = len(matches)

            return ToolResult(
                success=True,
                data={
                    "pattern": pattern,
                    "path": str(search_path),
                    "file_pattern": file_pattern,
                    "total_matches": total_matches,
                    "matches": matches[:max_matches],
                    "truncated": total_matches > max_matches,
                },
            )

        except Exception as e:
            return ToolResult(success=False, error=f"Search failed: {str(e)}")


class SymbolSearchTool(Tool):
    """代码符号搜索工具.

    搜索函数、类、变量等代码符号定义。
    """

    # 符号类型模式
    SYMBOL_PATTERNS = {
        "function": r"(?:^|(?<=\s))def\s+(\w+)\s*\(",
        "class": r"(?:^|(?<=\s))class\s+(\w+)",
        "async_function": r"(?:^|(?<=\s))async\s+def\s+(\w+)\s*\(",
        "import": r"(?:^|(?<=\s))(?:from\s+[\w.]+\s+)?import\s+(\w+)",
        "constant": r"(?:^|(?<=\s))(\w+)\s*=\s*(?:['\"][^'\"]*['\"]|\d+)",
        "decorator": r"@\w+",
    }

    def __init__(self) -> None:
        super().__init__()

    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="symbol_search",
            description="搜索代码中的符号定义（函数、类、变量等）。",
            category=ToolCategory.SEARCH,
            parameters={
                "properties": {
                    "symbol_type": {
                        "type": "string",
                        "description": "符号类型: function, class, async_function, import, decorator",
                    },
                    "path": {
                        "type": "string",
                        "description": "搜索路径（文件或目录）",
                    },
                    "file_pattern": {
                        "type": "string",
                        "description": "文件过滤模式（默认 *.py）",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "最大返回结果数（默认 200）",
                    },
                },
                "required": ["symbol_type", "path"],
            },
            examples=[
                "symbol_search(symbol_type='function', path='src/')",
                "symbol_search(symbol_type='class', path='models.py')",
                "symbol_search(symbol_type='async_function', path='api/')",
            ],
            tags=["search", "symbol", "function", "class"],
        )

    def execute(self, **kwargs) -> ToolResult:
        symbol_type = kwargs.get("symbol_type")
        path = kwargs.get("path")
        file_pattern = kwargs.get("file_pattern", "*.py")
        max_results = kwargs.get("max_results", 200)

        try:
            search_path = Path(path).expanduser().resolve()

            # 获取对应的正则模式
            if symbol_type not in self.SYMBOL_PATTERNS:
                return ToolResult(
                    success=False,
                    error=f"Unknown symbol type: {symbol_type}. Available: {list(self.SYMBOL_PATTERNS.keys())}",
                )

            pattern = self.SYMBOL_PATTERNS[symbol_type]
            compiled = re.compile(pattern, re.MULTILINE)

            symbols = []

            if search_path.is_file():
                files_to_search = [search_path]
            elif search_path.is_dir():
                files_to_search = list(search_path.rglob(file_pattern))
            else:
                return ToolResult(success=False, error=f"Path not found: {path}")

            for file_path in files_to_search:
                if len(symbols) >= max_results:
                    break

                try:
                    with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                        content = f.read()

                    for match in compiled.finditer(content):
                        symbol_name = match.group(1) if match.lastindex else match.group(0)

                        # 找到行号
                        line_num = content[:match.start()].count("\n") + 1

                        # 获取该行内容
                        lines = content.split("\n")
                        line_content = lines[line_num - 1].strip() if line_num <= len(lines) else ""

                        symbols.append({
                            "name": symbol_name,
                            "type": symbol_type,
                            "file": str(file_path.relative_to(search_path.parent)),
                            "line": line_num,
                            "definition": line_content,
                        })

                except (PermissionError, UnicodeDecodeError):
                    continue

            # 按文件排序
            symbols.sort(key=lambda x: (x["file"], x["line"]))

            return ToolResult(
                success=True,
                data={
                    "symbol_type": symbol_type,
                    "path": str(search_path),
                    "file_pattern": file_pattern,
                    "total": len(symbols),
                    "symbols": symbols[:max_results],
                    "truncated": len(symbols) > max_results,
                },
            )

        except Exception as e:
            return ToolResult(success=False, error=f"Symbol search failed: {str(e)}")
