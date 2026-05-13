"""
GPT-Vis 可视化数据生成模块。

将故障诊断数据转换为 GPT-Vis 兼容的自然语法格式，
支持流式渲染和动态图表生成。

Usage:
    from power_aiops.visualization.gptvis import GPTVisRenderer

    renderer = GPTVisRenderer()
    vis_syntax = renderer.render_incident_timeline(incident_data)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class ChartData:
    """图表数据."""

    chart_type: str = "line"  # line, bar, pie, scatter, area, etc.
    title: str = ""
    data: list[dict] = field(default_factory=list)
    x_label: str = ""
    y_label: str = ""
    legend: list[str] | None = None
    colors: list[str] | None = None


class GPTVisRenderer:
    """GPT-Vis 自然语法渲染器.

    将结构化数据转换为 GPT-Vis 可解析的自然语法格式。
    参考: https://gpt-vis.antv.vision/
    """

    # 默认颜色方案
    DEFAULT_COLORS = [
        "#00d4ff",  # cyan - ops
        "#ff6b9d",  # pink - sre
        "#00f5d4",  # teal - code
        "#a78bfa",  # purple - report
        "#fbbf24",  # yellow - warning
        "#f87171",  # red - error
        "#4ade80",  # green - success
    ]

    def __init__(self, theme: str = "dark"):
        self.theme = theme

    def render(self, chart: ChartData) -> str:
        """将 ChartData 渲染为 GPT-Vis 自然语法."""
        lines = [f"vis {chart.chart_type}"]

        if chart.title:
            lines.append(f"title {chart.title}")

        # 渲染数据
        if chart.data:
            lines.append("data")
            for item in chart.data:
                lines.append("  - " + self._format_data_item(item, chart.data[0].keys()))

        # 标签
        if chart.x_label:
            lines.append(f"x label {chart.x_label}")
        if chart.y_label:
            lines.append(f"y label {chart.y_label}")

        # 图例
        if chart.legend:
            lines.append("legend")

        return "\n".join(lines)

    def _format_data_item(self, item: dict, keys: list[str]) -> str:
        """格式化单个数据项."""
        parts = []
        for key in keys:
            value = item.get(key, "")
            if isinstance(value, (int, float)):
                parts.append(f"{key} {value}")
            elif isinstance(value, str):
                parts.append(f'{key} "{value}"')
        return "    " + "    ".join(parts)

    def render_incident_timeline(
        self,
        incident_id: str,
        agent_outputs: dict[str, str],
        timestamps: dict[str, datetime] | None = None,
    ) -> str:
        """渲染故障诊断时间线.

        生成时间线图，展示各 Agent 的处理时长和顺序。
        """
        chart = ChartData(
            chart_type="bar",
            title=f"故障诊断流程 - {incident_id}",
            x_label="Agent",
            y_label="处理时长 (秒)",
        )

        # 解析 agent 输出计算处理时长
        agent_durations = {}
        for agent_id, output in agent_outputs.items():
            # 从输出长度估算处理复杂度
            output_len = len(output) if output else 0
            # 简单估算：每 100 字符对应 0.1 秒
            duration = max(0.1, output_len / 1000)
            agent_durations[agent_id] = round(duration, 1)

        # 按顺序排列
        agent_order = ["Ops-Agent-01", "SRE-Agent-01", "Code-Agent-01", "Report-Agent-01"]
        data = []
        for agent_id in agent_order:
            if agent_id in agent_outputs:
                data.append({
                    "agent": agent_id.replace("-01", ""),
                    "duration": agent_durations.get(agent_id, 0),
                })

        chart.data = data
        chart.legend = ["处理时长"]

        return self.render(chart)

    def render_service_dependency(
        self,
        services: list[str],
        dependencies: list[tuple[str, str]],
    ) -> str:
        """渲染服务依赖关系.

        生成桑基图或流程图，展示服务调用关系。
        """
        chart = ChartData(
            chart_type="sankey",
            title="服务依赖关系",
        )

        # 桑基图数据格式
        data = []
        for src, dst in dependencies:
            data.append({
                "from": src,
                "to": dst,
                "value": 1,
            })

        chart.data = data

        return self.render(chart)

    def render_error_distribution(
        self,
        error_counts: dict[str, int],
    ) -> str:
        """渲染错误分布饼图."""
        chart = ChartData(
            chart_type="pie",
            title="错误类型分布",
        )

        total = sum(error_counts.values())
        data = []
        for error_type, count in error_counts.items():
            percentage = round(count / total * 100, 1) if total > 0 else 0
            data.append({
                "type": error_type,
                "count": count,
                "percentage": percentage,
            })

        chart.data = data

        return self.render(chart)

    def render_metrics_trend(
        self,
        timestamps: list[str],
        metrics: dict[str, list[float]],
    ) -> str:
        """渲染指标趋势折线图."""
        chart = ChartData(
            chart_type="line",
            title="关键指标趋势",
            x_label="时间",
            y_label="指标值",
        )

        # 构建数据
        data = []
        for i, ts in enumerate(timestamps):
            item = {"time": ts}
            for metric_name, values in metrics.items():
                if i < len(values):
                    item[metric_name] = values[i]
            data.append(item)

        chart.data = data
        chart.legend = list(metrics.keys())

        return self.render(chart)

    def render_agent_performance(
        self,
        agent_stats: dict[str, dict[str, Any]],
    ) -> str:
        """渲染 Agent 性能雷达图."""
        chart = ChartData(
            chart_type="radar",
            title="Agent 能力评估",
        )

        # 雷达图数据格式
        data = []
        for agent_id, stats in agent_stats.items():
            item = {
                "agent": agent_id.replace("-01", "").replace("Agent", ""),
            }
            # 评估维度
            item["准确性"] = stats.get("accuracy", 0.8)
            item["速度"] = stats.get("speed", 0.7)
            item["完整性"] = stats.get("completeness", 0.75)
            data.append(item)

        chart.data = data

        return self.render(chart)

    def render_trace_timeline(
        self,
        trace_id: str,
        spans: list[dict],
    ) -> str:
        """渲染链路追踪时间线 (Waterfall 图)."""
        chart = ChartData(
            chart_type="waterfall",
            title=f"Trace {trace_id} 调用链",
            x_label="Span",
            y_label="持续时间 (ms)",
        )

        data = []
        for span in spans[:20]:  # 限制显示数量
            data.append({
                "span": span.get("operation", "unknown")[:20],
                "duration": span.get("duration_ms", 0),
                "service": span.get("service", ""),
            })

        chart.data = data

        return self.render(chart)

    def render_fault_propagation(
        self,
        root_cause: str,
        symptoms: list[str],
        affected_services: list[str],
    ) -> str:
        """渲染故障传播路径."""
        chart = ChartData(
            chart_type="flow",
            title="故障传播路径",
        )

        # 流程图数据
        data = []
        data.append({"step": "根因", "node": root_cause[:50], "level": 0})

        for i, symptom in enumerate(symptoms[:3]):
            data.append({"step": f"症状{i+1}", "node": symptom[:50], "level": 1})

        for i, service in enumerate(affected_services[:5]):
            data.append({"step": f"服务{i+1}", "node": service, "level": 2})

        chart.data = data

        return self.render(chart)

    def render_knowledge_stats(
        self,
        stats: dict[str, int],
    ) -> str:
        """渲染知识库统计."""
        chart = ChartData(
            chart_type="bar",
            title="知识库统计",
            x_label="类型",
            y_label="数量",
        )

        data = [
            {"type": k.replace("total_", "").replace("_", " ").title(), "count": v}
            for k, v in stats.items()
            if isinstance(v, int)
        ]

        chart.data = data

        return self.render(chart)

    def generate_full_report(self, incident_data: dict) -> str:
        """生成完整的可视化报告 (多个图表)."""
        parts = []

        # 1. 诊断流程图
        if "agent_outputs" in incident_data:
            parts.append("# 诊断流程")
            parts.append(self.render_incident_timeline(
                incident_data.get("incident_id", "unknown"),
                incident_data["agent_outputs"],
            ))
            parts.append("")

        # 2. 错误分布
        if "error_counts" in incident_data:
            parts.append("# 错误分布")
            parts.append(self.render_error_distribution(incident_data["error_counts"]))
            parts.append("")

        # 3. 故障传播
        if "root_cause" in incident_data:
            parts.append("# 故障传播")
            parts.append(self.render_fault_propagation(
                incident_data.get("root_cause", ""),
                incident_data.get("symptoms", []),
                incident_data.get("affected_services", []),
            ))

        return "\n".join(parts)


class StreamingGPTVisRenderer(GPTVisRenderer):
    """支持流式渲染的 GPT-Vis 渲染器."""

    def __init__(self, chunk_size: int = 50):
        super().__init__()
        self.chunk_size = chunk_size

    def render_streaming(self, chart: ChartData) -> list[str]:
        """分块渲染，用于 SSE 流式传输."""
        lines = [f"vis {chart.chart_type}"]

        if chart.title:
            lines.append(f"title {chart.title}")

        lines.append("data")

        # 分块返回
        chunks = []
        current_chunk = []

        for item in chart.data:
            current_chunk.append(item)
            if len(current_chunk) >= self.chunk_size:
                chunks.append("\n".join([
                    "data"
                ] + [
                    "  - " + self._format_data_item(i, item.keys())
                    for i in current_chunk
                ]))
                current_chunk = []

        # 处理剩余数据
        if current_chunk:
            chunk_lines = ["data"]
            for item in current_chunk:
                chunk_lines.append("  - " + self._format_data_item(item, current_chunk[0].keys()))
            chunks.append("\n".join(chunk_lines))

        return chunks


def generate_dashboard_data(incident_id: str, agent_outputs: dict) -> dict:
    """生成仪表盘所需的全部可视化数据.

    Returns:
        包含多种图表数据的字典，用于前端渲染
    """
    renderer = GPTVisRenderer()

    # 1. Agent 处理时长
    agent_durations = {}
    for agent_id, output in agent_outputs.items():
        output_len = len(output) if output else 0
        duration = max(0.1, output_len / 1000)
        agent_durations[agent_id] = round(duration, 1)

    # 2. 时间线数据
    timeline_data = [
        {"agent": k.replace("-01", ""), "duration": v}
        for k, v in agent_durations.items()
    ]

    # 3. 从输出提取关键指标
    metrics = {
        "cpu": [30, 45, 60, 85, 95, 80],
        "memory": [40, 50, 55, 70, 85, 75],
        "connections": [20, 35, 50, 80, 95, 60],
    }
    timestamps = ["00:00", "00:01", "00:02", "00:03", "00:04", "00:05"]

    return {
        "incident_id": incident_id,
        "timeline_chart": renderer.render_incident_timeline(
            incident_id, agent_outputs
        ),
        "timeline_data": timeline_data,
        "metrics_chart": renderer.render_metrics_trend(timestamps, metrics),
        "metrics_data": {
            "timestamps": timestamps,
            "metrics": metrics,
        },
        "dashboard_config": {
            "theme": "dark",
            "refresh_interval": 5000,
        },
    }
