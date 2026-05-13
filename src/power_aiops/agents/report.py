from __future__ import annotations

import asyncio
from datetime import datetime
from typing import TYPE_CHECKING, AsyncGenerator

from power_aiops.agents.base import AgentResult, AgentStreamChunk, BaseAgent
from power_aiops.llm.client import OpenAICompatibleClient
from power_aiops.memory.shared_board import SharedBoard
from power_aiops.models.incident import IncidentContext
from power_aiops.prompts import SYSTEM_PROMPT_REPORT_AGENT
from power_aiops.visualization import GPTVisRenderer

if TYPE_CHECKING:
    from power_aiops.agents.tools import ToolRegistry

# SharedBoard keys
BOARD_KEY_OPS = "ops_output"
BOARD_KEY_SRE = "sre_output"
BOARD_KEY_CODE = "code_output"
BOARD_KEY_REPORT = "report_output"
BOARD_KEY_VISUALIZATION = "visualization_data"
BOARD_KEY_EXPORT_PATH = "export_path"


class ReportAgent(BaseAgent):
    """报告与复盘 Agent；读取所有 Agent 输出，生成完整报告。

    支持导出为 Word、PDF 等格式。
    """

    def __init__(
        self,
        board: SharedBoard,
        *,
        use_llm: bool = True,
        llm: OpenAICompatibleClient | None = None,
        tool_registry: "ToolRegistry | None" = None,
        default_export_dir: str = "reports",
    ) -> None:
        self._use_llm = use_llm
        self._llm = llm if llm is not None else OpenAICompatibleClient()
        self._board = board
        self._tool_registry = tool_registry
        self._default_export_dir = default_export_dir

    @property
    def agent_id(self) -> str:
        return "Report-Agent-01"

    def run(self, ctx: IncidentContext) -> AgentResult:
        user = self._user_prompt(ctx)

        if self._use_llm:
            text = self._llm.chat(system=SYSTEM_PROMPT_REPORT_AGENT, user=user)
            meta = {"llm": "openai-compatible" if self._llm.is_configured() else "stub"}
        else:
            text = self._placeholder(ctx)
            meta = {"llm": "stub"}

        # Write to board for final report
        self._board.set(BOARD_KEY_REPORT, text)

        # 生成可视化数据
        self._generate_visualization(ctx, text)

        return AgentResult(agent_id=self.agent_id, content=text, meta=meta)

    def _generate_visualization(self, ctx: IncidentContext, report_text: str) -> dict:
        """生成 GPT-Vis 可视化数据，存入 SharedBoard."""
        renderer = GPTVisRenderer()

        # 收集所有 Agent 输出
        agent_outputs = {
            "Ops-Agent": self._board.get(BOARD_KEY_OPS, ""),
            "SRE-Agent": self._board.get(BOARD_KEY_SRE, ""),
            "Code-Agent": self._board.get(BOARD_KEY_CODE, ""),
        }

        # 生成时间线图表
        timeline_chart = renderer.render_incident_timeline(ctx.incident_id, agent_outputs)

        # 生成报告统计
        stats_data = {
            "incident_id": ctx.incident_id,
            "summary": ctx.summary,
            "events_count": len(ctx.events),
            "agents": list(agent_outputs.keys()),
            "report_length": len(report_text),  
        }

        vis_data = {
            "timeline_chart": timeline_chart,
            "stats": stats_data,
            "render_config": {
                "theme": "dark",
                "colors": renderer.DEFAULT_COLORS,
            },
        }

        self._board.set(BOARD_KEY_VISUALIZATION, vis_data)
        return vis_data

    def export_report(
        self,
        format: str = "markdown",
        output_path: str | None = None,
    ) -> dict:
        """导出报告为指定格式.

        Args:
            format: 导出格式 (markdown, docx, pdf, html)
            output_path: 输出文件路径 (可选，默认使用报告目录)

        Returns:
            导出结果字典
        """
        report_content = self._board.get(BOARD_KEY_REPORT, "")
        if not report_content:
            return {"success": False, "error": "No report content found"}

        # 确定输出路径
        from pathlib import Path

        if not output_path:
            export_dir = Path(self._default_export_dir)
            export_dir.mkdir(parents=True, exist_ok=True)
            filename = f"aiops_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.{format}"
            output_path = str(export_dir / filename)
        else:
            output_path = str(Path(output_path))

        self._board.set(BOARD_KEY_EXPORT_PATH, output_path)

        # 获取导出工具
        if not self._tool_registry:
            return {"success": False, "error": "Tool registry not configured"}

        if format == "markdown":
            # 直接写入
            try:
                Path(output_path).parent.mkdir(parents=True, exist_ok=True)
                with open(output_path, "w", encoding="utf-8") as f:
                    f.write(report_content)
                return {
                    "success": True,
                    "path": output_path,
                    "format": "markdown",
                }
            except Exception as e:
                return {"success": False, "error": str(e)}

        elif format == "docx":
            tool = self._tool_registry.get("export_docx")
            if not tool:
                return {"success": False, "error": "export_docx tool not available"}
            result = tool.execute(content=report_content, output_path=output_path)
            return {
                "success": result.success,
                "path": result.data.get("path") if result.data else None,
                "error": result.error,
            }

        elif format == "pdf":
            tool = self._tool_registry.get("export_pdf")
            if not tool:
                return {"success": False, "error": "export_pdf tool not available"}
            result = tool.execute(content=report_content, output_path=output_path)
            return {
                "success": result.success,
                "path": result.data.get("path") if result.data else None,
                "error": result.error,
            }

        elif format == "html":
            tool = self._tool_registry.get("markdown_to_html")
            if not tool:
                return {"success": False, "error": "markdown_to_html tool not available"}
            result = tool.execute(content=report_content)
            if result.success:
                html_path = str(Path(output_path).with_suffix(".html"))
                with open(html_path, "w", encoding="utf-8") as f:
                    f.write(result.data.get("html", ""))
                return {
                    "success": True,
                    "path": html_path,
                    "format": "html",
                }
            return {"success": False, "error": result.error}

        else:
            return {"success": False, "error": f"Unsupported format: {format}"}

    def _user_prompt(self, ctx: IncidentContext) -> str:
        lines = [
            "## 故障信息",
            f"- incident_id: {ctx.incident_id}",
            f"- trace_id: {ctx.trace_id}",
            f"- summary: {ctx.summary or '待确认'}",
            f"- 关联告警数: {len(ctx.events)}",
        ]

        # Append Ops-Agent analysis (truncate to prevent timeout)
        ops_output = self._board.get(BOARD_KEY_OPS)
        if ops_output:
            lines.append("\n## Ops-Agent 初步分析")
            lines.append(f"{ops_output[:1500]}")

        # Append SRE-Agent plan (truncate)
        sre_output = self._board.get(BOARD_KEY_SRE)
        if sre_output:
            lines.append("\n## SRE-Agent 处置方案")
            lines.append(f"{sre_output[:1500]}")

        # Append Code-Agent script (truncate)
        code_output = self._board.get(BOARD_KEY_CODE)
        if code_output:
            lines.append("\n## Code-Agent 诊断脚本")
            lines.append(f"{code_output[:1000]}")

        return "\n".join(lines)

    def _placeholder(self, ctx: IncidentContext) -> str:
        ops = self._board.get(BOARD_KEY_OPS, "")
        sre = self._board.get(BOARD_KEY_SRE, "")
        code = self._board.get(BOARD_KEY_CODE, "")
        lines = [
            f"[Report-Agent] 《故障分析报告》（incident={ctx.incident_id}）。",
            "1. 时间线  2. 影响面  3. 根因  4. 处置  5. 改进项",
            f"Ops摘要: {ops[:200]}...",
            f"SRE方案: {sre[:200]}...",
            f"Code脚本: {code[:200]}...",
        ]
        return "\n".join(lines)

    async def stream_run(self, ctx: IncidentContext) -> AsyncGenerator[AgentStreamChunk, None]:
        """
        流式执行 Report-Agent，实时 yield 每个 token 片段。
        """
        user = self._user_prompt(ctx)
        self._board.set(BOARD_KEY_REPORT, "")  # 初始化

        if self._use_llm and self._llm.is_configured():
            parts = []
            async for delta in self._llm.chat_stream(system=SYSTEM_PROMPT_REPORT_AGENT, user=user):
                parts.append(delta)
                self._board.set(BOARD_KEY_REPORT, "".join(parts))
                yield AgentStreamChunk(agent_id=self.agent_id, delta=delta, is_done=False)

            yield AgentStreamChunk(agent_id=self.agent_id, delta="", is_done=True)
        else:
            text = self._placeholder(ctx)
            for char in text:
                await asyncio.sleep(0.015)
                yield AgentStreamChunk(agent_id=self.agent_id, delta=char, is_done=False)
            self._board.set(BOARD_KEY_REPORT, text)
            yield AgentStreamChunk(agent_id=self.agent_id, delta="", is_done=True)

        # 生成可视化数据
        final_text = self._board.get(BOARD_KEY_REPORT, "")
        self._generate_visualization(ctx, final_text)
