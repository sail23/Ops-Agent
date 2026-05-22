"""
文件操作工具集。

提供安全的文件读写和搜索功能。
"""

from __future__ import annotations

from pathlib import Path

from power_aiops.agents.tools.base import (
    Tool,
    ToolCategory,
    ToolMetadata,
    ToolResult,
)


class FileReadTool(Tool):
    """文件读取工具.

    安全地读取文件内容，支持编码检测和内容限制。
    """

    def __init__(self, max_size_kb: int = 512) -> None:
        super().__init__()
        self._max_size_kb = max_size_kb

    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="file_read",
            description="读取文件内容。返回文件的前 N 行或后 N 行，或指定范围的行。",
            category=ToolCategory.FILE,
            parameters={
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "文件路径（绝对路径或相对于当前目录）",
                    },
                    "start_line": {
                        "type": "integer",
                        "description": "起始行号（从 1 开始，不指定则从头开始）",
                    },
                    "end_line": {
                        "type": "integer",
                        "description": "结束行号（不指定则读到文件末尾）",
                    },
                    "encoding": {
                        "type": "string",
                        "description": "文件编码（默认 utf-8）",
                    },
                },
                "required": ["path"],
            },
            examples=[
                "file_read(path='src/main.py')",
                "file_read(path='config.json', start_line=1, end_line=50)",
                "file_read(path='./utils/helper.py', encoding='utf-8')",
            ],
            tags=["read", "file", "source"],
        )

    def execute(self, **kwargs) -> ToolResult:
        path = kwargs.get("path")
        start_line = kwargs.get("start_line", 1)
        end_line = kwargs.get("end_line")
        encoding = kwargs.get("encoding", "utf-8")

        try:
            file_path = Path(path).expanduser().resolve()

            # 安全检查：禁止访问危险路径
            dangerous_paths = [
                "/etc/passwd",
                "/etc/shadow",
                "/etc/sudoers",
                ".ssh",
                ".aws",
                ".config",
            ]
            for dangerous in dangerous_paths:
                if str(file_path).startswith(dangerous) or dangerous in str(file_path):
                    return ToolResult(
                        success=False,
                        error=f"Access denied: {dangerous} path not allowed",
                    )

            # 检查文件是否存在
            if not file_path.exists():
                return ToolResult(success=False, error=f"File not found: {path}")

            if not file_path.is_file():
                return ToolResult(success=False, error=f"Not a file: {path}")

            # 检查文件大小
            file_size = file_path.stat().st_size
            if file_size > self._max_size_kb * 1024:
                return ToolResult(
                    success=False,
                    error=f"File too large ({file_size / 1024:.1f}KB > {self._max_size_kb}KB limit)",
                )

            # 读取文件
            with open(file_path, "r", encoding=encoding, errors="replace") as f:
                lines = f.readlines()

            # 提取指定范围
            start_idx = max(0, start_line - 1)
            if end_line is not None:
                end_idx = min(len(lines), end_line)
            else:
                end_idx = len(lines)

            content = "".join(lines[start_idx:end_idx])

            return ToolResult(
                success=True,
                data={
                    "path": str(file_path),
                    "lines": len(lines),
                    "read_lines": end_idx - start_idx,
                    "content": content,
                    "truncated": end_line is None and len(lines) > 1000,
                },
            )

        except UnicodeDecodeError as e:
            return ToolResult(success=False, error=f"Encoding error: {e}")
        except PermissionError:
            return ToolResult(success=False, error=f"Permission denied: {path}")
        except Exception as e:
            return ToolResult(success=False, error=f"Read failed: {str(e)}")


class FileWriteTool(Tool):
    """文件写入工具.

    安全地写入文件内容到指定路径。
    默认不覆盖已有文件，除非明确指定。
    """

    def __init__(self, allowed_dir: str | None = None) -> None:
        super().__init__()
        self._allowed_dir = allowed_dir

    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="file_write",
            description="写入内容到文件。如果文件已存在且未指定覆盖，则返回错误。",
            category=ToolCategory.FILE,
            parameters={
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "文件路径",
                    },
                    "content": {
                        "type": "string",
                        "description": "要写入的内容",
                    },
                    "overwrite": {
                        "type": "boolean",
                        "description": "是否覆盖已有文件（默认 False）",
                    },
                    "encoding": {
                        "type": "string",
                        "description": "文件编码（默认 utf-8）",
                    },
                },
                "required": ["path", "content"],
            },
            examples=[
                "file_write(path='output.txt', content='Hello World')",
                "file_write(path='script.py', content=code, overwrite=True)",
            ],
            tags=["write", "file", "create"],
        )

    def execute(self, **kwargs) -> ToolResult:
        path = kwargs.get("path")
        content = kwargs.get("content", "")
        overwrite = kwargs.get("overwrite", False)
        encoding = kwargs.get("encoding", "utf-8")

        try:
            file_path = Path(path).expanduser().resolve()

            # 安全检查：禁止写入危险路径
            dangerous_paths = [
                "/etc",
                "/bin",
                "/sbin",
                "/usr/bin",
                "/usr/sbin",
                "/boot",
                "/sys",
                "/proc",
                ".ssh",
                ".aws",
            ]
            for dangerous in dangerous_paths:
                if str(file_path).startswith(dangerous):
                    return ToolResult(
                        success=False,
                        error=f"Access denied: cannot write to {dangerous}",
                    )

            # 检查目录是否存在
            file_path.parent.mkdir(parents=True, exist_ok=True)

            # 检查文件是否已存在
            if file_path.exists() and not overwrite:
                return ToolResult(
                    success=False,
                    error=f"File already exists (use overwrite=True to replace): {path}",
                )

            # 写入文件
            with open(file_path, "w", encoding=encoding) as f:
                f.write(content)

            return ToolResult(
                success=True,
                data={
                    "path": str(file_path),
                    "bytes_written": len(content.encode(encoding)),
                    "lines_written": len(content.splitlines()),
                },
            )

        except PermissionError:
            return ToolResult(success=False, error=f"Permission denied: {path}")
        except Exception as e:
            return ToolResult(success=False, error=f"Write failed: {str(e)}")


class FileSearchTool(Tool):
    """文件搜索工具.

    在目录中搜索匹配条件的文件。
    """

    def __init__(self, max_results: int = 100) -> None:
        super().__init__()
        self._max_results = max_results

    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="file_search",
            description="在目录中搜索匹配的文件。支持 glob 模式和名称匹配。",
            category=ToolCategory.FILE,
            parameters={
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "文件名模式（支持 glob，如 *.py, test_*.py）",
                    },
                    "root_dir": {
                        "type": "string",
                        "description": "搜索根目录（默认当前目录）",
                    },
                    "recursive": {
                        "type": "boolean",
                        "description": "是否递归搜索子目录（默认 True）",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": f"最大返回结果数（默认 {max_results}）",
                    },
                },
                "required": ["pattern"],
            },
            examples=[
                "file_search(pattern='*.py', root_dir='src')",
                "file_search(pattern='**/test_*.py')",
                "file_search(pattern='config.*', recursive=False)",
            ],
            tags=["search", "file", "glob"],
        )

    def execute(self, **kwargs) -> ToolResult:
        pattern = kwargs.get("pattern")
        root_dir = kwargs.get("root_dir", ".")
        recursive = kwargs.get("recursive", True)
        max_results = kwargs.get("max_results", self._max_results)

        try:
            root_path = Path(root_dir).expanduser().resolve()

            # 安全检查
            if not root_path.exists():
                return ToolResult(success=False, error=f"Directory not found: {root_dir}")

            if not root_path.is_dir():
                return ToolResult(success=False, error=f"Not a directory: {root_dir}")

            # 搜索文件
            if recursive:
                matches = list(root_path.rglob(pattern))
            else:
                matches = list(root_path.glob(pattern))

            # 限制结果数量
            total = len(matches)
            matches = matches[:max_results]

            # 转换为相对路径并排序
            results = []
            for path in matches:
                if path.is_file():
                    try:
                        rel_path = path.relative_to(root_path)
                        results.append({
                            "name": path.name,
                            "path": str(path),
                            "relative": str(rel_path),
                            "size": path.stat().st_size,
                        })
                    except ValueError:
                        # 跨驱动器情况
                        results.append({
                            "name": path.name,
                            "path": str(path),
                            "relative": str(path),
                            "size": path.stat().st_size,
                        })

            results.sort(key=lambda x: x["name"])

            return ToolResult(
                success=True,
                data={
                    "pattern": pattern,
                    "root_dir": str(root_path),
                    "total_matches": total,
                    "returned": len(results),
                    "files": results,
                    "truncated": total > max_results,
                },
            )

        except Exception as e:
            return ToolResult(success=False, error=f"Search failed: {str(e)}")
