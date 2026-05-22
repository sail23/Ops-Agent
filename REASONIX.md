# power-aiops-agents — Reasonix memory

## Stack

- **Language** — Python >=3.10
- **Web** — FastAPI + uvicorn
- **Config** — pydantic + pydantic-settings (`.env` read at runtime)
- **LLM client** — httpx-based OpenAI-compatible chat client (optional; stub works without)
- **Vector store** — chromadb (auto-created, gitignored)
- **Reporting** — python-docx, reportlab (Word/PDF export)

## Layout

| Path | Contents |
|---|---|
| `src/power_aiops/` | Package root |
| `…/agents/` | `BaseAgent(ABC)` + role agents: Ops/SRE/Code/Report |
| `…/api/` | FastAPI app, routes, request/response schemas |
| `…/memory/` | Short-term, shared-board, long-term, vector RAG, graph RAG |
| `…/models/` | `EventObject`, `AgentMessage`, `IncidentContext` (pydantic) |
| `…/orchestration/` | Pipeline state machine + debate multi-agent flow |
| `…/prompts/` | Chain-of-thought step templates + role system prompts |
| `…/security/` | High-risk command fence + role permission matrix |
| `…/integrations/` | Prometheus / OpenRCA stub connectors |
| `…/llm/` | OpenAI-compatible chat + embedding clients |
| `…/cli.py` | CLI entry point (registered as `power-aiops`) |
| `…/config.py` | Pydantic-settings (reads `.env`) |
| `tests/` | pytest test files |

## Commands

| Action | Command |
|---|---|
| Install (editable) | `pip install -e .` |
| Install (dev deps) | `pip install -e '.[dev]'` |
| Run tests | `pytest -v` |
| Lint check | `ruff check .` |
| Format | `ruff format .` |
| CLI entry | `power-aiops` (or `python -m power_aiops`) |
| Dev server | `uvicorn power_aiops.api.app:app --reload` |

## Conventions

- **ruff** line-length=100, target-version py310
- **src-layout** — `package-dir={"":"src"}` for setuptools discovery
- **Tests** — `tests/test_*.py` with `pythonpath = ["src"]` in pytest config
- **Agents** — extend `BaseAgent(ABC)`, implement `run(IncidentContext) -> AgentResult`
- **Export builders** — `report_exporter.py` generates `.docx` (python-docx) and `.pdf` (reportlab)

## Watch out for

- **`.env` is gitignored** — copy `.env.example` to `.env` and fill in API keys
- **Vector store** — `chroma_data/` auto-created at first run, gitignored; delete to reset
- **Integration stubs** — `integrations/prometheus.py` and `integrations/openrca.py` return empty lists (not wired to real monitoring)
- **Agent LLM stubs** — `BaseAgent` runs B2 skeleton logic unless `OPENAI_API_KEY` is set; agents fall back to stubs gracefully
- **Package name** — the Python distribution is `power-aiops-agents`; the import package is `power_aiops`
