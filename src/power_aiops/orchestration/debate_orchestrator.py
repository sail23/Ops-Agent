"""辩论编排器：动态轮次路由（阶段二）。

核心变化：不再使用固定轮次顺序，而是：
  1. 每个 Agent 发言后，解析 LLM 输出的 next_turn 指令
  2. 编排器根据指令 + 状态决定下一个发言者
  3. 收敛判断：算法计算 + LLM convergence 字段
  4. 人类介入：争议时暂停确认

动态路由规则：
  - 初始阶段：确保 Ops → SRE → Code 顺序完成第一轮
  - 审视阶段：由 next_turn 指令驱动，可任意跳转
  - 收敛阶段：SRE 给出 convergence 判断
  - 终止条件：convergence=true / dispute / max_turns / terminate 指令
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, AsyncGenerator

from power_aiops.agents.dynamic_code import DynamicCodeAgent
from power_aiops.llm.client import OpenAICompatibleClient
from power_aiops.memory.shared_board import SharedBoard
from power_aiops.models.incident import IncidentContext
from power_aiops.orchestration.debate import (
    BOARD_KEY_CONVERGENCE,
    BOARD_KEY_DEBATE_HISTORY,
    BOARD_KEY_FINAL_REPORT,
    BOARD_KEY_ROOT_CAUSE,
    BOARD_KEY_TURN_OUTPUT,
    DebateMessage,
    DebateMessageType,
    DebateResult,
    DebateRole,
    DebateRound,
    DebateState,
    DebateTurn,
    NextTurnHint,
    next_turn_hint_to_role,
    next_turn_hint_to_round,
    parse_next_turn,
)
from power_aiops.orchestration.debate_board import DebateBoard
from power_aiops.orchestration.debate_prompts import (
    DEBATE_PROMPT_REPORT_JUDGE,
    debate_prompt_for,
)
from power_aiops.prompts.roles import SYSTEM_PROMPT_REPORT_AGENT

logger = logging.getLogger(__name__)


# ─────────────────── 解析辅助 ───────────────────

_SECTION_RE = re.compile(
    r"^##\s*(reasoning|conclusion|confidence|disputed_points|consensus_points|"
    r"script_needs|script_additions|convergence|ready_for_report|verdict|"
    r"final_report|stance|next_turn)\s*$",
    re.MULTILINE | re.IGNORECASE,
)


def _parse_sections(text: str) -> dict[str, str]:
    sections: dict[str, str] = {}
    current_key = ""
    current_value_lines: list[str] = []

    for line in text.splitlines():
        m = _SECTION_RE.match(line)
        if m:
            if current_key:
                sections[current_key.lower()] = "\n".join(current_value_lines).strip()
            current_key = m.group(1).strip()
            current_value_lines = []
        else:
            current_value_lines.append(line)

    if current_key:
        sections[current_key.lower()] = "\n".join(current_value_lines).strip()

    return sections


def _extract_conclusion(text: str) -> str:
    sections = _parse_sections(text)
    for key in ("conclusion", "final_report"):
        if key in sections:
            return sections[key]
    return text[:2000]


def _extract_reasoning(text: str) -> str:
    sections = _parse_sections(text)
    reasoning = sections.get("reasoning", "")
    if reasoning:
        return reasoning
    parts = [v for k, v in sections.items() if k not in ("conclusion", "final_report") and v]
    return "\n".join(parts)[:1500]


def _extract_meta(text: str) -> dict[str, Any]:
    sections = _parse_sections(text)
    meta: dict[str, Any] = {}

    confidence_map = {"高": 1.0, "中": 0.6, "低": 0.3, "high": 1.0, "medium": 0.6, "low": 0.3}
    conf_raw = sections.get("confidence", "")
    meta["confidence"] = confidence_map.get(conf_raw.strip(), 0.5) if conf_raw else 0.5

    meta["convergence"] = "true" if sections.get("convergence", "").strip().lower() == "true" else "false"
    meta["disputed_points"] = [p.strip() for p in sections.get("disputed_points", "").splitlines() if p.strip()]
    meta["consensus_points"] = [p.strip() for p in sections.get("consensus_points", "").splitlines() if p.strip()]
    meta["next_turn"] = parse_next_turn(text).value
    meta["stance"] = sections.get("stance", "").strip().lower()
    meta["ready_for_report"] = sections.get("ready_for_report", "").strip().lower() == "true"

    return meta


# ─────────────────── Agent 包装器 ───────────────────

class DebateAgentWrapper:
    def __init__(
        self,
        role: DebateRole,
        llm: OpenAICompatibleClient | None = None,
        board: SharedBoard | None = None,
    ) -> None:
        self.role = role
        self._llm = llm if llm is not None else OpenAICompatibleClient()
        self._board = board or SharedBoard()
        self._agent_id_map = {
            DebateRole.OPS: "Ops-Agent-01",
            DebateRole.SRE: "SRE-Agent-01",
            DebateRole.CODE: "DynamicCode-Agent-01",  # 使用 DynamicCodeAgent
            DebateRole.REPORT: "Report-Agent-01",
        }
        # CODE 角色使用 DynamicCodeAgent，使用传入的 board（来自 orchestrator 的 _shared_board）
        self._dynamic_code_agent: DynamicCodeAgent | None = None
        if role == DebateRole.CODE:
            self._dynamic_code_agent = DynamicCodeAgent(board=self._board, llm=self._llm)

    @property
    def agent_id(self) -> str:
        return self._agent_id_map.get(self.role, f"{self.role.value}-agent")

    def _build_user_prompt(
        self,
        ctx: IncidentContext,
        board: DebateBoard | None,
        round_obj: DebateRound,
    ) -> str:
        lines: list[str] = [
            f"## 当前故障信息",
            f"- incident_id: {ctx.incident_id}",
            f"- summary: {ctx.summary or '待确认'}",
            f"- 关联告警数: {len(ctx.events)}",
        ]

        if ctx.events:
            lines.append("\n## 关联告警")
            for ev in ctx.events[:8]:
                lines.append(
                    f"- [{ev.source.value}] {ev.metric_type}: {ev.value} "
                    f"(@ {ev.timestamp.strftime('%Y-%m-%d %H:%M:%S')})"
                )

        # 注入辩论上下文（其他 Agent 的发言）
        if board:
            history = board.read_for_agent(self.role.value, show_reasoning=True)
            if history:
                lines.append(f"\n{history}")

        return "\n".join(lines)

    def execute(
        self,
        ctx: IncidentContext,
        board: DebateBoard | None,
        round_obj: DebateRound,
    ) -> tuple[str, str, dict[str, Any], NextTurnHint]:
        """执行一轮辩论发言，返回 (content, reasoning, meta, next_turn)。

        CODE 角色使用 DynamicCodeAgent 生成实际的 Python 代码。
        其他角色使用 LLM 直接生成文本。
        """
        # CODE 角色使用 DynamicCodeAgent
        if self.role == DebateRole.CODE and self._dynamic_code_agent:
            return self._execute_code_role(ctx)

        system_prompt = debate_prompt_for(self.role.value, round_obj.value)
        user_prompt = self._build_user_prompt(ctx, board, round_obj)

        if not self._llm.is_configured():
            text = self._stub_output(round_obj)
        else:
            text = self._llm.chat(system=system_prompt, user=user_prompt)

        conclusion = _extract_conclusion(text)
        reasoning = _extract_reasoning(text)
        meta = _extract_meta(text)
        next_turn = parse_next_turn(text)

        return conclusion, reasoning, meta, next_turn

    def _execute_code_role(self, ctx: IncidentContext) -> tuple[str, str, dict[str, Any], NextTurnHint]:
        """执行 CODE 角色：使用 DynamicCodeAgent 生成分析代码。"""
        if self._dynamic_code_agent is None:
            return self._stub_output(DebateRound.CODE_INITIAL), "", {}, NextTurnHint.CONVERGE

        try:
            # 运行 DynamicCodeAgent 获取代码
            result = self._dynamic_code_agent.run(ctx)

            # 格式化输出为辩论格式
            conclusion = result.content[:1500] if result.content else "代码生成完成"
            reasoning = f"使用 DynamicCodeAgent 生成了 {len(result.content)} 字符的分析代码"

            meta = {
                "confidence": "中",
                "convergence": "false",
                "disputed_points": [],
                "consensus_points": [],
                "next_turn": "converge",
                "code_blocked": result.blocked,
                "code_length": len(result.content),
            }

            next_turn = NextTurnHint.CONVERGE
            return conclusion, reasoning, meta, next_turn
        except Exception as e:
            logger.warning(f"DynamicCodeAgent execution failed: {e}")
            return self._stub_output(DebateRound.CODE_INITIAL), "", {}, NextTurnHint.CONVERGE

    def _stub_output(self, round_obj: DebateRound) -> str:
        stubs: dict[str, str] = {
            "ops_initial": (
                "## reasoning\n根据告警信息初步判断为资源瓶颈类故障。\n"
                "## conclusion\n初步假设：数据库连接池耗尽导致请求超时。\n"
                "## confidence\n中\n"
                "## disputed_points\n1. 连接池配置参数是多少？\n2. 是否存在慢查询堆积？\n"
                "## next_turn\nsre"
            ),
            "sre_initial": (
                "## reasoning\n从告警模式看，数据库连接超时是主要表现。\n"
                "## conclusion\n建议主备切换 + 连接池扩容，同时检查慢查询。\n"
                "## confidence\n中\n"
                "## disputed_points\n1. 备库同步延迟是多少？\n"
                "## next_turn\ncode"
            ),
            "code_initial": (
                "## reasoning\n需要验证主备链路和连接池配置后再决定切换策略。\n"
                "## conclusion\n建议准备数据库健康检查脚本，验证后再切换。\n"
                "## confidence\n中\n"
                "## script_needs\n1. 数据库连接池状态查询\n2. 备库同步延迟检测\n"
                "## next_turn\nconverge"
            ),
            "ops_review": (
                "## reasoning\n支持 SRE 的主备切换方案，需补充业务通知。\n"
                "## stance\nsupportive\n"
                "## disputed_points\n变更窗口是否在 SLA 允许范围内？\n"
                "## next_turn\nsre"
            ),
            "sre_review": (
                "## reasoning\n三方意见已基本收敛。\n"
                "## conclusion\n方案确定：主备切换 + 脚本验证。\n"
                "## consensus_points\n根因=连接池瓶颈\n方案=切换+验证\n"
                "## disputed_points\n空\n"
                "## next_turn\nconverge"
            ),
            "code_review": (
                "## reasoning\nSRE 方案可行，但需确保脚本在切换前完成前置检查。\n"
                "## conclusion\n建议增加：1. 备库延迟检测 2. 切换后监控。\n"
                "## disputed_points\n空\n"
                "## next_turn\nconverge"
            ),
            "converge": (
                "## reasoning\n三方已达成共识：根因=连接池瓶颈，方案=切换+验证。\n"
                "## conclusion\n收敛达成：立即执行主备切换。\n"
                "## consensus_points\n根因=连接池瓶颈\n方案=主备切换+健康检查\n"
                "## disputed_points\n空\n"
                "## convergence\ntrue\n"
                "## next_turn\nreport"
            ),
            "report": (
                "## verdict\nconverged\n"
                "## consensus_points\n数据库连接池瓶颈是主要根因，解决方案为主备切换+脚本验证。\n"
                "## final_report\n# 故障报告\n\n## 时间线\n- 故障发生：收到数据库连接超时告警\n- 初步判断：连接池资源耗尽\n- 处置完成：执行主备切换，验证备库健康\n\n## 根因分析\n数据库连接池配置不足以支撑当前业务峰值，导致连接等待超时。\n\n## 处置过程\n1. 确认主库连接池耗尽\n2. 评估备库同步延迟 < 5s\n3. 执行主备切换\n4. 验证备库服务正常\n\n## 改进建议\n1. 增加连接池最大连接数\n2. 添加连接池使用率监控告警\n3. 定期巡检慢查询\n"
                "## next_turn\nterminate"
            ),
        }
        return stubs.get(round_obj.value, "## conclusion\n暂无结论\n## reasoning\n（stub 输出）\n## next_turn\nterminate")

    def fallback_stub(self, round_obj: DebateRound) -> tuple[str, str, dict[str, Any], NextTurnHint]:
        text = self._stub_output(round_obj)
        return _extract_conclusion(text), _extract_reasoning(text), _extract_meta(text), parse_next_turn(text)


# ─────────────────── 辩论编排器 ───────────────────

class DebateOrchestrator:
    def __init__(
        self,
        *,
        max_turns: int = 12,
        max_rounds: int | None = None,  # 新增：最大辩论轮数
        llm: OpenAICompatibleClient | None = None,
    ) -> None:
        self._max_turns = max_turns
        self._max_rounds = max_rounds
        self._llm = llm if llm is not None else OpenAICompatibleClient()
        self._state: DebateState | None = None
        self._board: DebateBoard | None = None
        self._wrapped_agents: dict[DebateRole, DebateAgentWrapper] = {}

    def _init_components(self, ctx: IncidentContext) -> None:
        self._state = DebateState(
            incident_id=ctx.incident_id,
            trace_id=ctx.trace_id,
            max_turns=self._max_turns,
        )
        self._board = DebateBoard()
        # 用于 DynamicCodeAgent 的 SharedBoard
        self._shared_board = SharedBoard()

        for role in [DebateRole.OPS, DebateRole.SRE, DebateRole.CODE, DebateRole.REPORT]:
            self._wrapped_agents[role] = DebateAgentWrapper(
                role=role,
                llm=self._llm,
                board=self._shared_board if role == DebateRole.CODE else None,
            )

    def _get_round_for_role(self, role: DebateRole, state: DebateState) -> DebateRound:
        """根据角色和状态决定当前轮次。"""
        if role == DebateRole.OPS:
            if not state.has_ops_initial:
                return DebateRound.OPS_INITIAL
            return DebateRound.OPS_REVIEW
        if role == DebateRole.SRE:
            if not state.has_sre_initial:
                return DebateRound.SRE_INITIAL
            return DebateRound.SRE_REVIEW
        if role == DebateRole.CODE:
            if not state.has_code_initial:
                return DebateRound.CODE_INITIAL
            return DebateRound.CODE_REVIEW
        return DebateRound.REPORT

    def _get_next_speaker(self, state: DebateState, hint: NextTurnHint) -> DebateRole | None:
        """根据 next_turn 指令 + 辩论状态决定下一个发言者。"""
        hint_role = next_turn_hint_to_role(hint)
        if hint_role:
            return hint_role

        if hint == NextTurnHint.CONVERGE:
            state.converge_attempts += 1
            return DebateRole.SRE  # converge 由 SRE 执行

        # DISPUTE / TERMINATE / 未知：返回 None，触发终止
        return None

    def _should_terminate(self) -> bool:
        if self._state is None:
            return True
        if self._state.terminated:
            return True
        if self._state.current_turn >= self._state.max_turns:
            # 检查是否已有 Report 轮次
            has_report = any(
                t.round == DebateRound.REPORT.value
                for t in self._state.turns
            )
            if not has_report:
                # 强制添加一个 Report 轮次
                logger.warning(f"达到最大轮次 {self._state.max_turns}，强制进入 Report 阶段")
                self._state.terminated = False  # 重置以便执行 Report
                return False  # 不终止，让 Report 阶段执行
            self._state.terminated = True
            self._state.termination_reason = "max_turns"
            return True
        return False

    def _msg_type_for_round(self, round_obj: DebateRound) -> DebateMessageType:
        mapping: dict[str, DebateMessageType] = {
            DebateRound.OPS_INITIAL.value: DebateMessageType.INITIAL,
            DebateRound.SRE_INITIAL.value: DebateMessageType.INITIAL,
            DebateRound.CODE_INITIAL.value: DebateMessageType.INITIAL,
            DebateRound.OPS_REVIEW.value: DebateMessageType.REVIEW,
            DebateRound.SRE_REVIEW.value: DebateMessageType.REVIEW,
            DebateRound.CODE_REVIEW.value: DebateMessageType.REVIEW,
            DebateRound.CONVERGE.value: DebateMessageType.SYNTHESIS,
            DebateRound.DISPUTE.value: DebateMessageType.REVIEW,
            DebateRound.REPORT.value: DebateMessageType.VERDICT,
        }
        return mapping.get(round_obj.value, DebateMessageType.INITIAL)

    def _handle_report_turn(self, turn: DebateTurn) -> None:
        if not self._state:
            return
        self._state.terminated = True
        self._state.termination_reason = "converged"

    # ─────────────────── 同步执行 ───────────────────

    def run_debate(self, ctx: IncidentContext) -> DebateResult:
        self._init_components(ctx)
        next_role: DebateRole | None = DebateRole.OPS  # 从 Ops 开始

        while next_role and not self._should_terminate():
            role = next_role
            round_obj = self._get_round_for_role(role, self._state)
            agent = self._wrapped_agents[role]

            try:
                conclusion, reasoning, meta, next_hint = agent.execute(ctx, self._board, round_obj)
            except Exception as e:
                logger.error(f"Agent {role.value} failed: {e}")
                conclusion, reasoning, meta, next_hint = agent.fallback_stub(round_obj)

            msg = DebateMessage(
                round=round_obj.value,
                role=role.value,
                agent_id=agent.agent_id,
                content=conclusion,
                reasoning=reasoning,
                msg_type=self._msg_type_for_round(round_obj).value,
                meta=meta,
                next_turn=next_hint,
            )

            if self._board:
                self._board.append(msg)

            turn = DebateTurn(
                turn_id=self._state.current_turn,
                round=round_obj.value,
                role=role.value,
                agent_id=agent.agent_id,
                message=msg,
                success=True,
            )
            self._state.add_turn(turn)

            if round_obj == DebateRound.REPORT:
                self._handle_report_turn(turn)

            next_role = self._get_next_speaker(self._state, next_hint)

            if next_hint == NextTurnHint.DISPUTE:
                self._state.terminated = True
                self._state.termination_reason = "dispute"
                break
            if next_hint == NextTurnHint.TERMINATE:
                self._state.terminated = True
                self._state.termination_reason = "terminate"
                break

        return self._build_result()

    # ─────────────────── 流式执行 ───────────────────

    async def stream_debate(
        self,
        ctx: IncidentContext,
        *,
        pause_event: asyncio.Event | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """流式执行动态辩论。

        流程（非固定顺序）：
          1. Ops 独立分析 → 输出 next_turn 指令
          2. 编排器解析指令，决定下一个发言者
          3. 重复，直到 convergence=true / dispute / terminate
          4. 进入 Report 裁决

        next_turn 指令驱动路由：
          ops / sre / code → 对应 Agent 发言
          converge         → SRE 执行收敛判断
          report           → 直接裁决
          dispute          → 人类介入暂停
          terminate        → 辩论终止
        """
        self._init_components(ctx)
        if pause_event is None:
            pause_event = asyncio.Event()
            pause_event.set()

        # 初始：确保 Ops 第一轮发言
        next_role: DebateRole | None = DebateRole.OPS

        try:
            while next_role and not self._should_terminate():
                role = next_role
                round_obj = self._get_round_for_role(role, self._state)
                agent = self._wrapped_agents[role]

                # turn_start 事件
                yield {
                    "type": "turn_start",
                    "turn_id": self._state.current_turn,
                    "turn_index": self._state.current_turn,
                    "round": round_obj.value,
                    "role": role.value,
                    "agent_id": agent.agent_id,
                }

                # 流式收集 token
                full_text = ""
                current_turn_index = self._state.current_turn  # 记录当前轮次索引
                try:
                    # CODE 角色使用 DynamicCodeAgent 流式执行
                    if role == DebateRole.CODE and agent._dynamic_code_agent:
                        async for chunk in agent._dynamic_code_agent.stream_run(ctx):
                            yield {"type": "delta", "agent_id": agent.agent_id, "delta": chunk.delta, "turn_index": current_turn_index}
                            if not chunk.is_done:
                                full_text += chunk.delta
                            else:
                                # 完成时获取结果
                                full_text = agent._board.get("code_output", full_text)
                    else:
                        async for delta_event in self._stream_turn_tokens(ctx, agent, round_obj, current_turn_index):
                            yield delta_event
                            if delta_event["type"] == "delta":
                                full_text += delta_event["delta"]
                except Exception as e:
                    logger.warning(f"Turn {self._state.current_turn} failed: {e}")
                    conclusion, reasoning, meta, next_hint = agent.fallback_stub(round_obj)
                else:
                    conclusion = _extract_conclusion(full_text)
                    reasoning = _extract_reasoning(full_text)
                    meta = _extract_meta(full_text)
                    next_hint = parse_next_turn(full_text)

                msg = DebateMessage(
                    round=round_obj.value,
                    role=role.value,
                    agent_id=agent.agent_id,
                    content=conclusion,
                    reasoning=reasoning,
                    msg_type=self._msg_type_for_round(round_obj).value,
                    meta=meta,
                    next_turn=next_hint,
                )

                if self._board:
                    self._board.append(msg)

                turn = DebateTurn(
                    turn_id=self._state.current_turn,
                    round=round_obj.value,
                    role=role.value,
                    agent_id=agent.agent_id,
                    message=msg,
                    success=True,
                )

                self._state.add_turn(turn)
                ctx.metadata[BOARD_KEY_TURN_OUTPUT] = turn.message.content
                ctx.metadata[BOARD_KEY_DEBATE_HISTORY] = self._board.snapshot()

                if round_obj == DebateRound.REPORT:
                    self._handle_report_turn(turn)

                yield {
                    "type": "turn_done",
                    "turn_id": turn.turn_id,
                    "turn_index": turn.turn_id,
                    "round": round_obj.value,
                    "role": role.value,
                    "agent_id": agent.agent_id,
                    "success": turn.success,
                    "convergence_score": self._state.convergence_score,
                    "next_turn": next_hint.value,
                    "reasoning_preview": turn.message.reasoning[:200],
                }

                # ── 路由决策 ───────────────────────────────
                # 如果任何 Agent 明确要求 report，或收敛成功，则进入 Report 阶段
                if next_hint == NextTurnHint.REPORT:
                    next_role = DebateRole.REPORT
                elif next_hint == NextTurnHint.CONVERGE:
                    # converge 尝试超过 3 次，强制进入 Report
                    if self._state.converge_attempts >= 3:
                        logger.info("收敛尝试已达3次，强制进入 Report 阶段")
                        next_role = DebateRole.REPORT
                    # 检查 convergence 字段是否明确为 true
                    elif meta.get("convergence", "").lower() == "true":
                        logger.info(f"收敛达成 (score={self._state.convergence_score:.2f})，进入 Report 阶段")
                        next_role = DebateRole.REPORT
                    # 检查收敛分数是否足够高
                    elif self._state.convergence_score >= 0.7:
                        logger.info(f"收敛分数达标 (score={self._state.convergence_score:.2f})，进入 Report 阶段")
                        next_role = DebateRole.REPORT
                    else:
                        # 收敛失败，继续由 SRE 审视或进入 Report（降级）
                        logger.warning(f"收敛未达成 (score={self._state.convergence_score:.2f})，强制进入 Report 阶段")
                        next_role = DebateRole.REPORT  # 降级：无论如何都进入 Report
                elif next_hint == NextTurnHint.DISPUTE:
                    self._state.terminated = True
                    self._state.termination_reason = "dispute"
                    disputed = meta.get("disputed_points", [])
                    yield {
                        "type": "pause_request",
                        "reason": "辩论存在未解决争议",
                        "disputed_points": disputed,
                        "convergence_score": self._state.convergence_score,
                    }
                    await pause_event.wait()
                    pause_event.clear()
                    self._state.terminated = True
                    self._state.termination_reason = "human_resolved"
                    yield {"type": "human_confirmed", "approved": True}
                    break
                elif next_hint == NextTurnHint.TERMINATE:
                    self._state.terminated = True
                    self._state.termination_reason = "terminate"
                    break
                else:
                    next_role = self._get_next_speaker(self._state, next_hint)

        finally:
            result = self._build_result()
            
            # 保存到 SharedBoard 供导出接口使用
            try:
                from power_aiops.memory.shared_board import SharedBoard
                board = SharedBoard()
                board.set(f"debate_result_{result.incident_id}", result.to_dict())
            except Exception:
                pass  # 不影响主流程
            
            yield {
                "type": "debate_done",
                "result": result.to_dict(),
                "report_content": result.report_text,
                "export_suggestions": ["docx", "pdf"],
            }

    async def _stream_turn_tokens(
        self,
        ctx: IncidentContext,
        agent: DebateAgentWrapper,
        round_obj: DebateRound,
        turn_index: int,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """流式调用 LLM，每收到一个 token yield delta。"""
        system_prompt = debate_prompt_for(agent.role.value, round_obj.value)
        user_prompt = agent._build_user_prompt(ctx, self._board, round_obj)

        if not self._llm.is_configured():
            # 即使没有 LLM，也尝试使用 stub（包含完整的报告内容）
            import asyncio
            stub_text = agent._stub_output(round_obj.value)
            for char in stub_text:
                await asyncio.sleep(0.005)
                yield {"type": "delta", "agent_id": agent.agent_id, "delta": char, "turn_index": turn_index, "round": round_obj.value}
            return

        if not system_prompt:
            # 如果没有对应 prompt，使用 Report 的默认 prompt
            if agent.role == DebateRole.REPORT:
                system_prompt = DEBATE_PROMPT_REPORT_JUDGE

        async for token in self._llm.chat_stream(system=system_prompt, user=user_prompt):
            yield {"type": "delta", "agent_id": agent.agent_id, "delta": token, "turn_index": turn_index, "round": round_obj.value}

    # ─────────────────── 结果构建 ───────────────────

    def _build_result(self) -> DebateResult:
        if not self._state:
            return DebateResult(incident_id="unknown")

        report_text = ""
        code_script = ""
        for turn in reversed(self._state.turns):
            if turn.round == DebateRound.REPORT.value:
                report_text = turn.message.content
                break
        if not report_text:
            for turn in reversed(self._state.turns):
                if turn.message.content:
                    report_text = turn.message.content
                    break

        for turn in self._state.turns:
            if turn.round == DebateRound.CODE_INITIAL.value:
                code_script = turn.message.content
                break

        llm_calls = sum(1 for t in self._state.turns if t.success)

        return DebateResult(
            incident_id=self._state.incident_id,
            trace_id=self._state.trace_id,
            total_turns=len(self._state.turns),
            total_rounds=self._state.current_turn,
            llm_calls=llm_calls,
            conclusion=report_text[:500],
            report_text=report_text,
            code_script=code_script,
            disputed_points=self._state.disputed_points,
            convergence_score=self._state.convergence_score,
            history=self._state.to_dict(),
            terminated=self._state.terminated,
            termination_reason=self._state.termination_reason,
        )


# ─────────────────── 兼容性别名 ───────────────────

def run_debate(ctx: IncidentContext, **kwargs) -> DebateResult:
    orchestrator = DebateOrchestrator(**kwargs)
    return orchestrator.run_debate(ctx)


async def stream_debate(ctx: IncidentContext, **kwargs) -> AsyncGenerator[dict[str, Any], None]:
    orchestrator = DebateOrchestrator(**kwargs)
    async for event in orchestrator.stream_debate(ctx, **kwargs):
        yield event
