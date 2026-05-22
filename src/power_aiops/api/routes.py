"""API routes for incidents and knowledge base."""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field
from typing import Union

from power_aiops.api.schemas import (
    DebateRunRequest,
    DebateRunResponse,
    DebateTurnItem,
    FaultCaseCreate,
    FaultCaseResponse,
    FaultCaseUpdate,
    IncidentRunRequest,
    IncidentRunResponse,
    KnowledgeBaseStats,
    SimilarCaseResponse,
    build_incident_context,
)
from power_aiops.memory.graph_rag import GraphRAG
from power_aiops.run_incident import demo_request, execute_incident_run, stream_incident_run

router = APIRouter()


# ─────────────────────────────────────────────────────────────────────────────
# Incident Pipeline Routes
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/run", response_model=IncidentRunResponse)
def post_incident_run(body: IncidentRunRequest) -> IncidentRunResponse:
    """根据请求体构建 IncidentContext 并执行编排 pipeline."""
    return execute_incident_run(body)


@router.post("/run/stream")
def post_incident_run_stream(body: IncidentRunRequest) -> StreamingResponse:
    """流式执行 Pipeline，Server-Sent Events 输出每个 Agent 的实时响应."""
    return StreamingResponse(
        stream_incident_run(body),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/demo", response_model=IncidentRunResponse)
def post_incident_demo() -> IncidentRunResponse:
    """固定示例请求，便于联调."""
    return execute_incident_run(demo_request())


# ─────────────────────────────────────────────────────────────────────────────
# Debate Routes (Stage 1: Ops + SRE × 2 rounds + Report)
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/debate", response_model=DebateRunResponse)
def post_debate_run(body: DebateRunRequest) -> DebateRunResponse:
    """辩论模式：2 Agent（Ops + SRE）× 2 轮 + Report 裁决。

    通过辩论而非线性流水线处理故障诊断，各 Agent 之间可互相质疑与回应。
    默认不启用；需设置 DEBATE_ENABLED=true 或在 body 中传 max_rounds>0。
    """
    from power_aiops.config import get_settings
    from power_aiops.orchestration import run_debate

    settings = get_settings()

    # 构建 incident context
    ctx = build_incident_context(body)

    # 确定辩论参数（优先用 body 中的，缺省用配置）
    max_rounds = body.max_rounds if body.max_rounds is not None else settings.debate_max_rounds
    max_turns = body.max_turns if body.max_turns is not None else settings.debate_max_turns

    # 执行辩论
    result = run_debate(ctx, max_rounds=max_rounds, max_turns=max_turns)

    # 自动入库知识库（Graph RAG + Chroma Vector RAG）
    try:
        from power_aiops.run_incident import _auto_persist_to_knowledge_base
        agent_outputs = {}
        for turn in result.history.get("turns", []):
            if turn.get("agent_id") and turn.get("message", {}).get("content"):
                agent_outputs[turn["agent_id"]] = turn["message"]["content"][:5000]
        if agent_outputs:
            _auto_persist_to_knowledge_base(
                incident_id=ctx.incident_id,
                summary=ctx.summary,
                agent_outputs=agent_outputs,
            )
    except Exception as e:
        import logging
        logging.warning(f"Failed to auto-persist debate result to knowledge base: {e}")

    # 构建响应
    return DebateRunResponse(
        incident_id=result.incident_id,
        trace_id=result.trace_id,
        total_turns=result.total_turns,
        total_rounds=result.total_rounds,
        llm_calls=result.llm_calls,
        conclusion=result.conclusion,
        report_text=result.report_text,
        code_script=result.code_script,
        disputed_points=result.disputed_points,
        turns=[
            DebateTurnItem(
                turn_id=turn.turn_id,
                round=turn.round,
                role=turn.role,
                agent_id=turn.agent_id,
                content_preview=turn.message.content[:500],
                reasoning_preview=turn.message.reasoning[:200],
                msg_type=turn.message.msg_type,
                timestamp=turn.message.timestamp,
                success=turn.success,
                error=turn.error,
            )
            for turn in result.history.get("turns", [])
        ],
        terminated=result.terminated,
        termination_reason=result.termination_reason,
        convergence_score=result.convergence_score,
        human_approved=result.human_approved,
        export_suggestions=["docx", "pdf", "html"],
    )


# 全局辩论会话管理（key: incident_id）
_debate_sessions: dict[str, dict] = {}
_SESSION_TTL_SECONDS = 300  # sessions expire after 5 minutes


def _cleanup_stale_sessions() -> None:
    """Remove sessions that exceed the TTL (prevents memory leak)."""
    now = time.monotonic()
    stale = [
        sid for sid, s in _debate_sessions.items()
        if now - s.get("created_at", now) > _SESSION_TTL_SECONDS
    ]
    for sid in stale:
        _debate_sessions.pop(sid, None)
    if stale:
        import logging
        logging.getLogger(__name__).info("Cleaned up %d stale debate session(s)", len(stale))


@router.post("/debate/stream")
def post_debate_stream(body: DebateRunRequest):
    """流式执行辩论（阶段二），Server-Sent Events 输出每个事件的实时结果。

    支持人类介入暂停机制：
      - 辩论在 R2 收敛失败时 yield pause_request 事件并暂停
      - 前端收到后显示确认按钮，用户确认后 POST /debate/control 放行
    """
    import uuid
    from power_aiops.config import get_settings

    settings = get_settings()

    ctx = build_incident_context(body)
    max_turns = body.max_turns if body.max_turns is not None else settings.debate_max_turns

    session_id = ctx.incident_id or uuid.uuid4().hex[:8]
    pause_event = asyncio.Event()
    pause_event.set()  # 默认已就绪

    # 清理过期会话
    _cleanup_stale_sessions()

    # 注册会话
    _debate_sessions[session_id] = {
        "pause_event": pause_event,
        "approved": False,
        "incident_id": ctx.incident_id,
        "created_at": time.monotonic(),
    }

    async def event_generator():
        from power_aiops.orchestration import DebateOrchestrator

        # 第一条消息：告知前端 session_id（用于 /debate/control）
        yield f"data: {json.dumps({'type': 'session_id', 'session_id': session_id})}\n\n"

        orchestrator = DebateOrchestrator(max_turns=max_turns)
        try:
            async for event in orchestrator.stream_debate(ctx, pause_event=pause_event):
                yield f"data: {json.dumps(event)}\n\n"
        finally:
            _debate_sessions.pop(session_id, None)

    import json
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "X-Session-Id": session_id,
        },
    )


@router.post("/debate/control")
def post_debate_control(
    session_id: str,
    action: str,  # "approve" | "reject"
):
    """人类介入控制端点：放行或拒绝辩论继续执行。

    当前端收到 pause_request 事件后，调用此接口放行。
    """
    _cleanup_stale_sessions()

    if session_id not in _debate_sessions:
        raise HTTPException(status_code=404, detail="辩论会话不存在或已结束")

    session = _debate_sessions[session_id]

    # Check TTL expiry
    now = time.monotonic()
    if now - session.get("created_at", now) > _SESSION_TTL_SECONDS:
        _debate_sessions.pop(session_id, None)
        raise HTTPException(status_code=410, detail="辩论会话已过期")

    pause_event: asyncio.Event = session["pause_event"]

    if action == "approve":
        session["approved"] = True
        pause_event.set()  # 放行，继续辩论
        return {"status": "approved", "message": "已放行，辩论继续"}
    elif action == "reject":
        session["approved"] = False
        pause_event.set()  # 拒绝也设 Event（防止卡死）
        return {"status": "rejected", "message": "已拒绝，辩论终止"}
    else:
        raise HTTPException(status_code=400, detail=f"未知操作: {action}")


@router.post("/debate/export")
def post_debate_export(incident_id: Union[str, None] = None, format: str = "docx", output_path: Union[str, None] = None):
    """
    辩论完成后导出报告。
    
    支持格式：
    - docx: Word 文档
    - pdf: PDF 文档
    
    如果不提供 output_path，将返回文件流供前端下载。
    """
    from power_aiops.export import export_debate_report
    from power_aiops.orchestration.debate import DebateResult

    # 验证 incident_id
    if not incident_id or incident_id in ("undefined", "null", ""):
        raise HTTPException(status_code=400, detail="无效的故障 ID，请重新执行辩论生成有效 ID")

    # 尝试从全局存储获取辩论结果
    result = None

    # 方案1：从 debate_control 的全局存储获取
    if hasattr(post_debate_control, '_last_result'):
        result = post_debate_control._last_result.get(incident_id)

    # 方案2：直接从 SharedBoard 获取（如果已存储）
    if not result:
        try:
            from power_aiops.memory.shared_board import SharedBoard
            board = SharedBoard()
            debate_data = board.get(f"debate_result_{incident_id}")
            if debate_data:
                result = DebateResult(**debate_data)
        except Exception:
            pass

    # 如果仍然没有结果，尝试重建
    if not result:
        raise HTTPException(status_code=404, detail=f"未找到故障 {incident_id} 的辩论结果，请重新执行辩论")

    # 导出报告
    format_type = format.lower() if format else "docx"
    if format_type not in ("docx", "pdf"):
        raise HTTPException(status_code=400, detail=f"不支持的格式: {format_type}，支持的格式: docx, pdf")

    try:
        file_bytes = export_debate_report(result, format=format_type)

        if output_path:
            # 保存到指定路径
            with open(output_path, 'wb') as f:
                f.write(file_bytes)
            return {
                "success": True,
                "incident_id": incident_id,
                "format": format_type,
                "path": output_path,
                "message": f"报告已导出到: {output_path}",
                "content_type": f"application/{'pdf' if format_type == 'pdf' else 'vnd.openxmlformats-officedocument.wordprocessingml.document'}",
            }
        else:
            # 返回文件供下载
            content_type = "application/pdf" if format_type == "pdf" else "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            filename = f"aiops_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.{format_type}"

            return Response(
                content=file_bytes,
                media_type=content_type,
                headers={
                    "Content-Disposition": f"attachment; filename*=UTF-8''{filename}",
                },
            )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"导出异常: {str(e)}")


@router.post("/run/export")
def post_pipeline_export(incident_id: Union[str, None] = None, format: str = "docx", output_path: Union[str, None] = None):
    """
    Pipeline 完成后导出报告。

    支持格式：
    - docx: Word 文档
    - pdf: PDF 文档

    如果不提供 output_path，将返回文件流供前端下载。
    """
    try:
        from power_aiops.memory.shared_board import SharedBoard
        from power_aiops.export import export_debate_report
        from power_aiops.orchestration.debate import DebateResult

        # 验证 incident_id
        if not incident_id or incident_id in ("undefined", "null", ""):
            raise HTTPException(status_code=400, detail="无效的故障 ID，请重新执行 Pipeline 生成有效 ID")

        # 从 SharedBoard 获取 Pipeline 结果
        board = SharedBoard()
        pipeline_data = board.get(f"pipeline_result_{incident_id}")

        if not pipeline_data:
            raise HTTPException(status_code=404, detail=f"未找到故障 {incident_id} 的 Pipeline 结果（数据可能未持久化），请重新执行 Pipeline")

        # 将 Pipeline 结果转换为 DebateResult 格式以复用导出器
        # 构造 agent_outputs 格式
        agent_outputs = pipeline_data.get("agent_outputs", {})
        report_content = pipeline_data.get("report_content", "")
        summary = pipeline_data.get("summary", "")

        # 构建 history（模拟辩论历史格式）
        turns = []
        for i, (agent_id_item, output) in enumerate(agent_outputs.items()):
            turn = {
                "turn_id": i,
                "round": f"Step {i+1}",
                "role": agent_id_item.replace("-01", "").lower(),
                "agent_id": agent_id_item,
                "message": {
                    "content": output[:500] if len(output) > 500 else output,
                }
            }
            turns.append(turn)

        # 创建 DebateResult 格式的数据
        debate_result_dict = {
            "incident_id": incident_id,
            "trace_id": pipeline_data.get("trace_id", ""),
            "total_turns": len(agent_outputs),
            "total_rounds": len(agent_outputs),
            "conclusion": report_content[:500] if report_content else summary,
            "report_text": report_content,
            "code_script": agent_outputs.get("Code-Agent-01", "") + agent_outputs.get("DynamicCode-Agent-01", ""),
            "disputed_points": [],
            "convergence_score": 1.0,
            "history": {"turns": turns},
            "terminated": True,
            "termination_reason": "pipeline_completed",
        }

        result = DebateResult(**debate_result_dict)

        # 导出报告
        format_type = format.lower() if format else "docx"
        if format_type not in ("docx", "pdf"):
            raise HTTPException(status_code=400, detail=f"不支持的格式: {format_type}，支持的格式: docx, pdf")

        file_bytes = export_debate_report(result, format=format_type)

        if output_path:
            with open(output_path, 'wb') as f:
                f.write(file_bytes)
            return {
                "success": True,
                "incident_id": incident_id,
                "format": format_type,
                "path": output_path,
                "message": f"报告已导出到: {output_path}",
                "content_type": f"application/{'pdf' if format_type == 'pdf' else 'vnd.openxmlformats-officedocument.wordprocessingml.document'}",
            }
        else:
            content_type = "application/pdf" if format_type == "pdf" else "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            filename = f"aiops_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.{format_type}"

            return Response(
                content=file_bytes,
                media_type=content_type,
                headers={
                    "Content-Disposition": f"attachment; filename*=UTF-8''{filename}",
                },
            )
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"导出异常: {str(e)}")


# ─────────────────────────────────────────────────────────────────────────────
# Knowledge Base (Graph RAG) Routes
# ─────────────────────────────────────────────────────────────────────────────

def _get_graph_rag() -> GraphRAG:
    """Get or create GraphRAG instance."""
    return GraphRAG()


@router.get("/knowledge/stats", response_model=KnowledgeBaseStats)
def get_knowledge_stats() -> KnowledgeBaseStats:
    """获取知识库统计信息."""
    rag = _get_graph_rag()
    try:
        stats = rag.get_stats()
        return KnowledgeBaseStats(
            total_cases=stats.get("total_cases", 0),
            total_symptoms=stats.get("total_symptoms", 0),
            total_root_causes=stats.get("total_root_causes", 0),
            total_services=stats.get("total_services", 0),
        )
    finally:
        rag.close()


@router.get("/knowledge/cases", response_model=list[FaultCaseResponse])
def list_cases(
    limit: int = 50,
    severity: str | None = None,
) -> list[FaultCaseResponse]:
    """列出故障案例."""
    from neo4j import GraphDatabase
    from power_aiops.config import get_settings

    settings = get_settings()
    driver = GraphDatabase.driver(
        settings.neo4j_uri,
        auth=(settings.neo4j_user, settings.neo4j_password),
    )

    try:
        with driver.session(database="neo4j") as session:
            if severity:
                result = session.run("""
                    MATCH (c:FaultCase {severity: $severity})
                    RETURN c ORDER BY c.created_at DESC LIMIT $limit
                """, severity=severity, limit=limit)
            else:
                result = session.run("""
                    MATCH (c:FaultCase)
                    RETURN c ORDER BY c.created_at DESC LIMIT $limit
                """, limit=limit)

            cases = []
            for record in result:
                c = record["c"]
                cases.append(FaultCaseResponse(
                    case_id=c.get("case_id", ""),
                    title=c.get("title", ""),
                    summary=c.get("summary", ""),
                    symptoms=[],
                    services=[],
                    hosts=[],
                    root_cause="",
                    resolution="",
                    severity=c.get("severity", "P2"),
                    duration_minutes=c.get("duration_minutes", 0),
                    created_at=str(c.get("created_at", "")),
                    tags=c.get("tags", []),
                ))
            return cases
    finally:
        driver.close()


@router.get("/knowledge/cases/{case_id}", response_model=FaultCaseResponse)
def get_case(case_id: str) -> FaultCaseResponse:
    """获取指定案例详情."""
    rag = _get_graph_rag()
    try:
        case = rag.get_case_details(case_id)
        if not case:
            raise HTTPException(status_code=404, detail=f"Case {case_id} not found")
        return FaultCaseResponse(
            case_id=case["case_id"],
            title=case["title"],
            summary=case["summary"],
            symptoms=case["symptoms"],
            services=case["services"],
            hosts=case["hosts"],
            root_cause=case["root_cause"],
            resolution=case["resolution"],
            severity=case["severity"],
            duration_minutes=case["duration_minutes"],
            created_at=case["created_at"],
            tags=[],  # Not stored in detail query
        )
    finally:
        rag.close()


@router.post("/knowledge/cases", response_model=FaultCaseResponse, status_code=201)
def create_case(body: FaultCaseCreate) -> FaultCaseResponse:
    """创建新的故障案例."""
    rag = _get_graph_rag()
    try:
        rag.store_case_dict({
            "case_id": body.case_id,
            "title": body.title,
            "summary": body.summary,
            "symptoms": body.symptoms,
            "services": body.services,
            "hosts": body.hosts,
            "root_cause": body.root_cause,
            "resolution": body.resolution,
            "severity": body.severity,
            "duration_minutes": body.duration_minutes,
            "tags": body.tags,
            "metadata": body.metadata,
        })
        return FaultCaseResponse(
            case_id=body.case_id,
            title=body.title,
            summary=body.summary,
            symptoms=body.symptoms,
            services=body.services,
            hosts=body.hosts,
            root_cause=body.root_cause,
            resolution=body.resolution,
            severity=body.severity,
            duration_minutes=body.duration_minutes,
            created_at=datetime.now(timezone.utc).isoformat(),
            tags=body.tags,
        )
    finally:
        rag.close()


@router.put("/knowledge/cases/{case_id}", response_model=FaultCaseResponse)
def update_case(case_id: str, body: FaultCaseUpdate) -> FaultCaseResponse:
    """更新故障案例."""
    from neo4j import GraphDatabase
    from power_aiops.config import get_settings

    settings = get_settings()
    driver = GraphDatabase.driver(
        settings.neo4j_uri,
        auth=(settings.neo4j_user, settings.neo4j_password),
    )

    try:
        with driver.session(database="neo4j") as session:
            # Check if case exists
            result = session.run("""
                MATCH (c:FaultCase {case_id: $case_id})
                RETURN c
            """, case_id=case_id)

            if not result.single():
                raise HTTPException(status_code=404, detail=f"Case {case_id} not found")

            # Build update query dynamically
            updates = []
            params: dict[str, Any] = {"case_id": case_id}

            if body.title is not None:
                updates.append("c.title = $title")
                params["title"] = body.title
            if body.summary is not None:
                updates.append("c.summary = $summary")
                params["summary"] = body.summary
            if body.severity is not None:
                updates.append("c.severity = $severity")
                params["severity"] = body.severity
            if body.duration_minutes is not None:
                updates.append("c.duration_minutes = $duration_minutes")
                params["duration_minutes"] = body.duration_minutes
            if body.root_cause is not None:
                updates.append("c.root_cause = $root_cause")
                params["root_cause"] = body.root_cause
            if body.resolution is not None:
                updates.append("c.resolution = $resolution")
                params["resolution"] = body.resolution

            if updates:
                session.run(f"""
                    MATCH (c:FaultCase {{case_id: $case_id}})
                    SET {', '.join(updates)}
                """, **params)

            # Get updated case
            rag = _get_graph_rag()
            case = rag.get_case_details(case_id)
            rag.close()

            return FaultCaseResponse(
                case_id=case["case_id"],
                title=case["title"],
                summary=case["summary"],
                symptoms=case["symptoms"],
                services=case["services"],
                hosts=case["hosts"],
                root_cause=case["root_cause"],
                resolution=case["resolution"],
                severity=case["severity"],
                duration_minutes=case["duration_minutes"],
                created_at=case["created_at"],
                tags=[],
            )
    finally:
        driver.close()


@router.delete("/knowledge/cases/{case_id}", status_code=204)
def delete_case(case_id: str) -> None:
    """删除故障案例."""
    from neo4j import GraphDatabase
    from power_aiops.config import get_settings

    settings = get_settings()
    driver = GraphDatabase.driver(
        settings.neo4j_uri,
        auth=(settings.neo4j_user, settings.neo4j_password),
    )

    try:
        with driver.session(database="neo4j") as session:
            result = session.run("""
                MATCH (c:FaultCase {case_id: $case_id})
                DETACH DELETE c
                RETURN count(c) AS deleted
            """, case_id=case_id)

            record = result.single()
            if record and record["deleted"] == 0:
                raise HTTPException(status_code=404, detail=f"Case {case_id} not found")
    finally:
        driver.close()


@router.get("/knowledge/search", response_model=list[SimilarCaseResponse])
def search_similar_cases(
    query: str,
    top_k: int = 5,
    search_type: str = "symptom",
) -> list[SimilarCaseResponse]:
    """搜索相似的故障案例."""
    rag = _get_graph_rag()
    try:
        if search_type not in ("symptom", "root_cause"):
            search_type = "symptom"

        results = rag.vector_search(query, search_type=search_type, top_k=top_k)

        return [
            SimilarCaseResponse(
                case_id=r.get("case_id", ""),
                title=r.get("title", ""),
                severity=r.get("severity", "P2"),
                matched_symptoms=r.get("matched_symptoms", []),
                resolution=r.get("resolution"),
                similarity_score=r.get("matched_symptoms", [{}])[0].get("score", 0.0),
                created_at=str(r.get("created_at", "")),
            )
            for r in results
        ]
    finally:
        rag.close()


@router.post("/knowledge/initialize", status_code=201)
def initialize_knowledge_base() -> dict[str, str]:
    """初始化知识库 schema（创建索引和约束）。"""
    rag = _get_graph_rag()
    try:
        rag.initialize_schema()
        return {"status": "initialized", "message": "Knowledge base schema initialized"}
    finally:
        rag.close()


# ─────────────────────────────────────────────────────────────────────────────
# Visualization Routes (GPT-Vis)
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/visualization/dashboard/{incident_id}")
def get_incident_dashboard(incident_id: str) -> dict:
    """获取故障诊断仪表盘数据（用于 GPT-Vis 渲染）。

    返回包含以下内容的字典：
    - timeline_chart: Agent 处理时长图（GPT-Vis 语法）
    - metrics_data: 指标趋势数据
    - fault_propagation: 故障传播路径
    """
    from power_aiops.visualization import generate_dashboard_data

    # 从知识库获取案例
    rag = _get_graph_rag()
    try:
        case = rag.get_case_details(incident_id)
        if not case:
            raise HTTPException(status_code=404, detail=f"Case {incident_id} not found")

        # 构建可视化数据
        dashboard_data = generate_dashboard_data(
            incident_id=incident_id,
            agent_outputs={},  # 可以从历史记录获取
        )

        # 添加案例详情
        dashboard_data["case"] = {
            "title": case.get("title", ""),
            "summary": case.get("summary", ""),
            "root_cause": case.get("root_cause", ""),
            "resolution": case.get("resolution", ""),
            "severity": case.get("severity", "P2"),
            "symptoms": case.get("symptoms", []),
            "services": case.get("services", []),
        }

        return dashboard_data

    finally:
        rag.close()


@router.get("/visualization/stats")
def get_visualization_stats() -> dict:
    """获取可视化统计信息."""
    rag = _get_graph_rag()
    try:
        stats = rag.get_stats()

        # 添加链路追踪统计
        stats["total_traces"] = 0
        stats["total_spans"] = 0
        stats["error_spans"] = 0

        # 添加 Chroma Vector RAG 统计
        try:
            from power_aiops.memory.vector_rag import ChromaVectorRAG
            vector_rag = ChromaVectorRAG()
            vector_stats = vector_rag.get_collection_stats()
            stats["vector_rag"] = {
                "documents": vector_stats.get("total_documents", 0),
                "collection_name": vector_stats.get("collection_name", ""),
            }
            vector_rag.close()
        except Exception:
            stats["vector_rag"] = {"documents": 0, "error": "unavailable"}

        return {
            "cases": stats.get("total_cases", 0),
            "symptoms": stats.get("total_symptoms", 0),
            "root_causes": stats.get("total_root_causes", 0),
            "services": stats.get("total_services", 0),
            "traces": stats.get("total_traces", 0),
            "spans": stats.get("total_spans", 0),
            "vector_documents": stats.get("vector_rag", {}).get("documents", 0),
        }
    finally:
        rag.close()


@router.get("/visualization/trace/{trace_id}")
def get_trace_visualization(trace_id: str) -> dict:
    """获取链路追踪可视化数据（GPT-Vis 瀑布图格式）。"""
    from power_aiops.visualization import GPTVisRenderer

    rag = _get_graph_rag()
    try:
        # 获取链路树
        trace_ctx = rag.get_trace_tree(trace_id)
        if not trace_ctx:
            raise HTTPException(status_code=404, detail=f"Trace {trace_id} not found")

        # 生成 GPT-Vis 瀑布图语法
        renderer = GPTVisRenderer()

        # 构建瀑布图数据
        spans_data = []
        for span in trace_ctx.spans:
            spans_data.append({
                "span": span.operation[:30] if span.operation else "unknown",
                "service": span.service,
                "duration": span.duration_ms,
                "status": span.status,
            })

        # 渲染瀑布图
        waterfall_chart = renderer.render_trace_timeline(trace_id, spans_data)

        return {
            "trace_id": trace_id,
            "total_spans": trace_ctx.total_spans,
            "error_spans": trace_ctx.error_spans,
            "duration_ms": trace_ctx.duration_ms,
            "waterfall_chart": waterfall_chart,
            "spans": spans_data,
        }

    finally:
        rag.close()


# ─────────────────────────────────────────────────────────────────────────────
# Report Export Routes (Human-Approved Export)
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/reports/{incident_id}")
def get_report(incident_id: str) -> dict:
    """获取指定故障的报告内容（已生成的报告存储在内存中）。

    报告在 /run 或 /run/stream 接口执行后通过 SSE 的 done 事件返回，
    前端应保存报告内容并在用户选择导出格式后调用 /reports/{incident_id}/export。
    """
    # 报告内容通过 SSE 的 done 事件返回，此处仅作为占位
    # 实际实现中，报告内容应存储到持久化存储（如 Redis/DB）
    return {
        "incident_id": incident_id,
        "message": "报告内容通过 /run 或 /run/stream 接口的 SSE done 事件返回",
        "suggestion": "前端应在 SSE 完成后保存报告内容，调用 /reports/{incident_id}/export 进行导出",
    }


class ReportExportRequest(BaseModel):
    """报告导出请求。"""

    format: str = Field(description="导出格式: docx, pdf, html, none")
    output_path: str | None = Field(default=None, description="输出文件路径（可选）")


class ReportExportResponse(BaseModel):
    """报告导出响应。"""

    success: bool
    incident_id: str
    format: str
    path: str | None = None
    message: str


@router.post("/reports/{incident_id}/export", response_model=ReportExportResponse)
def export_report(
    incident_id: str,
    body: ReportExportRequest,
) -> ReportExportResponse:
    """导出报告为指定格式（人工选择后调用）。

    流程：
    1. 前端调用 /run 或 /run/stream 执行故障处理 pipeline
    2. 报告内容通过 SSE 的 done 事件返回，前端保存报告文本
    3. 用户选择导出格式（docx/pdf/html/none）
    4. 前端调用此接口进行导出

    导出格式说明：
    - docx: Word 文档（需要 python-docx 库）
    - pdf: PDF 文档（需要 reportlab 库）
    - html: HTML 格式（无需额外依赖）
    - none: 不导出任何文件，返回确认信息
    """
    from power_aiops.agents.report import ReportAgent
    from power_aiops.agents.tools import get_tool_registry

    format_type = body.format.lower() if body.format else "none"

    # 如果选择不导出
    if format_type == "none":
        return ReportExportResponse(
            success=True,
            incident_id=incident_id,
            format="none",
            path=None,
            message="用户选择不导出文件，报告内容已在前端显示",
        )

    # 获取工具注册表
    registry = get_tool_registry()

    # 从 SharedBoard 获取报告内容
    from power_aiops.memory.shared_board import SharedBoard
    board = SharedBoard()
    report_content = board.get("report_output", "")

    if not report_content:
        return ReportExportResponse(
            success=False,
            incident_id=incident_id,
            format=format_type,
            path=None,
            message="报告中无内容可导出，请先执行 /run 或 /run/stream 接口生成报告",
        )

    # 创建 ReportAgent（传入共享 board）
    agent = ReportAgent(
        board=board,
        tool_registry=registry,
        default_export_dir="reports",
    )
    agent._board.set("incident_id", incident_id)

    # 执行导出
    if format_type not in ("docx", "pdf", "html"):
        return ReportExportResponse(
            success=False,
            incident_id=incident_id,
            format=format_type,
            path=None,
            message=f"不支持的导出格式: {format_type}，支持: docx, pdf, html, none",
        )

    result = agent.export_report(format=format_type, output_path=body.output_path)

    if result.get("success"):
        return ReportExportResponse(
            success=True,
            incident_id=incident_id,
            format=format_type,
            path=result.get("path"),
            message=f"报告已导出为 {format_type} 格式: {result.get('path')}",
        )
    else:
        return ReportExportResponse(
            success=False,
            incident_id=incident_id,
            format=format_type,
            path=None,
            message=f"导出失败: {result.get('error', '未知错误')}",
        )
