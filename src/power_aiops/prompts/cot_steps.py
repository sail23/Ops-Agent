"""
运维五步思维链（Chain-of-Thought）模板，与业务开发文档中的结构化推理一致。

输出应为分步推理，禁止跳步直接给结论；每步有明确 [输出] 占位，便于解析或展示。
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# 单步模板（可单独拼接或注入到总模板）
# ---------------------------------------------------------------------------

STEP1_PHENOMENON = """\
Step1 — 现象确认
- 复述核心告警指标，排除误报可能。
- [输出]：确认故障是否真实存在；若存在，影响范围为：{impact_scope_placeholder}\
"""

STEP2_IMPACT = """\
Step2 — 影响评估
- 分析对业务稳定性、可靠性的影响，判断故障等级与是否需启动应急预案。
- [输出]：故障等级为 [级别]；需立即启动 [级别] 应急预案（或：暂不需启动）。\
"""

STEP3_ROOT_CAUSE = """\
Step3 — 根因推导
- 列出可能原因；引用知识库或历史案例中相似案例作为佐证。
- [输出]：最可能根因为 [原因]；佐证为 [证据]；不确定性说明：{uncertainty_note}\
"""

STEP4_PLAN = """\
Step4 — 方案制定
- 主方案：按原子操作列出步骤（步骤1、2、3…）。
- 回滚方案：主方案失败时的恢复路径。
- [输出]：主方案如下；回滚方案如下。\
"""

STEP5_RISK = """\
Step5 — 风险自检
- 该方案是否对其他系统或业务有影响？是否触碰安规/网络安全红线？
- [输出]：经自检，无违规风险 / 发现风险点 [风险] 并已修正 / 需人工审批后方可执行。\
"""

# ---------------------------------------------------------------------------
# 总模板：一次注入故障名称与约束
# ---------------------------------------------------------------------------

COT_FULL_TEMPLATE = """\
【任务】分析并处置故障：{fault_name}

【约束】{constraints}

请严格按以下五步顺序推理，每一步先给出推理过程，再给出该步规定的 [输出] 字段。

{step1}

{step2}

{step3}

{step4}

{step5}

【风格】保守型（默认假设方案与命令均有风险）、严谨型（关键结论须有依据）、审计型（结论须可追溯到告警、知识库或外部权威来源，禁止凭空捏造）。
"""


def format_cot_prompt(
    fault_name: str,
    *,
    constraints: str = "严格遵守电力安全工作规程及本单位运维规范；未经审批不得对生产环境执行写操作。",
    impact_scope_placeholder: str = "（待根据监控与拓扑填写）",
    uncertainty_note: str = "（待补充验证项）",
) -> str:
    """生成完整五步思维链提示词，供 Ops/SRE 等 Agent 使用。"""
    s1 = STEP1_PHENOMENON.format(impact_scope_placeholder=impact_scope_placeholder)
    s3 = STEP3_ROOT_CAUSE.format(uncertainty_note=uncertainty_note)
    return COT_FULL_TEMPLATE.format(
        fault_name=fault_name,
        constraints=constraints,
        step1=s1,
        step2=STEP2_IMPACT,
        step3=s3,
        step4=STEP4_PLAN,
        step5=STEP5_RISK,
    )


def cot_step_labels() -> tuple[str, ...]:
    """五步固定标签，便于 UI 或解析。"""
    return (
        "现象确认",
        "影响评估",
        "根因推导",
        "方案制定",
        "风险自检",
    )
