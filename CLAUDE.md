# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

`power-aiops-agents` — a multi-agent AIOps skeleton for power data center operations. Four specialized agents (Ops, SRE, Code, Report) collaborate through either a linear pipeline or a dynamic debate orchestrator to diagnose and resolve incidents.

## Build, test, lint

```powershell
# Editable install
pip install -e ".[dev]"

# Run all tests
pytest -q

# Run a single test file
pytest tests/test_pipeline.py -q

# Lint
ruff check src tests

# Verify import
python -c "import power_aiops; print(power_aiops.__version__)"
```

## Run

```powershell
# CLI (same logic as POST /incidents/run)
power-aiops run --demo
power-aiops run --demo --pretty
power-aiops run --json tests\fixtures\sample_incident.json

# HTTP API
uvicorn power_aiops.api.app:app --reload --host 0.0.0.0 --port 8000
# Then open http://127.0.0.1:8000/docs

# Pipeline smoke test (one-liner)
python -c "from power_aiops.models import IncidentContext; from power_aiops.orchestration import run_pipeline; s=run_pipeline(IncidentContext(incident_id='x', trace_id='t')); print(s.code_blocked, list(s.agent_outputs.keys()))"
```

## Architecture

### Domain model (`models/`)

- **`EventObject`** — normalized alert/event from any source (Prometheus, ELK, Zabbix, manual). Has `timestamp`, `device_id`, `metric_type`, `value`, `raw_payload`, `source`.
- **`AgentMessage`** — inter-agent envelope with `sender`, `receiver` (both `AgentRef`), `priority`, `trace_id`, `payload`. JSON-serializable via `agent_message_to_json_dict` / `agent_message_from_json_dict`.
- **`IncidentContext`** — the central context object passed to every agent. Starts with `events[]` + `summary`, gets progressively filled by pipeline agents (`title`, `symptoms`, `root_cause`, `resolution`, `severity` via `update_from_pipeline()`).

### Agent interface (`agents/base.py`)

All agents extend `BaseAgent`:
- `agent_id` (property) — unique string identifier
- `run(ctx: IncidentContext) -> AgentResult` — synchronous execution, returns `AgentResult(content, blocked, fence_matched, meta)`
- `stream_run(ctx) -> AsyncGenerator[AgentStreamChunk]` — async streaming with token-level deltas; default delegates to `run()`, subclasses override for real streaming

### Two orchestration modes

**1. Linear pipeline** (`orchestration/pipeline.py`) — default, always available:
- `run_pipeline(ctx)` → fixed Ops → SRE → Code → Report sequence
- Each step writes to `SharedBoard` and `ShortTermMemory`
- Code output passes through `fence_check_text()`; if blocked, `state.code_blocked = True` but Report still runs
- SREAgent receives `LongTermMemory` for hybrid RAG retrieval
- Code step uses `DynamicCodeAgent` (not the simpler `CodeAgent`)
- `stream_pipeline(ctx)` → SSE-compatible async generator

**2. Debate orchestrator** (`orchestration/debate_orchestrator.py`) — opt-in via config `DEBATE_ENABLED=true`:
- Dynamic turn routing: agents emit `next_turn` hints, orchestrator decides next speaker
- Ops → SRE → Code initial statements, then review rounds, convergence check, Report verdict
- Supports dispute pauses for human approval via `/debate/control`
- Uses `DebateAgentWrapper` which wraps LLM calls; CODE role delegates to `DynamicCodeAgent`

### Memory system

- **`ShortTermMemory`** — sliding window deque (default 20 turns) of `Turn(agent_id, content, at)`
- **`SharedBoard`** — singleton thread-safe key-value store with JSON file persistence. Pipeline uses fixed keys (`BOARD_KEY_OPS`, `BOARD_KEY_SRE`, `BOARD_KEY_CODE`, `BOARD_KEY_REPORT`, `BOARD_KEY_CODE_BLOCKED`). Debate uses `DebateBoard` wrapper.
- **`LongTermMemory`** — hybrid Graph RAG + Vector RAG facade. `hybrid_search()` uses Reciprocal Rank Fusion to combine results.

### Hybrid RAG knowledge base

**Graph RAG** (`memory/graph_rag.py`): Neo4j graph with nodes `FaultCase`, `Symptom`, `Service`, `Host`, `RootCause`, `Resolution`, `Trace`, `Span`. Vector indexes on Symptom/embedding and RootCause/embeddings via Neo4j 5.x native vector index. Embeddings via Zhipu AI (`embedding-3`, 256d) with SHA-256 hash fallback.

**Vector RAG** (`memory/vector_rag.py`): Chroma persistent client, collection `incident_cases`. Same embedding pipeline (Zhipu → hash fallback). `IncidentDocument` dataclass for storage.

**Auto-persistence**: `_auto_persist_to_knowledge_base()` in `run_incident.py` uses LLM extraction (with rule-based fallback) to pull symptoms/root_cause/resolution from agent outputs and persists to both Neo4j and Chroma after every pipeline/debate run.

### Dynamic Code Agent (`agents/dynamic_code.py`)

The Code step always uses `DynamicCodeAgent` (alias `RCAgent`), not the simpler `CodeAgent`:
- Generates Python analysis code (LLM or fallback templates) that queries Neo4j for traces, error spans, slow spans
- Code passes through `fence_check_text()` **and** `_sanitize_code()` (dangerous pattern regex)
- Execution is **disabled by default** (`code_execution_enabled: False` in config); when enabled, runs in subprocess with timeout in a temp file
- Fallback code template is extensive (~80 lines) and self-contained

### API (`api/`)

FastAPI app at `api/app.py`. Routes registered at both `/incidents` and `/api/v1`:
- `POST /incidents/run` — execute pipeline
- `POST /incidents/run/stream` — SSE streaming pipeline
- `POST /incidents/demo` — fixed demo
- `POST /incidents/debate` — debate mode
- `POST /incidents/debate/stream` — SSE streaming debate
- `POST /incidents/debate/control` — human approval for dispute pauses
- `POST /incidents/debate/export`, `POST /incidents/run/export` — report export (docx/pdf)
- `GET/POST /knowledge/*` — CRUD for fault case knowledge base
- `/reports/{incident_id}/export` — human-approved report export (docx/pdf/html)

### Security (`security/`)

- **`fences.py`** — `fence_check_text(text)` scans against ~70 regex patterns covering: file system ops, SQL injection/dangerous statements, network attacks, path traversal, sensitive file access, system modification, code execution bypasses
- **`permissions.py`** — role matrix defined but not enforced at runtime yet

### Configuration (`config.py`)

Pydantic-settings from `.env`. Key non-obvious settings:
- `openai_chat_model` defaults to `deepseek-chat` (not gpt-4o-mini)
- `openai_timeout_seconds` defaults to 300
- `zhipu_embedding_dim` defaults to 256
- `debate_enabled` / `debate_max_rounds` / `debate_max_turns` control debate mode
- `code_execution_enabled` / `code_execution_timeout` control DynamicCodeAgent sandbox

### LLM layer (`llm/`)

- `OpenAICompatibleClient` — chat + streaming via `/v1/chat/completions`. Gracefully falls back to stub text when no API key configured.
- `ZhipuEmbeddingClient` — embedding API, used by both Graph RAG and Vector RAG.

### CLI entry point (`cli.py` + `run_incident.py`)

`run_incident.execute_incident_run()` is the shared entry for both CLI and API. CLI uses `argparse` with `run --demo` and `run --json <path>` subcommands.

### Integrations (`integrations/`)

Stubs returning empty lists for Prometheus and ELK. OpenRCA dataset client is fully implemented. Production implementations would call real monitoring APIs and map results to `EventObject`.

### Export (`export/`)

`report_exporter.py` generates docx (via python-docx) and PDF (via reportlab) from debate/pipeline results.

## Key patterns

- `SharedBoard` is a **singleton** (via `__new__`) — all agents in one process share the same board data. The `DebateOrchestrator` creates its own separate `SharedBoard` for CODE role.
- `LongTermMemory` is initialized inside `run_pipeline()` / `stream_pipeline()` when not provided, and passed to SREAgent only.
- Pipeline and debate results are persisted to `SharedBoard` under keys `pipeline_result_{incident_id}` and `debate_result_{incident_id}` for later export.
- Agent outputs are automatically persisted to knowledge base (Neo4j + Chroma) after pipeline/debate runs via LLM extraction.
- The `DynamicCodeAgent` fallback code template hardcodes Neo4j connection details — it depends on `get_settings()` at runtime.
