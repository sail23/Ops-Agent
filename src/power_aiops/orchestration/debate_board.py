"""辩论消息池：存放辩论全过程中所有 Agent 的发言记录。"""

from __future__ import annotations

from threading import RLock
from typing import TYPE_CHECKING, Any

from power_aiops.orchestration.debate import DebateMessage, DebateMessageType, DebateRole

if TYPE_CHECKING:
    from power_aiops.orchestration.debate import DebateMessage


class DebateBoard:
    """线程安全的辩论消息池，记录所有 Agent 的发言与推理过程。

    与 SharedBoard 的区别：
    - SharedBoard：存放当前/latest 状态，各 Agent 覆盖写入
    - DebateBoard：存放完整历史，不可覆盖，只能追加

    辩论中的每个 Agent 通过 read() 获取辩论上下文，
    通过 append() 注册自己的发言。
    """

    def __init__(self) -> None:
        self._lock = RLock()
        self._messages: list[DebateMessage] = []
        self._latest_by_role: dict[str, DebateMessage] = {}  # role -> latest message

    # ─────────────────── 写入 ───────────────────

    def append(self, message: DebateMessage) -> None:
        """追加一条消息（辩论记录只能追加，不能修改）。"""
        with self._lock:
            self._messages.append(message)
            self._latest_by_role[message.role] = message

    def add_disputed_point(self, point: str) -> None:
        """记录一个争议点（供 ReportAgent 裁决用）。"""
        with self._lock:
            for msg in reversed(self._messages):
                if msg.msg_type == DebateMessageType.VERDICT.value:
                    msg.meta.setdefault("disputed_points", []).append(point)
                    return

    # ─────────────────── 读取 ───────────────────

    def read(
        self,
        role: str | None = None,
        current_role: str | None = None,
        max_messages: int = 20,
    ) -> str:
        """读取辩论上下文（供 LLM 输入用）。

        Args:
            role: 限制只返回某角色的消息（如只读 SRE 的发言）
            current_role: 当前发言的 Agent（用于过滤，避免只看自己的发言）
            max_messages: 最多返回多少条消息（避免 LLM 上下文溢出）

        Returns:
            格式化后的辩论上下文字符串
        """
        with self._lock:
            messages = list(self._messages)

        # 按角色过滤
        if role:
            messages = [m for m in messages if m.role == role]

        # 按 turn_id 倒序取最近 max_messages 条
        messages = messages[-max_messages:]

        return self._format_messages(messages)

    def read_all(self) -> list[DebateMessage]:
        """返回辩论全量历史（用于调试/持久化）。"""
        with self._lock:
            return list(self._messages)

    def read_for_agent(
        self,
        agent_role: str,
        show_reasoning: bool = True,
    ) -> str:
        """构建发送给特定 Agent 的辩论上下文。

        - 包含其他 Agent 的结论（不含或仅含简要推理）
        - 包含争议点（如果有）
        - 不包含自身历史（避免自我强化）
        """
        with self._lock:
            others = [m for m in self._messages if m.role != agent_role]

        return self._format_as_prompt_context(others, show_reasoning)

    def latest(self, role: str) -> DebateMessage | None:
        """获取某角色最近一条消息。"""
        with self._lock:
            return self._latest_by_role.get(role)

    def latest_of(self, roles: list[str]) -> list[DebateMessage]:
        """获取多个角色各自的最新消息。"""
        with self._lock:
            return [self._latest_by_role[r] for r in roles if r in self._latest_by_role]

    def disputed_points(self) -> list[str]:
        """收集所有争议点（meta 中带 disputed_points 的消息）。"""
        with self._lock:
            points: list[str] = []
            for msg in self._messages:
                points.extend(msg.meta.get("disputed_points", []))
            return points

    def count(self) -> int:
        """消息总数。"""
        with self._lock:
            return len(self._messages)

    # ─────────────────── 格式化 ───────────────────

    def _format_messages(self, messages: list[DebateMessage]) -> str:
        """将消息列表格式化为可读文本。"""
        if not messages:
            return ""

        lines: list[str] = ["## 辩论记录"]
        for msg in messages:
            role_label = DebateRole(msg.role).name if msg.role in [r.value for r in DebateRole] else msg.role

            lines.append(f"\n### [{msg.round}] {role_label} ({msg.msg_type})")
            lines.append(f"<time> {msg.timestamp}")

            if include_reasoning(msg.msg_type):
                lines.append(f"\n**推理过程**:\n{msg.reasoning[:1500]}")
                lines.append(f"\n**结论**:\n{msg.content[:1000]}")
            else:
                lines.append(f"\n{msg.content[:2000]}")

            if msg.meta.get("disputed_points"):
                lines.append(f"\n⚠️ 争议点: {msg.meta['disputed_points']}")

        return "\n".join(lines)

    def _format_as_prompt_context(
        self,
        messages: list[DebateMessage],
        include_reasoning: bool,
    ) -> str:
        """格式化为 LLM 的 system prompt 上下文。"""
        if not messages:
            return ""

        lines: list[str] = ["## 辩论上下文（供参考）"]

        for msg in messages:
            role_label = _role_display_name(msg.role)
            lines.append(f"\n### {role_label} 的观点:")
            if include_reasoning and msg.reasoning:
                lines.append(f"[推理] {msg.reasoning[:1000]}")
            lines.append(f"[结论] {msg.content[:800]}")

        return "\n".join(lines)

    # ─────────────────── 快照 ───────────────────

    def snapshot(self) -> dict[str, Any]:
        """返回消息池快照（用于持久化）。"""
        with self._lock:
            return {
                "messages": [
                    {
                        "msg_id": m.msg_id,
                        "round": m.round,
                        "role": m.role,
                        "agent_id": m.agent_id,
                        "content": m.content,
                        "reasoning": m.reasoning,
                        "msg_type": m.msg_type,
                        "references": m.references,
                        "timestamp": m.timestamp,
                    }
                    for m in self._messages
                ],
                "count": len(self._messages),
            }


def _role_display_name(role: str) -> str:
    """角色显示名称."""
    mapping = {
        DebateRole.OPS.value: "Ops-Agent",
        DebateRole.SRE.value: "SRE-Agent",
        DebateRole.CODE.value: "Code-Agent",
        DebateRole.REPORT.value: "Report-Agent",
    }
    return mapping.get(role, role)


def include_reasoning(msg_type: str) -> bool:
    """判断该消息类型是否应包含推理过程（给其他 Agent 看）。"""
    return msg_type in {
        DebateMessageType.INITIAL.value,
        DebateMessageType.REVIEW.value,
        DebateMessageType.RESPONSE.value,
        DebateMessageType.SYNTHESIS.value,
    }
