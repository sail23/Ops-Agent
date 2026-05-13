"""
代码分析工具集。

提供代码静态分析和理解能力。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any

from power_aiops.agents.tools.base import (
    Tool,
    ToolCategory,
    ToolMetadata,
    ToolResult,
)


class CodeAnalysisTool(Tool):
    """Python 代码静态分析工具.

    分析代码结构、依赖、复杂度等。
    """

    def __init__(self) -> None:
        super().__init__()

    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="code_analysis",
            description="对 Python 代码进行静态分析，返回代码结构、依赖、复杂度等信息。",
            category=ToolCategory.ANALYSIS,
            parameters={
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "要分析的 Python 代码",
                    },
                    "path": {
                        "type": "string",
                        "description": "文件路径（二选一，与 code 互斥）",
                    },
                    "analysis_type": {
                        "type": "string",
                        "description": "分析类型: structure, imports, functions, classes, all (默认 all)",
                    },
                },
                "required": [],
            },
            examples=[
                "code_analysis(code='import os\\nprint(os.getcwd())')",
                "code_analysis(path='src/main.py', analysis_type='functions')",
            ],
            tags=["analyze", "ast", "static", "python"],
        )

    def execute(self, **kwargs) -> ToolResult:
        code = kwargs.get("code")
        path = kwargs.get("path")
        analysis_type = kwargs.get("analysis_type", "all")

        # 获取代码内容
        if path:
            try:
                file_path = Path(path).expanduser().resolve()
                if not file_path.exists():
                    return ToolResult(success=False, error=f"File not found: {path}")
                with open(file_path, "r", encoding="utf-8") as f:
                    code = f.read()
            except Exception as e:
                return ToolResult(success=False, error=f"Failed to read file: {e}")
        elif not code:
            return ToolResult(success=False, error="Either code or path must be provided")

        try:
            # 解析 AST
            tree = ast.parse(code)

            result = {}

            if analysis_type in ("structure", "all"):
                result["structure"] = self._analyze_structure(tree)

            if analysis_type in ("imports", "all"):
                result["imports"] = self._analyze_imports(tree)

            if analysis_type in ("functions", "all"):
                result["functions"] = self._analyze_functions(tree)

            if analysis_type in ("classes", "all"):
                result["classes"] = self._analyze_classes(tree)

            return ToolResult(success=True, data=result)

        except SyntaxError as e:
            return ToolResult(success=False, error=f"Syntax error: {e}")
        except Exception as e:
            return ToolResult(success=False, error=f"Analysis failed: {str(e)}")

    def _analyze_structure(self, tree: ast.AST) -> dict[str, Any]:
        """分析代码整体结构."""
        return {
            "total_lines": len(tree.body) if hasattr(tree, "body") else 0,
            "module_docstring": ast.get_docstring(tree) or "",
        }

    def _analyze_imports(self, tree: ast.AST) -> dict[str, Any]:
        """分析导入语句."""
        imports = []
        from_imports = []

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append({
                        "name": alias.name,
                        "alias": alias.asname or alias.name.split(".")[-1],
                    })
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                for alias in node.names:
                    from_imports.append({
                        "module": module,
                        "name": alias.name,
                        "alias": alias.asname or alias.name,
                    })

        return {
            "imports": imports,
            "from_imports": from_imports,
            "total_imports": len(imports) + len(from_imports),
        }

    def _analyze_functions(self, tree: ast.AST) -> dict[str, Any]:
        """分析函数定义."""
        functions = []

        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) or isinstance(node, ast.AsyncFunctionDef):
                func_info = {
                    "name": node.name,
                    "line": node.lineno,
                    "is_async": isinstance(node, ast.AsyncFunctionDef),
                    "args": [arg.arg for arg in node.args.args],
                    "defaults": len(node.args.defaults),
                    "decorators": [self._get_decorator_name(d) for d in node.decorator_list],
                    "docstring": ast.get_docstring(node) or "",
                    "complexity": self._estimate_complexity(node),
                }
                functions.append(func_info)

        return {
            "functions": functions,
            "total": len(functions),
            "async_count": sum(1 for f in functions if f["is_async"]),
        }

    def _analyze_classes(self, tree: ast.AST) -> dict[str, Any]:
        """分析类定义."""
        classes = []

        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                class_info = {
                    "name": node.name,
                    "line": node.lineno,
                    "bases": [self._get_name_from_expr(base) for base in node.bases],
                    "decorators": [self._get_decorator_name(d) for d in node.decorator_list],
                    "docstring": ast.get_docstring(node) or "",
                    "methods": [],
                    "class_attributes": [],
                }

                for item in node.body:
                    if isinstance(item, ast.FunctionDef):
                        class_info["methods"].append({
                            "name": item.name,
                            "line": item.lineno,
                            "is_static": any(self._get_decorator_name(d) == "staticmethod" for d in item.decorator_list),
                            "is_classmethod": any(self._get_decorator_name(d) == "classmethod" for d in item.decorator_list),
                        })
                    elif isinstance(item, ast.AnnAssign) and item.target:
                        class_info["class_attributes"].append({
                            "name": self._get_name_from_expr(item.target),
                            "has_annotation": item.annotation is not None,
                        })

                classes.append(class_info)

        return {
            "classes": classes,
            "total": len(classes),
        }

    def _estimate_complexity(self, node: ast.AST) -> int:
        """估算代码复杂度."""
        complexity = 1
        for child in ast.walk(node):
            if isinstance(child, (ast.If, ast.While, ast.For, ast.AsyncFor)):
                complexity += 1
            elif isinstance(child, ast.BoolOp):
                complexity += len(child.values) - 1
        return complexity

    def _get_decorator_name(self, node: ast.AST) -> str:
        """获取装饰器名称."""
        if isinstance(node, ast.Name):
            return node.id
        elif isinstance(node, ast.Attribute):
            parts = []
            current = node
            while isinstance(current, ast.Attribute):
                parts.append(current.attr)
                current = current.value
            if isinstance(current, ast.Name):
                parts.append(current.id)
            return ".".join(reversed(parts))
        elif isinstance(node, ast.Call):
            return self._get_decorator_name(node.func)
        return "unknown"

    def _get_name_from_expr(self, node: ast.AST) -> str:
        """从表达式获取名称."""
        if isinstance(node, ast.Name):
            return node.id
        elif isinstance(node, ast.Attribute):
            return f"{self._get_name_from_expr(node.value)}.{node.attr}"
        elif isinstance(node, ast.Subscript):
            return self._get_name_from_expr(node.value)
        return "unknown"


class DocstringExtractorTool(Tool):
    """文档字符串提取工具.

    从代码中提取函数、类、模块的文档字符串。
    """

    def __init__(self) -> None:
        super().__init__()

    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="docstring_extract",
            description="提取 Python 代码中的文档字符串，支持提取模块、类、函数的文档。",
            category=ToolCategory.ANALYSIS,
            parameters={
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "要提取文档的 Python 代码",
                    },
                    "path": {
                        "type": "string",
                        "description": "文件路径",
                    },
                    "target": {
                        "type": "string",
                        "description": "目标类型: module, class, function, all (默认 all)",
                    },
                    "name": {
                        "type": "string",
                        "description": "具体名称（可选，如不指定则返回所有）",
                    },
                },
                "required": [],
            },
            examples=[
                "docstring_extract(code='def foo(): \\\"\\\"\\\"Doc\\\"\\\"\\\" pass')",
                "docstring_extract(path='src/utils.py', target='class')",
            ],
            tags=["docstring", "documentation", "help"],
        )

    def execute(self, **kwargs) -> ToolResult:
        code = kwargs.get("code")
        path = kwargs.get("path")
        target = kwargs.get("target", "all")
        name = kwargs.get("name")

        # 获取代码
        if path:
            try:
                with open(Path(path).expanduser().resolve(), "r", encoding="utf-8") as f:
                    code = f.read()
            except Exception as e:
                return ToolResult(success=False, error=f"Failed to read file: {e}")
        elif not code:
            return ToolResult(success=False, error="Either code or path must be provided")

        try:
            tree = ast.parse(code)
            result = {}

            if target in ("module", "all"):
                result["module"] = {
                    "docstring": ast.get_docstring(tree) or "",
                    "line": 1,
                }

            if target in ("class", "all"):
                result["classes"] = []
                for node in ast.walk(tree):
                    if isinstance(node, ast.ClassDef):
                        if name and node.name != name:
                            continue
                        result["classes"].append({
                            "name": node.name,
                            "line": node.lineno,
                            "docstring": ast.get_docstring(node) or "",
                        })

            if target in ("function", "all"):
                result["functions"] = []
                for node in ast.walk(tree):
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        if name and node.name != name:
                            continue
                        result["functions"].append({
                            "name": node.name,
                            "line": node.lineno,
                            "is_async": isinstance(node, ast.AsyncFunctionDef),
                            "docstring": ast.get_docstring(node) or "",
                        })

            return ToolResult(success=True, data=result)

        except SyntaxError as e:
            return ToolResult(success=False, error=f"Syntax error: {e}")
        except Exception as e:
            return ToolResult(success=False, error=f"Extraction failed: {str(e)}")
