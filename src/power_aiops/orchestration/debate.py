"""辩论编排数据模型：轮次、消息、状态、结果。

动态辩论（阶段二）：
  - 每个 Agent 发言后，LLM 输出 next_turn 指令决定下一个发言者
  - 编排器解析指令，决定下一个 Agent（可动态插入/跳过轮次）
  - 收敛判断：算法 + LLM 双层判断
  - 人类介入：争议时暂停确认

next_turn 指令格式：
  - "ops"       → Ops 发言
  - "sre"       → SRE 发言
  - "code"      → Code 发言
  - "report"    → 直接进入 Report 裁决
  - "converge"  → SRE 综合收敛
  - "dispute"   → 人类介入
  - "terminate"  → 辩论终止
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4


# SharedBoard 兼容键（供 orchestrator 写入 metadata）
BOARD_KEY_TURN_OUTPUT = "debate_turn_output"
BOARD_KEY_DEBATE_HISTORY = "debate_history"
BOARD_KEY_FINAL_REPORT = "debate_final_report"
BOARD_KEY_ROOT_CAUSE = "debate_root_cause"
BOARD_KEY_CONVERGENCE = "debate_convergence"


class DebateRole(Enum):
    """辩论参与者角色."""

    OPS = "ops"       # 运维专家：初判、事件分发
    SRE = "sre"      # SRE 架构师：方案制定、风险评估
    CODE = "code"     # 代码专家：脚本生成与执行
    REPORT = "report" # 裁判：裁决、报告生成


class DebateRound(Enum):
    """辩论轮次（动态模式，不再使用固定顺序编号）。

    每个轮次代表一个 Agent 的一次发言。
    通过 next_turn 指令动态串联。
    """

    # ── 初始分析 ─────────────────────────────────────
    OPS_INITIAL = "ops_initial"       # Ops 第一轮独立分析
    SRE_INITIAL = "sre_initial"       # SRE 第一轮独立分析
    CODE_INITIAL = "code_initial"     # Code 第一轮独立分析

    # ── 动态审视轮（可重复插入） ─────────────────────
    OPS_REVIEW = "ops_review"         # Ops 审视其他 Agent 结论
    SRE_REVIEW = "sre_review"          # SRE 审视其他 Agent 结论
    CODE_REVIEW = "code_review"        # Code 审视其他 Agent 结论

    # ── 收敛阶段 ──────────────────────────────────────
    CONVERGE = "converge"             # SRE 综合收敛判断
    DISPUTE = "dispute"              # 人类介入争议确认

    # ── 裁决阶段 ─────────────────────────────────────
    REPORT = "report"                 # Report 裁决并生成报告

    # ── 元信息 ────────────────────────────────────────
    TERMINATED = "terminated"


class DebateMessageType(Enum):
    """消息交互类型."""

    INITIAL = "initial"       # 初始发言
    REVIEW = "review"         # 审视/质疑/反驳
    RESPONSE = "response"    # 回应质疑
    SYNTHESIS = "synthesis"  # 综合收敛
    VERDICT = "verdict"      # 裁决


class NextTurnHint(Enum):
    """LLM 输出的下一轮指令（由解析器从文本提取）。"""

    OPS = "ops"
    SRE = "sre"
    CODE = "code"
    REPORT = "report"
    CONVERGE = "converge"   # SRE 综合收敛
    DISPUTE = "dispute"     # 人类介入
    TERMINATE = "terminate" # 提前终止


@dataclass
class DebateMessage:
    """辩论中的一条消息，包含完整推理过程."""

    msg_id: str = field(default_factory=lambda: uuid4().hex[:12])
    round: str = ""
    role: str = ""
    agent_id: str = ""

    # 消息核心内容
    content: str = ""         # 最终结论/输出
    reasoning: str = ""      # 推理过程

    # 动态轮次指令（由 LLM 输出决定下一个发言者）
    next_turn: NextTurnHint = NextTurnHint.TERMINATE  # 默认终止

    # 元信息
    msg_type: str = DebateMessageType.INITIAL.value
    references: list[str] = field(default_factory=list)
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class DebateTurn:
    """一轮辩论（一个 Agent 的一次发言为一轮 Turn）。"""

    turn_id: int
    round: str
    role: str
    agent_id: str
    message: DebateMessage

    # 执行结果
    success: bool = True
    error: str = ""


# ── 解析 next_turn 指令的辅助函数 ──────────────────────────────

_NEXT_TURN_RE = re.compile(
    r"##\s*next_turn\s*\n(.+?)(?=\n##|\Z)",
    re.IGNORECASE | re.DOTALL,
)


def parse_next_turn(text: str) -> NextTurnHint:
    """从 LLM 输出文本中解析 next_turn 指令。"""
    m = _NEXT_TURN_RE.search(text)
    if not m:
        # fallback：关键词扫描
        raw = text.lower()
        if "converge" in raw or "收敛" in raw:
            return NextTurnHint.CONVERGE
        if "report" in raw or "裁决" in raw:
            return NextTurnHint.REPORT
        if "dispute" in raw or "争议" in raw or "人类" in raw:
            return NextTurnHint.DISPUTE
        if "terminate" in raw or "终止" in raw:
            return NextTurnHint.TERMINATE
        return NextTurnHint.TERMINATE

    hint_text = m.group(1).strip().lower()
    if "sre" in hint_text and "converge" not in hint_text:
        return NextTurnHint.SRE
    if "code" in hint_text:
        return NextTurnHint.CODE
    if "ops" in hint_text:
        return NextTurnHint.OPS
    if "converge" in hint_text:
        return NextTurnHint.CONVERGE
    if "dispute" in hint_text or "人类" in hint_text:
        return NextTurnHint.DISPUTE
    if "report" in hint_text or "裁决" in hint_text:
        return NextTurnHint.REPORT
    if "terminate" in hint_text or "终止" in hint_text:
        return NextTurnHint.TERMINATE
    return NextTurnHint.TERMINATE


def next_turn_hint_to_role(hint: NextTurnHint) -> DebateRole | None:
    """将 next_turn 指令映射为对应的 Agent 角色。"""
    mapping = {
        NextTurnHint.OPS: DebateRole.OPS,
        NextTurnHint.SRE: DebateRole.SRE,
        NextTurnHint.CODE: DebateRole.CODE,
        NextTurnHint.REPORT: DebateRole.REPORT,
        NextTurnHint.CONVERGE: DebateRole.SRE,   # converge 由 SRE 执行
        NextTurnHint.DISPUTE: None,              # dispute 不对应 Agent
        NextTurnHint.TERMINATE: None,            # terminate 不对应 Agent
    }
    return mapping.get(hint)


def next_turn_hint_to_round(hint: NextTurnHint) -> DebateRound:
    """将 next_turn 指令映射为对应的辩论轮次。"""
    mapping = {
        NextTurnHint.OPS: DebateRound.OPS_REVIEW,
        NextTurnHint.SRE: DebateRound.SRE_REVIEW,
        NextTurnHint.CODE: DebateRound.CODE_REVIEW,
        NextTurnHint.REPORT: DebateRound.REPORT,
        NextTurnHint.CONVERGE: DebateRound.CONVERGE,
        NextTurnHint.DISPUTE: DebateRound.DISPUTE,
        NextTurnHint.TERMINATE: DebateRound.TERMINATED,
    }
    return mapping.get(hint, DebateRound.TERMINATED)


@dataclass
class DebateState:
    """辩论状态的完整快照（可 JSON 序列化）。"""

    incident_id: str
    trace_id: str = ""

    # 轮次控制
    current_turn: int = 0
    max_turns: int = 12           # 最大发言次数（安全阀）
    terminated: bool = False
    termination_reason: str = ""

    # 辩论记录
    turns: list[DebateTurn] = field(default_factory=list)

    # 阶段性立场（用于检测收敛）
    ops_position: str = ""
    sre_position: str = ""
    code_position: str = ""
    convergence_score: float = 0.0

    # 动态路由状态
    has_ops_initial: bool = False  # R0 Ops 是否完成
    has_sre_initial: bool = False  # R0 SRE 是否完成
    has_code_initial: bool = False  # R0 Code 是否完成
    converge_attempts: int = 0       # 收敛尝试次数（防止无限循环）

    # 争议记录
    disputed_points: list[str] = field(default_factory=list)

    def add_turn(self, turn: DebateTurn) -> None:
        self.turns.append(turn)
        self.current_turn += 1

        content = turn.message.content[:500]
        if turn.role == DebateRole.OPS.value:
            self.ops_position = content
            self.has_ops_initial = True
        elif turn.role == DebateRole.SRE.value:
            self.sre_position = content
            self.has_sre_initial = True
        elif turn.role == DebateRole.CODE.value:
            self.code_position = content
            self.has_code_initial = True

        self._update_convergence()

    def _update_convergence(self) -> None:
        """基于各 Agent 最新立场计算收敛分（0.0~1.0）。"""
        positions = [self.ops_position, self.sre_position, self.code_position]
        filled = [p for p in positions if p]
        if len(filled) < 2:
            self.convergence_score = 0.0
            return

        tokens_a = set(self.ops_position.split())
        tokens_b = set(self.sre_position.split())
        tokens_c = set(self.code_position.split())

        def jaccard(a: set, b: set) -> float:
            return len(a & b) / max(len(a | b), 1)

        overlap_ab = jaccard(tokens_a, tokens_b)
        overlap_bc = jaccard(tokens_b, tokens_c)
        overlap_ac = jaccard(tokens_a, tokens_c)

        self.convergence_score = round((overlap_ab + overlap_bc + overlap_ac) / 3, 3)

    def to_dict(self) -> dict[str, Any]:
        return {
            "incident_id": self.incident_id,
            "trace_id": self.trace_id,
            "current_turn": self.current_turn,
            "max_turns": self.max_turns,
            "terminated": self.terminated,
            "termination_reason": self.termination_reason,
            "turns": [
                {
                    "turn_id": t.turn_id,
                    "round": t.round,
                    "role": t.role,
                    "agent_id": t.agent_id,
                    "content": t.message.content,
                    "reasoning": t.message.reasoning,
                    "msg_type": t.message.msg_type,
                    "references": t.message.references,
                    "timestamp": t.message.timestamp,
                    "success": t.success,
                    "error": t.error,
                }
                for t in self.turns
            ],
            "ops_position": self.ops_position,
            "sre_position": self.sre_position,
            "code_position": self.code_position,
            "convergence_score": self.convergence_score,
            "converge_attempts": self.converge_attempts,
            "disputed_points": self.disputed_points,
        }


@dataclass
class DebateResult:
    """辩论最终结果（供 API 响应使用）。"""

    incident_id: str
    trace_id: str = ""

    # 辩论统计
    total_turns: int = 0
    total_rounds: int = 0
    llm_calls: int = 0

    # 结论
    conclusion: str = ""
    report_text: str = ""

    # Code Agent 输出
    code_script: str = ""
    code_executed: bool = False
    code_output: str = ""

    # 争议点
    disputed_points: list[str] = field(default_factory=list)

    # 收敛信息
    convergence_score: float = 0.0
    human_approved: bool = False

    # 辩论历史（JSON 快照）
    history: dict[str, Any] = field(default_factory=dict)

    # 状态
    terminated: bool = False
    termination_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "incident_id": self.incident_id,
            "trace_id": self.trace_id,
            "total_turns": self.total_turns,
            "total_rounds": self.total_rounds,
            "llm_calls": self.llm_calls,
            "conclusion": self.conclusion,
            "report_text": self.report_text,
            "code_script": self.code_script,
            "disputed_points": self.disputed_points,
            "convergence_score": self.convergence_score,
            "human_approved": self.human_approved,
            "terminated": self.terminated,
            "termination_reason": self.termination_reason,
            "history": self.history,
        }
