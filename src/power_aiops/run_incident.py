"""与 HTTP API 共用的编排入口：请求体 → IncidentContext → pipeline → 响应。

支持自动入库知识库（使用 LLM 提取结构化信息）。
"""

from __future__ import annotations

import json
import logging
import re
import threading

from power_aiops.api.schemas import IncidentRunRequest, IncidentRunResponse, build_incident_context
from power_aiops.llm.client import OpenAICompatibleClient
from power_aiops.memory.graph_rag import GraphRAG
from power_aiops.memory.shared_board import SharedBoard
from power_aiops.orchestration import run_pipeline, stream_pipeline

logger = logging.getLogger(__name__)

# LLM 提取提示词模板
_EXTRACTION_PROMPT_TEMPLATE = """你是一个故障案例知识抽取专家。请从以下故障处理过程的 Agent 输出中，提取结构化的故障案例信息。

## 故障基本信息
- incident_id: {incident_id}
- summary: {summary}

## Agent 输出内容
{agent_outputs}

## 提取要求
请以 JSON 格式返回以下字段：

{{
    "symptoms": [
        // 症状列表，如 "数据库连接超时", "CPU 使用率 100%", "服务不可达"
        // 每个症状不超过 50 字
    ],
    "root_cause": "根因分析，简洁描述导致故障的根本原因，不超过 200 字",
    "resolution": "解决方案，包括具体步骤和操作方法，不超过 500 字",
    "affected_services": ["受影响的服务列表，如服务名"],
    "affected_hosts": ["涉及的主机或 IP 列表"],
    "keywords": ["有助于检索的关键词列表，3-5 个"]
}}

注意事项：
1. 只提取输出中明确包含的信息，不要编造
2. symptoms 应该简洁、通用，便于后续匹配
3. root_cause 要有技术深度，区分表象和根本原因
4. resolution 要具体可操作
5. 如果某字段无法从输出中提取，则返回空列表或空字符串
6. 返回的 JSON 不要包含 markdown 代码块标记
"""

# 备用规则提取（LLM 不可用时的降级方案）
_BACKUP_EXTRACTION_KEYWORDS = {
    "symptoms": ["超时", "失败", "异常", "错误", "告警", "故障", "不可用", "高负载"],
    "resolution": ["解决", "方案", "处理", "建议", "步骤", "重启", "扩容", "回滚", "切换"],
    "root_cause": ["根因", "原因", "导致", "由于", "root cause", "根本原因"],
}


def _format_agent_outputs(agent_outputs: dict[str, str]) -> str:
    """格式化 Agent 输出用于提示词."""
    parts = []
    for agent_id, output in agent_outputs.items():
        if output:
            parts.append(f"\n### {agent_id}\n{output[:3000]}")
    return "\n".join(parts) if parts else "(无可用输出)"


def _parse_extraction_json(text: str) -> dict | None:
    """解析 LLM 返回的 JSON 结果."""
    text = text.strip()
    # 尝试移除 markdown 代码块
    text = re.sub(r"^```json\s*", "", text)
    text = re.sub(r"^```\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    text = text.strip()

    try:
        import json as _json
        return _json.loads(text)
    except Exception:
        return None


def _extract_with_llm(
    incident_id: str,
    summary: str,
    agent_outputs: dict[str, str],
) -> dict:
    """使用 LLM 从 Agent 输出中提取结构化信息.

    Returns:
        包含 symptoms, root_cause, resolution 等字段的字典
    """
    llm = OpenAICompatibleClient()

    if not llm.is_configured():
        logger.warning("LLM not configured, falling back to rule-based extraction")
        return _fallback_extract(incident_id, summary, agent_outputs)

    try:
        formatted_outputs = _format_agent_outputs(agent_outputs)
        prompt = _EXTRACTION_PROMPT_TEMPLATE.format(
            incident_id=incident_id,
            summary=summary or "待确认",
            agent_outputs=formatted_outputs,
        )

        logger.info(f"Extracting case info with LLM for {incident_id}")
        response = llm.chat(
            system="你是一个专业的故障案例知识抽取专家，只返回 JSON 格式的结果。",
            user=prompt,
        )

        result = _parse_extraction_json(response)
        if result:
            logger.info(f"LLM extraction successful for {incident_id}")
            return {
                "symptoms": result.get("symptoms", []),
                "root_cause": result.get("root_cause", ""),
                "resolution": result.get("resolution", ""),
                "services": result.get("affected_services", []),
                "hosts": result.get("affected_hosts", []),
                "tags": result.get("keywords", []),
            }
        else:
            logger.warning(f"Failed to parse LLM response, falling back to rules for {incident_id}")
            return _fallback_extract(incident_id, summary, agent_outputs)

    except Exception as e:
        logger.warning(f"LLM extraction failed: {e}, falling back to rules")
        return _fallback_extract(incident_id, summary, agent_outputs)


def _fallback_extract(
    incident_id: str,
    summary: str,
    agent_outputs: dict[str, str],
) -> dict:
    """规则匹配降级提取（当 LLM 不可用时使用）."""
    symptoms: list[str] = []
    resolutions: list[str] = []
    root_cause = ""

    for output in agent_outputs.values():
        if not output:
            continue
        for line in output.split("\n"):
            line = line.strip()
            if not line or len(line) > 200:
                continue

            # 提取症状
            if any(kw in line for kw in _BACKUP_EXTRACTION_KEYWORDS["symptoms"]):
                symptoms.append(line[:100])

            # 提取解决方案
            if any(kw in line for kw in _BACKUP_EXTRACTION_KEYWORDS["resolution"]):
                resolutions.append(line[:200])

            # 提取根因
            if any(kw in line.lower() for kw in _BACKUP_EXTRACTION_KEYWORDS["root_cause"]):
                if not root_cause:
                    root_cause = line[:200]

    # 从 SRE-Agent 优先提取根因
    sre_output = agent_outputs.get("SRE-Agent-01", "")
    if sre_output and not root_cause:
        for line in sre_output.split("\n"):
            if any(kw in line.lower() for kw in ["因为", "由于", "原因", "根因"]):
                root_cause = line.strip()[:200]
                break

    return {
        "symptoms": list(dict.fromkeys(symptoms))[:10],
        "root_cause": root_cause or summary[:200],
        "resolution": "\n".join(resolutions[:10])[:1000],
        "services": [],
        "hosts": [],
        "tags": [],
    }


def _auto_persist_to_knowledge_base(
    incident_id: str,
    summary: str,
    agent_outputs: dict[str, str],
    severity: str = "P2",
) -> None:
    """将故障处理结果自动存入知识库.

    使用 LLM 从 Agent 输出中提取结构化的症状、根因和解决方案，
    同时存入 Graph RAG 和 Chroma Vector RAG。
    """
    try:
        extracted = _extract_with_llm(incident_id, summary, agent_outputs)

        # 提取第一个服务名作为参考
        service_name = extracted.get("services", [""])[0] if extracted.get("services") else ""

        case_data = {
            "case_id": incident_id,
            "title": summary or f"故障案例 {incident_id}",
            "summary": summary,
            "symptoms": extracted.get("symptoms", []),
            "root_cause": extracted.get("root_cause", ""),
            "resolution": extracted.get("resolution", ""),
            "severity": severity,
            "services": extracted.get("services", []),
            "hosts": extracted.get("hosts", []),
            "tags": extracted.get("tags", []),
        }

        # 存入 Graph RAG
        try:
            from power_aiops.memory.graph_rag import GraphRAG
            rag = GraphRAG()
            rag.store_case_dict(case_data)
            rag.close()
            logger.info(f"Persisted incident {incident_id} to Graph RAG")
        except Exception as e:
            logger.warning(f"Failed to persist to Graph RAG: {e}")

        # 存入 Chroma Vector RAG
        try:
            from power_aiops.memory.vector_rag import ChromaVectorRAG, IncidentDocument
            vector_rag = ChromaVectorRAG()
            vector_rag.initialize()

            doc = IncidentDocument(
                incident_id=incident_id,
                title=summary or f"故障案例 {incident_id}",
                symptoms=extracted.get("symptoms", []),
                root_cause=extracted.get("root_cause", ""),
                solution=extracted.get("resolution", ""),
                service_name=service_name,
                severity=severity,
            )
            vector_rag.store_incident(doc)
            vector_rag.close()
            logger.info(f"Persisted incident {incident_id} to Chroma Vector RAG")
        except Exception as e:
            logger.warning(f"Failed to persist to Chroma Vector RAG: {e}")

        logger.info(f"Auto-persisted incident {incident_id} to knowledge base (LLM extracted)")

    except Exception as e:
        logger.warning(f"Failed to auto-persist to knowledge base: {e}")


def execute_incident_run(req: IncidentRunRequest) -> IncidentRunResponse:
    """根据请求体构建 IncidentContext 并执行编排 pipeline."""
    ctx = build_incident_context(req)
    board = SharedBoard()
    state = run_pipeline(ctx, board=board)

    # 自动入库知识库（后台线程，不阻塞响应）
    if state.agent_outputs:
        threading.Thread(
            target=_auto_persist_to_knowledge_base,
            args=(ctx.incident_id, ctx.summary, state.agent_outputs),
            daemon=True,
        ).start()

    report_content = board.get("report_output", "")

    # 保存 Pipeline 结果到 SharedBoard 供导出接口使用
    pipeline_result = {
        "incident_id": ctx.incident_id,
        "trace_id": ctx.trace_id,
        "summary": ctx.summary,
        "report_content": report_content,
        "agent_outputs": state.agent_outputs,
        "shared_board": board.snapshot(),
        "completed_steps": state.completed_steps,
    }
    board.set(f"pipeline_result_{ctx.incident_id}", pipeline_result)

    return IncidentRunResponse(
        incident_id=ctx.incident_id,
        trace_id=ctx.trace_id,
        code_blocked=state.code_blocked,
        fence_matched=state.fence_matched,
        completed_steps=state.completed_steps,
        agent_outputs=state.agent_outputs,
        shared_board=board.snapshot(),
        report_content=report_content,
        export_suggestions=["docx", "pdf", "html"],
    )


async def stream_incident_run(req: IncidentRunRequest):
    """异步生成器：流式执行 Pipeline，yield SSE 格式的事件."""
    ctx = build_incident_context(req)
    board = SharedBoard()
    agent_outputs_buffer: dict[str, str] = {}

    # 发送开始事件
    yield _sse_event("start", {
        "incident_id": ctx.incident_id,
        "trace_id": ctx.trace_id,
    })

    # 流式执行 pipeline
    async for chunk in stream_pipeline(ctx, board=board):
        # 收集 agent 输出用于后续入库
        if chunk.is_done:
            current_output = board.get(f"{_agent_id_to_key(chunk.agent_id)}_output", "")
            if current_output:
                agent_outputs_buffer[chunk.agent_id] = current_output

        yield _sse_event("chunk", {
            "agent_id": chunk.agent_id,
            "delta": chunk.delta,
            "is_done": chunk.is_done,
            "blocked": chunk.blocked,
            "fence_matched": chunk.fence_matched,
        })

    # 自动入库知识库（后台线程，不阻塞 SSE done 事件）
    if agent_outputs_buffer:
        threading.Thread(
            target=_auto_persist_to_knowledge_base,
            args=(ctx.incident_id, ctx.summary, agent_outputs_buffer),
            daemon=True,
        ).start()

    # 发送完成事件（包含报告内容供前端保存）
    report_content = board.get("report_output", "")

    # 保存 Pipeline 结果到 SharedBoard 供导出接口使用
    pipeline_result = {
        "incident_id": ctx.incident_id,
        "trace_id": ctx.trace_id,
        "summary": ctx.summary,
        "report_content": report_content,
        "agent_outputs": agent_outputs_buffer,
        "shared_board": board.snapshot(),
        "completed_steps": list(agent_outputs_buffer.keys()),
    }
    board.set(f"pipeline_result_{ctx.incident_id}", pipeline_result)

    yield _sse_event("done", {
        "incident_id": ctx.incident_id,
        "trace_id": ctx.trace_id,
        "shared_board": board.snapshot(),
        "report_content": report_content,
        "agent_outputs": agent_outputs_buffer,
        "export_suggestions": ["docx", "pdf"],
    })


def _agent_id_to_key(agent_id: str) -> str:
    """将 agent_id 转换为 board key."""
    mapping = {
        "Ops-Agent-01": "ops_output",
        "SRE-Agent-01": "sre_output",
        "Code-Agent-01": "code_output",
        "DynamicCode-Agent-01": "code_output",  # DynamicCodeAgent 使用相同的 key
        "Report-Agent-01": "report_output",
    }
    return mapping.get(agent_id, f"{agent_id.lower().replace('-', '_')}_output")


def _sse_event(event_type: str, data: dict) -> str:
    """构造 SSE 格式的事件字符串"""
    return f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def demo_request() -> IncidentRunRequest:
    """与 `POST /incidents/demo` 使用相同示例."""
    return IncidentRunRequest(
        incident_id="INC-DEMO",
        trace_id="trace-demo",
        device_id="demo-host-01",
        metric_type="cpu_usage",
        value=93.5,
    )
