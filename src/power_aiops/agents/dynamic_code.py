"""
动态代码分析代理 (Dynamic Code Agent)。

增强版 CodeAgent，支持：
- 动态生成 Python 代码进行数据分析
- 直接查询 Neo4j 图数据库（Trace/故障案例）
- 读取 OpenRCA 数据集进行分析
- 代码执行沙箱（安全执行用户生成代码）

参考 OpenDerisk RCA-agent 模式：使用 Python 代码做数据检索和分析，
避免直接向 LLM 发送大量上下文数据。

Usage:
    from power_aiops.agents.dynamic_code import DynamicCodeAgent
    from power_aiops.memory.shared_board import SharedBoard

    board = SharedBoard()
    agent = DynamicCodeAgent(board=board)
    result = agent.run(ctx)
    print(result.content)  # 包含代码 + 执行结果
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import AsyncGenerator

from power_aiops.agents.base import AgentResult, AgentStreamChunk, BaseAgent
from power_aiops.llm.client import OpenAICompatibleClient
from power_aiops.memory.graph_rag import GraphRAG
from power_aiops.memory.shared_board import (
    BOARD_KEY_CODE,
    BOARD_KEY_CODE_RESULT,
    SharedBoard,
)
from power_aiops.models.incident import IncidentContext
from power_aiops.security.fences import fence_check_text

logger = logging.getLogger(__name__)


@dataclass
class CodeExecutionResult:
    """代码执行结果."""

    success: bool
    output: str = ""
    error: str = ""
    execution_time_ms: float = 0
    return_code: int = 0


@dataclass
class DataQueryContext:
    """数据分析上下文，供代码生成使用."""

    incident_id: str
    trace_id: str | None = None
    summary: str = ""
    services: list[str] = field(default_factory=list)
    error_services: list[str] = field(default_factory=list)
    time_range_hours: int = 1
    neo4j_available: bool = True
    openrca_available: bool = False
    similar_cases_count: int = 0


class DynamicCodeAgent(BaseAgent):
    """动态代码分析 Agent.

    核心能力：
    1. 理解数据分析需求，生成 Python 代码
    2. 代码中嵌入 Neo4j 查询（查询 Trace/故障）
    3. 支持 OpenRCA 数据集分析
    4. 安全执行生成代码并返回结果
    """

    def __init__(
        self,
        board: SharedBoard,
        *,
        use_llm: bool = True,
        llm: OpenAICompatibleClient | None = None,
        graph_rag: GraphRAG | None = None,
        enable_execution: bool = False,  # 默认不执行，仅生成代码
        execution_timeout: int = 30,  # 代码执行超时（秒）
        max_output_lines: int = 100,  # 最大输出行数
    ) -> None:
        self._use_llm = use_llm
        self._llm = llm if llm is not None else OpenAICompatibleClient()
        self._board = board
        self._graph_rag = graph_rag
        self._enable_execution = enable_execution
        self._execution_timeout = execution_timeout
        self._max_output_lines = max_output_lines

    @property
    def agent_id(self) -> str:
        return "DynamicCode-Agent-01"

    @property
    def graph_rag(self) -> GraphRAG | None:
        return self._graph_rag

    def run(self, ctx: IncidentContext) -> AgentResult:
        """执行动态代码分析."""
        # 构建数据分析上下文
        query_ctx = self._build_query_context(ctx)

        # 生成分析代码
        code = self._generate_analysis_code(ctx, query_ctx)

        # 安全检查
        check = fence_check_text(code)
        if not check.allowed:
            self._board.set(BOARD_KEY_CODE, "[围栏拦截] 代码未通过安全检查")
            return AgentResult(
                agent_id=self.agent_id,
                content="[围栏拦截] 代码未通过安全检查，禁止执行。",
                blocked=True,
                fence_matched=check.matched,
                meta={"code_length": len(code), "llm": "stub"},
            )

        # 执行代码（可选）
        execution_result = None
        if self._enable_execution:
            execution_result = self._execute_code(code, query_ctx)

        # 构建输出
        lines = [
            f"[{self.agent_id}] 动态代码分析完成",
            "",
            "### 分析思路",
            "1. 查询图数据库获取错误链路和慢链路",
            "2. 分析故障传播路径，定位根因服务",
            "3. 对比历史相似案例，辅助判断",
            "",
            "---",
            "",
            "### 生成的 Python 代码",
            "```python",
            code,
            "```",
        ]

        if execution_result:
            lines.append(f"\n### 执行结果 (耗时: {execution_result.execution_time_ms:.0f}ms)")
            if execution_result.success:
                lines.append("```")
                output_lines = execution_result.output.split("\n")[:self._max_output_lines]
                lines.append("\n".join(output_lines))
                if len(execution_result.output.split("\n")) > self._max_output_lines:
                    lines.append(f"... (共 {len(execution_result.output.split(chr(10)))} 行，已截断)")
                lines.append("```")
            else:
                lines.append(f"**执行失败**: {execution_result.error}")

        # 存储结果
        self._board.set(BOARD_KEY_CODE, code)
        if execution_result:
            self._board.set(BOARD_KEY_CODE_RESULT, json.dumps({
                "success": execution_result.success,
                "output": execution_result.output[:5000],
                "error": execution_result.error,
                "execution_time_ms": execution_result.execution_time_ms,
            }))

        content = "\n".join(lines)
        meta = {
            "llm": "openai-compatible" if self._llm.is_configured() else "stub",
            "code_length": len(code),
            "execution_enabled": self._enable_execution,
        }
        if execution_result:
            meta["execution_success"] = execution_result.success
            meta["execution_time_ms"] = execution_result.execution_time_ms

        return AgentResult(agent_id=self.agent_id, content=content, meta=meta)

    def _build_query_context(self, ctx: IncidentContext) -> DataQueryContext:
        """从 IncidentContext 构建数据分析上下文."""
        # 提取涉及的服务
        services = []
        error_services = []

        for event in ctx.events:
            if hasattr(event, "device_id") and event.device_id:
                services.append(event.device_id)
            if hasattr(event, "metric_type") and "error" in event.metric_type.lower():
                error_services.append(event.device_id or "")

        # 检查 Graph RAG 可用性
        neo4j_available = False
        similar_cases_count = 0
        if self._graph_rag is None:
            try:
                self._graph_rag = GraphRAG()
                neo4j_available = self._graph_rag.health_check()
                if neo4j_available:
                    stats = self._graph_rag.get_stats()
                    similar_cases_count = stats.get("total_cases", 0)
            except Exception as e:
                logger.warning(f"GraphRAG not available: {e}")
                neo4j_available = False

        return DataQueryContext(
            incident_id=ctx.incident_id,
            trace_id=ctx.trace_id,
            summary=ctx.summary or "",
            services=list(set(services)),
            error_services=list(set(error_services)),
            neo4j_available=neo4j_available,
            similar_cases_count=similar_cases_count,
        )

    def _generate_and_prepare_code(self, ctx: IncidentContext) -> tuple[str, DataQueryContext]:
        """生成代码并准备上下文（在独立线程中执行以避免阻塞）。

        Returns:
            (generated_code, query_context) 元组
        """
        query_ctx = self._build_query_context(ctx)
        code = self._generate_analysis_code(ctx, query_ctx)
        return code, query_ctx

    def _generate_analysis_code(self, ctx: IncidentContext, query_ctx: DataQueryContext) -> str:
        """生成数据分析 Python 代码."""
        if self._use_llm and self._llm.is_configured():
            try:
                code = self._llm_chat_code_generation(ctx, query_ctx)
                # 清理 LLM 返回的代码（去除可能的解释性文字）
                return self._clean_generated_code(code)
            except Exception as e:
                logger.warning(f"LLM code generation failed, using fallback: {e}")
                return self._generate_fallback_code(ctx, query_ctx)
        return self._generate_fallback_code(ctx, query_ctx)

    def _clean_generated_code(self, raw_output: str) -> str:
        """清理 LLM 返回的代码，去除解释性文字和 Markdown 标记。"""
        lines = raw_output.strip().split('\n')
        code_lines = []
        in_code_block = False
        markdown_removed = False

        for line in lines:
            stripped = line.strip()
            # 检测代码块开始
            if stripped.startswith('```python') or stripped.startswith('```py'):
                in_code_block = True
                markdown_removed = True
                continue
            if stripped.startswith('```') and not in_code_block:
                in_code_block = True
                continue
            # 检测代码块结束
            if stripped == '```' and in_code_block:
                in_code_block = False
                continue
            # 在代码块内或没有 markdown 标记时，收集代码行
            if in_code_block:
                code_lines.append(line)
            elif not markdown_removed:
                # 没有 markdown 标记，检查是否看起来像代码
                # 如果行以字母数字开头且包含 Python 关键字或语法，可能是纯代码
                if stripped and not stripped.startswith('#') and not stripped.startswith('"""'):
                    # 检查是否像代码行
                    if any(kw in stripped for kw in ['import ', 'def ', 'class ', 'return ', 'print(', 'if ', 'for ', 'while ', 'try:', 'except']):
                        code_lines.append(line)
                        markdown_removed = True
                    elif stripped.startswith(('"""', "'''")):
                        code_lines.append(line)
                        markdown_removed = True

        if code_lines:
            return '\n'.join(code_lines).strip()

        # 如果清理后没有代码，返回原始内容
        return raw_output.strip()

    def _llm_chat_code_generation(self, ctx: IncidentContext, query_ctx: DataQueryContext) -> str:
        """使用 LLM 生成数据分析代码."""
        system_prompt = """You are a Data Analysis Expert specializing in root cause analysis.

Your task is to generate Python code that analyzes telemetry data for fault diagnosis.

IMPORTANT RULES:
1. The generated code MUST be safe and contain NO dangerous operations (rm -rf, format, etc.)
2. Use neo4j python driver to query graph database for traces and fault cases
3. Use pandas for data analysis when needed
4. Always include proper error handling
5. Print results in a structured format for easy reading

Available libraries in execution environment:
- neo4j (graph database queries)
- pandas (data analysis)
- json, datetime, collections
- networkx (graph analysis)

OUTPUT FORMAT: First provide a brief explanation (2-3 lines), then output the code in a markdown code block.

Example format:
```
分析思路：本代码将查询错误链路和慢链路来定位根因。

```python
import neo4j
# ... code ...
```
```"""

        user_prompt = f"""Generate Python code for root cause analysis with the following context:

## Incident Information
- Incident ID: {query_ctx.incident_id}
- Summary: {query_ctx.summary}
- Trace ID: {query_ctx.trace_id or 'N/A'}
- Error Services: {', '.join(query_ctx.error_services) if query_ctx.error_services else 'None detected'}
- Total Alerts: {len(ctx.events)}

## Query Capabilities
- Neo4j Available: {query_ctx.neo4j_available}
- Similar Cases in KB: {query_ctx.similar_cases_count}

## Sample Events
{self._format_events(ctx.events[:10])}

## Code Requirements
1. Query Neo4j for:
   - Similar fault cases (using Graph RAG)
   - Error traces involving affected services
   - Slow traces (>5s duration)
   - Fault propagation paths

2. Generate analysis results that help identify:
   - Root cause service/component
   - Call chain where error originated
   - Similar historical incidents

First briefly explain your analysis approach, then provide the Python code:"""

        return self._llm.chat(system=system_prompt, user=user_prompt)

    def _generate_fallback_code(self, ctx: IncidentContext, query_ctx: DataQueryContext) -> str:
        """生成后备代码（当 LLM 不可用时）."""
        services_json = json.dumps(query_ctx.services)
        error_services_json = json.dumps(query_ctx.error_services)

        code = f'''"""动态数据分析代码 - {query_ctx.incident_id}

此代码自动生成用于根因分析。
"""

import json
from datetime import datetime, timedelta

# === 配置 ===
INCIDENT_ID = "{query_ctx.incident_id}"
TRACE_ID = "{query_ctx.trace_id or ""}"
SERVICES = {services_json}
ERROR_SERVICES = {error_services_json}
SUMMARY = """{query_ctx.summary[:500] if query_ctx.summary else ""}"""

print("=" * 60)
print(f"故障分析报告 - {{INCIDENT_ID}}")
print("=" * 60)

# === 1. 故障摘要 ===
print("\\n## 1. 故障摘要")
print(f"描述: {{SUMMARY}}")
print(f"涉及服务: {{', '.join(SERVICES) if SERVICES else '未知'}}")
print(f"错误服务: {{', '.join(ERROR_SERVICES) if ERROR_SERVICES else '无'}}")

# === 2. 事件统计 ===
print("\\n## 2. 关联事件统计")
event_types = {{}}
for event in {self._format_events_for_code(ctx.events)}:
    metric_type = event.get("metric_type", "unknown")
    event_types[metric_type] = event_types.get(metric_type, 0) + 1

for metric_type, count in sorted(event_types.items(), key=lambda x: -x[1])[:10]:
    print(f"  {{metric_type}}: {{count}} 次")

# === 3. Neo4j 查询 (示例) ===
print("\\n## 3. 图数据库查询 (如可用)")

NEO4J_AVAILABLE = {str(query_ctx.neo4j_available).lower()}
if NEO4J_AVAILABLE:
    try:
        from neo4j import GraphDatabase
        from power_aiops.config import get_settings

        settings = get_settings()
        uri = settings.neo4j_uri
        user = settings.neo4j_user
        password = settings.neo4j_password

        driver = GraphDatabase.driver(uri, auth=(user, password))

        with driver.session(database="neo4j") as session:
            # 查询错误链路
            if ERROR_SERVICES:
                result = session.run("""
                    MATCH (t:Trace)-[:CONTAINS]->(s:Span)
                    WHERE s.service IN $services AND s.status IN ['ERROR', 'TIMEOUT']
                    RETURN t.trace_id AS trace_id,
                           s.service AS error_service,
                           s.operation AS operation,
                           s.start_time AS time
                    ORDER BY s.start_time DESC
                    LIMIT 10
                """, services=ERROR_SERVICES)

                print("\\n### 错误链路:")
                for record in result:
                    print(f"  - Trace: {{record['trace_id']}}")
                    print(f"    服务: {{record['error_service']}}")
                    print(f"    操作: {{record['operation']}}")

            # 查询慢链路
            result = session.run("""
                MATCH (t:Trace)
                WHERE t.duration_ms > 5000
                RETURN t.trace_id AS trace_id,
                       t.duration_ms AS duration,
                       t.total_spans AS spans
                ORDER BY t.duration_ms DESC
                LIMIT 5
            """)

            print("\\n### 慢链路 (>5s):")
            for record in result:
                print(f"  - {{record['trace_id']}}: {{record['duration']/1000:.1f}}s, {{record['spans']}} spans")

        driver.close()
        print("\\n[Neo4j] 查询完成")

    except ImportError:
        print("[Neo4j] 驱动未安装: pip install neo4j")
    except Exception as e:
        print(f"[Neo4j] 查询失败: {{e}}")
else:
    print("[Neo4j] 不可用，跳过图数据库查询")

# === 4. 根因分析建议 ===
print("\\n## 4. 根因分析建议")

if ERROR_SERVICES:
    print("\\n**可疑服务 (可能为根因)**:")
    for svc in ERROR_SERVICES[:3]:
        print(f"  - {{svc}}")

if SERVICES:
    print("\\n**受影响服务**:")
    for svc in SERVICES[:5]:
        print(f"  - {{svc}}")

print("\\n**排查方向**:")
print("  1. 检查错误服务的上游依赖")
print("  2. 分析链路追踪中的异常 Span")
print("  3. 对比历史相似故障案例")
print("  4. 检查最近变更记录")

print("\\n" + "=" * 60)
print("分析完成")
'''
        return code

    def _format_events(self, events) -> str:
        """格式化事件列表用于 prompt."""
        if not events:
            return "无关联事件"

        lines = []
        for ev in events[:10]:
            lines.append(f"- {ev.metric_type}: {str(ev.value)[:100]}")
        return "\n".join(lines)

    def _format_events_for_code(self, events) -> str:
        """格式化事件列表用于 Python 代码."""
        event_dicts = []
        for ev in events:
            event_dicts.append({
                "timestamp": str(ev.timestamp),
                "metric_type": ev.metric_type,
                "value": str(ev.value)[:200] if ev.value else "",
                "device_id": ev.device_id or "",
                "source": ev.source.value if hasattr(ev.source, "value") else str(ev.source),
            })
        return json.dumps(event_dicts, ensure_ascii=False)

    def _execute_code(self, code: str, query_ctx: DataQueryContext) -> CodeExecutionResult:
        """安全执行生成的 Python 代码.

        执行策略：
        1. 写入临时文件
        2. 使用 subprocess 执行（隔离环境）
        3. 超时控制
        4. 输出捕获
        """
        import time

        start_time = time.time()

        try:
            # 清理代码（移除危险操作）
            safe_code = self._sanitize_code(code)

            # 写入临时文件
            with tempfile.NamedTemporaryFile(
                mode="w",
                suffix=".py",
                delete=False,
                encoding="utf-8",
            ) as f:
                f.write(safe_code)
                temp_file = f.name

            try:
                # 执行代码
                result = subprocess.run(
                    [sys.executable, temp_file],
                    capture_output=True,
                    text=True,
                    timeout=self._execution_timeout,
                    cwd=tempfile.gettempdir(),
                )

                execution_time = (time.time() - start_time) * 1000

                return CodeExecutionResult(
                    success=result.returncode == 0,
                    output=result.stdout,
                    error=result.stderr,
                    execution_time_ms=execution_time,
                    return_code=result.returncode,
                )

            finally:
                # 清理临时文件
                try:
                    Path(temp_file).unlink(missing_ok=True)
                except Exception:
                    pass

        except subprocess.TimeoutExpired:
            execution_time = (time.time() - start_time) * 1000
            return CodeExecutionResult(
                success=False,
                output="",
                error=f"代码执行超时 ({self._execution_timeout}s)",
                execution_time_ms=execution_time,
                return_code=-1,
            )
        except Exception as e:
            execution_time = (time.time() - start_time) * 1000
            return CodeExecutionResult(
                success=False,
                output="",
                error=str(e),
                execution_time_ms=execution_time,
                return_code=-1,
            )

    def _sanitize_code(self, code: str) -> str:
        """清理代码，移除危险操作.

        允许的操作：
        - 数据查询 (SELECT, MATCH, READ)
        - 打印输出 (print)
        - 数据处理 (pandas, json, collections)

        禁止的操作：
        - 文件删除 (rm, del, unlink)
        - 系统修改 (chmod, chown)
        - 网络攻击 (端口扫描等)
        """
        dangerous_patterns = [
            r"\brm\s+-rf\b",
            r"\brmdir\b",
            r"\.unlink\(",
            r"\.remove\(",
            r"\.rmtree\(",
            r"shutil\.rmtree",
            r"__import__\s*\(",
            r"eval\s*\(",
            r"exec\s*\(",
            r"os\.system",
            r"os\.popen",
            r"subprocess\.(run|Popen|call)\s*\(",
            r"\bsocket\.",
            r"\bcurl.*--max-time",
            r"wget\s+",
            r"\bnc\s+",
            r"net\.cat",
        ]

        for pattern in dangerous_patterns:
            if re.search(pattern, code, re.IGNORECASE):
                logger.warning(f"Dangerous pattern detected: {pattern}")
                raise ValueError(f"代码包含危险操作: {pattern}")

        return code

    async def stream_run(self, ctx: IncidentContext) -> AsyncGenerator[AgentStreamChunk, None]:
        """流式执行 DynamicCodeAgent."""
        # 先 yield 一个"思考中"状态，让前端知道 Code-Agent 正在工作
        yield AgentStreamChunk(
            agent_id=self.agent_id,
            delta=f"[{self.agent_id}] 正在生成分析代码...\n",
            is_done=False
        )

        # 在后台生成代码（使用 asyncio.to_thread 避免阻塞）
        code, query_ctx = await asyncio.to_thread(self._generate_and_prepare_code, ctx)

        # 切换到流式输出模式前稍作停顿，让前端有时间处理
        await asyncio.sleep(0.05)

        # 清空"思考中"行，开始输出实际内容
        yield AgentStreamChunk(
            agent_id=self.agent_id,
            delta=f"[{self.agent_id}] 代码生成完成，开始分析\n\n",
            is_done=False
        )

        # 安全检查
        check = fence_check_text(code)
        if not check.allowed:
            blocked_msg = "[围栏拦截] 代码未通过安全检查"
            self._board.set(BOARD_KEY_CODE, blocked_msg)
            for char in blocked_msg:
                await asyncio.sleep(0.02)
                yield AgentStreamChunk(agent_id=self.agent_id, delta=char, is_done=False, blocked=True)
            yield AgentStreamChunk(agent_id=self.agent_id, delta="", is_done=True, blocked=True)
            return

        # 输出分析思路（流式输出）
        yield AgentStreamChunk(
            agent_id=self.agent_id,
            delta=f"[{self.agent_id}] 动态代码分析\n\n",
            is_done=False
        )

        # 输出分析思路
        analysis_tips = """### 分析思路
1. 查询图数据库获取错误链路和慢链路
2. 分析故障传播路径，定位根因服务
3. 对比历史相似案例，辅助判断

---
### 生成的 Python 代码

```python
"""

        for char in analysis_tips:
            await asyncio.sleep(0.008)
            yield AgentStreamChunk(agent_id=self.agent_id, delta=char, is_done=False)

        # 分段输出代码（每 30 字符为一段，中间有延迟）
        code_segments = [code[i:i+30] for i in range(0, len(code), 30)]
        for segment in code_segments:
            await asyncio.sleep(0.005)
            yield AgentStreamChunk(agent_id=self.agent_id, delta=segment, is_done=False)

        await asyncio.sleep(0.02)
        yield AgentStreamChunk(agent_id=self.agent_id, delta="```\n", is_done=False)

        # 执行代码（如果启用）
        if self._enable_execution:
            await asyncio.sleep(0.05)
            yield AgentStreamChunk(agent_id=self.agent_id, delta="\n### 执行中...\n", is_done=False)

            # 在后台线程执行代码
            result = await asyncio.to_thread(self._execute_code, code, query_ctx)

            if result.success:
                await asyncio.sleep(0.03)
                yield AgentStreamChunk(agent_id=self.agent_id, delta="```\n", is_done=False)
                for line in result.output.split("\n")[:50]:
                    await asyncio.sleep(0.01)
                    yield AgentStreamChunk(agent_id=self.agent_id, delta=line + "\n", is_done=False)
                await asyncio.sleep(0.02)
                yield AgentStreamChunk(agent_id=self.agent_id, delta="```\n", is_done=False)
            else:
                yield AgentStreamChunk(
                    agent_id=self.agent_id,
                    delta=f"**执行失败**: {result.error}\n",
                    is_done=False,
                )

        self._board.set(BOARD_KEY_CODE, code)
        await asyncio.sleep(0.02)
        yield AgentStreamChunk(agent_id=self.agent_id, delta="", is_done=True)


# 向后兼容别名
RCAgent = DynamicCodeAgent
