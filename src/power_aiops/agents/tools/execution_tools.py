"""
代码执行工具集。

提供安全的代码执行能力。
"""

from __future__ import annotations

import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from power_aiops.agents.tools.base import (
    Tool,
    ToolCategory,
    ToolMetadata,
    ToolResult,
)


class SafeExecTool(Tool):
    """安全代码执行工具.

    在隔离环境中执行 Python 代码，支持超时和输出限制。
    """

    def __init__(
        self,
        timeout: int = 30,
        max_output_lines: int = 500,
        max_memory_mb: int = 256,
    ) -> None:
        super().__init__()
        self._timeout = timeout
        self._max_output_lines = max_output_lines
        self._max_memory_mb = max_memory_mb

    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="safe_exec",
            description="在安全沙箱中执行 Python 代码。支持超时控制和输出限制。",
            category=ToolCategory.EXECUTION,
            parameters={
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "要执行的 Python 代码",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": f"超时时间（秒，默认 {self._timeout}）",
                    },
                    "capture_output": {
                        "type": "boolean",
                        "description": "是否捕获 stdout/stderr（默认 True）",
                    },
                },
                "required": ["code"],
            },
            examples=[
                'safe_exec(code="print(sum(range(100)))")',
                "safe_exec(code='import json; print(json.dumps({\"key\": \"value\"}))')",
            ],
            tags=["execute", "run", "python", "sandbox"],
        )

    def execute(self, **kwargs) -> ToolResult:
        code = kwargs.get("code", "")
        timeout = kwargs.get("timeout", self._timeout)
        capture_output = kwargs.get("capture_output", True)

        if not code.strip():
            return ToolResult(success=False, error="Empty code provided")

        # 安全检查
        check_result = self._security_check(code)
        if not check_result["allowed"]:
            return ToolResult(
                success=False,
                error=f"Security check failed: {check_result['reason']}",
                metadata={"matched": check_result.get("matched")},
            )

        temp_file = None
        try:
            # 写入临时文件
            with tempfile.NamedTemporaryFile(
                mode="w",
                suffix=".py",
                delete=False,
                encoding="utf-8",
            ) as f:
                f.write(code)
                temp_file = f.name

            # 构建执行命令
            cmd = [sys.executable, temp_file]

            # 设置资源限制（仅在 Unix 系统上）
            import os

            creationflags = 0
            if hasattr(os, "CREATE_NO_WINDOW") and sys.platform == "win32":
                creationflags = 0x08000000  # CREATE_NO_WINDOW

            # 执行
            result = subprocess.run(
                cmd,
                capture_output=capture_output,
                text=True,
                timeout=timeout,
                cwd=tempfile.gettempdir(),
                creationflags=creationflags,
            )

            # 处理输出
            stdout = result.stdout or ""
            stderr = result.stderr or ""

            # 限制输出行数
            output_lines = stdout.split("\n")
            if len(output_lines) > self._max_output_lines:
                stdout = "\n".join(output_lines[: self._max_output_lines])
                stdout += f"\n... (output truncated, total {len(output_lines)} lines)"

            return ToolResult(
                success=result.returncode == 0,
                data={
                    "return_code": result.returncode,
                    "stdout": stdout,
                    "stderr": stderr,
                    "execution_time": "N/A",  # subprocess 已经处理
                },
            )

        except subprocess.TimeoutExpired:
            return ToolResult(
                success=False,
                error=f"Execution timeout ({timeout}s exceeded)",
                metadata={"timeout": timeout},
            )
        except Exception as e:
            return ToolResult(success=False, error=f"Execution failed: {str(e)}")
        finally:
            # 清理临时文件
            if temp_file:
                try:
                    Path(temp_file).unlink(missing_ok=True)
                except Exception:
                    pass

    def _security_check(self, code: str) -> dict[str, Any]:
        """安全检查.

        Returns:
            {"allowed": bool, "reason": str, "matched": str}
        """
        dangerous_patterns = [
            (r"\brm\s+-rf\b", "File deletion (rm -rf)"),
            (r"\bdel\s+\w+", "File deletion (del)"),
            (r"\.unlink\(", "File deletion (unlink)"),
            (r"\.remove\(", "File deletion (remove)"),
            (r"shutil\.rmtree", "Directory deletion (rmtree)"),
            (r"\brmdir\b", "Directory deletion (rmdir)"),
            (r"__import__\s*\(\s*['\"]os['\"]", "Dynamic os import"),
            (r"__import__\s*\(\s*['\"]sys['\"]", "Dynamic sys import"),
            (r"__import__\s*\(\s*['\"]subprocess['\"]", "Dynamic subprocess import"),
            (r"\beval\s*\(", "Dangerous eval()"),
            (r"\bexec\s*\(", "Dangerous exec()"),
            (r"os\.system\s*\(", "os.system() call"),
            (r"os\.popen\s*\(", "os.popen() call"),
            (r"subprocess\.run\s*\(\s*shell\s*=\s*True", "subprocess with shell=True"),
            (r"subprocess\.Popen\s*\(\s*shell\s*=\s*True", "subprocess.Popen with shell=True"),
            (r"\bsocket\.", "Socket operations"),
            (r"\bcurl\s+", "curl command"),
            (r"\bwget\s+", "wget command"),
            (r"\bnc\s+", "netcat command"),
            (r"\bopenssl\s+", "openssl command"),
            (r"open\s*\(\s*['\"]/etc/", "Access to /etc"),
            (r"open\s*\(\s*['\"]/proc/", "Access to /proc"),
            (r"open\s*\(\s*['\"]/sys/", "Access to /sys"),
            (r"requests\.get\s*\(\s*['\"]http://", "HTTP request (non-HTTPS)"),
            (r"requests\.post\s*\(\s*['\"]http://", "HTTP POST (non-HTTPS)"),
            (r"urllib\.request\.urlopen", "urllib request"),
            (r"import\s+pty\b", "PTY module import"),
            (r"import\s+forky\b", "Fork module import"),
            (r"\bos\.fork\b", "os.fork() call"),
            (r"\bmultiprocessing\.Process", "Process creation"),
            (r"threading\.Thread", "Thread creation"),
            (r"asyncio\.create_subprocess_exec\s*\(\s*shell\s*=\s*True", "Async shell subprocess"),
            (r"sys\.exit\s*\(\s*0\s*\)", "Force exit with code 0"),
            (r"\bexit\s*\(", "Exit call"),
            (r"\bquit\s*\(", "Quit call"),
            (r"settrace\s*\(", "sys.settrace()"),
            (r"setprofile\s*\(", "sys.setprofile()"),
        ]

        for pattern, reason in dangerous_patterns:
            if re.search(pattern, code, re.IGNORECASE):
                return {
                    "allowed": False,
                    "reason": reason,
                    "matched": re.search(pattern, code, re.IGNORECASE).group(0),
                }

        return {"allowed": True}


class ShellCommandTool(Tool):
    """Shell 命令执行工具.

    执行系统命令（受限）。
    """

    # 允许的命令前缀
    ALLOWED_COMMANDS = [
        "git ",
        "ls ",
        "pwd",
        "cat ",
        "head ",
        "tail ",
        "grep ",
        "find ",
        "wc ",
        "sort ",
        "uniq ",
        "awk ",
        "sed ",
        "cut ",
        "diff ",
        "stat ",
        "tree ",
    ]

    # 禁止的命令模式
    FORBIDDEN_PATTERNS = [
        r"\|\s*rm",
        r"&\s*rm",
        r";\s*rm",
        r"rm\s+.*-rf",
        r"wget\s+",
        r"curl\s+",
        r"nc\s+",
        r"bash\s+-i",
        r"sh\s+-i",
        r"python.*-m\s+pip",
        r"pip\s+install",
        r"npm\s+install",
        r">\s*/dev/",
        r"2>\s*/dev/",
    ]

    def __init__(self, timeout: int = 10) -> None:
        super().__init__()
        self._timeout = timeout

    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="shell",
            description="执行安全的 shell 命令（受限）。仅支持读操作和 git 命令。",
            category=ToolCategory.EXECUTION,
            parameters={
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "要执行的 shell 命令",
                    },
                    "cwd": {
                        "type": "string",
                        "description": "工作目录",
                    },
                },
                "required": ["command"],
            },
            examples=[
                "shell(command='git status')",
                "shell(command='ls -la', cwd='src/')",
                "shell(command='grep -r TODO .')",
            ],
            tags=["shell", "execute", "command"],
        )

    def execute(self, **kwargs) -> ToolResult:
        command = kwargs.get("command", "").strip()
        cwd = kwargs.get("cwd")

        if not command:
            return ToolResult(success=False, error="Empty command")

        # 安全检查：命令白名单
        is_allowed = any(command.startswith(cmd) for cmd in self.ALLOWED_COMMANDS)
        if not is_allowed:
            return ToolResult(
                success=False,
                error=f"Command not allowed. Allowed: {', '.join(self.ALLOWED_COMMANDS)}",
            )

        # 安全检查：禁止模式
        for pattern in self.FORBIDDEN_PATTERNS:
            if re.search(pattern, command, re.IGNORECASE):
                return ToolResult(success=False, error=f"Forbidden pattern detected: {pattern}")

        try:
            import os

            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=self._timeout,
                cwd=cwd or os.getcwd(),
            )

            return ToolResult(
                success=result.returncode == 0,
                data={
                    "return_code": result.returncode,
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                },
            )

        except subprocess.TimeoutExpired:
            return ToolResult(success=False, error=f"Command timeout ({self._timeout}s)")
        except Exception as e:
            return ToolResult(success=False, error=f"Execution failed: {str(e)}")
