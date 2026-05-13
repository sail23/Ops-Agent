# power-aiops-agents

面向**电力数据中心运维**的多智能体（Ops / SRE / Code / Report）协作骨架：统一事件与消息模型、共享记忆与安全围栏，后续将接入编排引擎与 HTTP API。业务与实现顺序以 [DEVELOPMENT_PLAN.md](./DEVELOPMENT_PLAN.md) 为准。

---

## 仓库结构（当前）

```
agents-/
├── DEVELOPMENT_PLAN.md      # Cursor 顺序开发计划
├── pyproject.toml           # 项目元数据与依赖
├── setup.py                 # 兼容旧版 pip 的可编辑安装
├── .env.example             # 环境变量示例（复制为 .env）
├── README.md
├── src/
│   └── power_aiops/
│       ├── __init__.py      # 包版本
│       ├── __main__.py      # CLI 入口（power-aiops / python -m power_aiops）
│       ├── config.py        # pydantic-settings（API Key、服务端口等）
│       ├── models/          # EventObject、AgentMessage、IncidentContext
│       ├── memory/          # 短期记忆、共享黑板、长期记忆桩
│       ├── prompts/         # 五步思维链模板、四角色 system 提示片段
│       ├── agents/          # BaseAgent、Ops/SRE/Code/Report 占位实现
│       ├── orchestration/   # run_pipeline、PipelineState、BOARD_KEY_*
│       ├── api/             # FastAPI app、routes、请求/响应模型
│       ├── cli.py           # 命令行入口
│       ├── run_incident.py  # execute_incident_run（API 与 CLI 共用）
│       ├── integrations/    # Prometheus / ELK 接入桩与映射示例
│       ├── llm/               # OpenAI 兼容 Chat 客户端（可选）
│       └── security/        # 高危命令围栏、角色权限矩阵
└── tests/
    └── …                    # 含 integrations、api、cli 等
```

以下能力**尚未实现**：真实监控拉数（桩仅返回空列表）；详见 `integrations/` 内文档字符串。

---

## 模块职责（简表）

| 路径 | 职责 |
|------|------|
| `models/events.py` | 多源告警归一为 `EventObject`（时间戳、设备 ID、指标、原始报文等） |
| `models/messages.py` | Agent 间通信信封（`Msg_id`、`Trace_id`、`Priority`、`Payload` 等） |
| `models/incident.py` | 单次故障上下文 `IncidentContext`（事件列表、共享笔记） |
| `memory/short_term.py` | 滑动窗口对话（默认 20 轮，可接摘要） |
| `memory/shared_board.py` | 全局共享黑板（线程安全键值） |
| `memory/long_term.py` | 向量/案例检索接口桩 `StubLongTermMemory` |
| `memory/message_log.py` | `MessageLog` 协议、`InMemoryMessageLog`；`run_pipeline(..., message_log=)` 可选 |
| `models/messages.py` | `agent_message_to_json_dict` / `agent_message_from_json_dict` |
| `security/fences.py` | 文本后处理：拦截 `rm -rf`、`drop table` 等高危模式 |
| `security/permissions.py` | 四类 Agent 与权限枚举（与文档中的角色模型对齐） |
| `config.py` | 从环境变量读取配置（见 `.env.example`） |
| `prompts/cot_steps.py` | 五步思维链模板与 `format_cot_prompt()` |
| `prompts/roles.py` | Ops / SRE / Code / Report 的 system 提示常量 |
| `agents/base.py` | `BaseAgent`、`AgentResult`；子类实现 `run(IncidentContext)` |
| `agents/code.py` | 占位脚本草案 + `fence_check_text`；`metadata["code_draft"]` 可注入测试 |
| `agents/ops.py` | `OpsAgent(use_llm=False)` 默认；`use_llm=True` 时走 `OpenAICompatibleClient`（无 Key 仍为 stub） |
| `llm/client.py` | `OpenAICompatibleClient.chat` → `POST .../v1/chat/completions` |
| `orchestration/pipeline.py` | `run_pipeline(ctx, board=, memory=)`，顺序四 Agent，写入共享黑板与短期记忆 |
| `api/app.py` | `app` 与 `create_app()`，挂载 `/incidents`、`/health` |
| `api/schemas.py` | `IncidentRunRequest` / `IncidentRunResponse`、`build_incident_context` |
| `run_incident.py` | `execute_incident_run`、`demo_request` |
| `cli.py` | `power-aiops run --demo` 或 `run --json <path>` |
| `integrations/prometheus.py` | `fetch_prometheus_events_stub`、`PrometheusClientConfig`、`map_prometheus_sample_to_event` |
| `integrations/elk.py` | `fetch_elk_events_stub`、`ElkClientConfig`、`map_elk_hit_to_event` |

---

## 环境要求

- **Python 3.10+**（`pyproject.toml` 中 `requires-python`）。若系统默认是 3.9，请使用 `py -3.10` / `py -3.11` / `py -3.14` 创建虚拟环境。
- 安装依赖时需能访问 **PyPI**，或使用国内镜像（见下文）。

---

## 安装

```powershell
cd C:\Users\cfyzy\Desktop\agents-
py -3.10 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -U pip setuptools wheel
pip install -e ".[dev]"
```

开发依赖（`[dev]`）包含：`pytest`、`ruff`。

---

## 运行方式

### CLI（已可用）

与 **`POST /incidents/run` 使用同一套逻辑**（`execute_incident_run`，见 `run_incident.py`）。

```powershell
power-aiops --version
power-aiops run --demo
power-aiops run --demo --pretty
power-aiops run --json tests\fixtures\sample_incident.json
python -m power_aiops run --demo
```

- `--demo`：与 `POST /incidents/demo` 相同示例。
- `--json PATH`：文件内容为 `IncidentRunRequest` 形状（与 API 请求体一致），标准输出为一行 JSON（可用 `--pretty` 格式化）。
- `--demo` 与 `--json` 互斥。

### HTTP API（FastAPI）

依赖已包含 `uvicorn`。在项目根目录、激活虚拟环境后：

```powershell
uvicorn power_aiops.api.app:app --reload --host 0.0.0.0 --port 8000
```

- 浏览器打开 [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs) 查看 OpenAPI。
- `GET /health` 健康检查。
- `POST /incidents/demo` 固定示例，跑通编排 pipeline。
- `POST /incidents/run` 请求体为 JSON（`device_id`/`metric_type` 简写或 `events` 数组、`metadata` 含 `code_draft` 等），返回 `shared_board`、`code_blocked`、`fence_matched` 等。

示例：

```powershell
curl -s -X POST http://127.0.0.1:8000/incidents/demo
curl -s -X POST http://127.0.0.1:8000/incidents/run -H "Content-Type: application/json" -d "{\"device_id\":\"h1\",\"metric_type\":\"cpu\"}"
```

---

## 配置（可选）

```powershell
copy .env.example .env
```

| 变量 | 说明 |
|------|------|
| `OPENAI_API_BASE` | 留空则使用 `https://api.openai.com/v1`；兼容自建网关时填完整 v1 前缀 |
| `OPENAI_API_KEY` | 留空则 LLM 走占位文本，不发起外呼 |
| `OPENAI_CHAT_MODEL` | 如 `gpt-4o-mini` |
| `OPENAI_TIMEOUT_SECONDS` | HTTP 超时（秒） |

`OpsAgent(use_llm=True)` 会调用 `OpenAICompatibleClient`；默认 `use_llm=False`，编排 pipeline 行为与此前一致。

---

## 验证与质量

```powershell
python -c "import power_aiops; print(power_aiops.__version__)"
pytest -q
ruff check src tests
```

编排冒烟（需已 `pip install -e .`）：

```powershell
python -c "from power_aiops.models import IncidentContext; from power_aiops.orchestration import run_pipeline; s=run_pipeline(IncidentContext(incident_id='x', trace_id='t')); print(s.code_blocked, list(s.agent_outputs.keys()))"
```

---

## 故障排查（Windows）

### 1. `pip install` SSL 证书或连接失败

- 检查公司代理/VPN；必要时配置环境变量 `HTTP_PROXY` / `HTTPS_PROXY`。
- 临时使用国内 PyPI 镜像（示例，请按需替换为贵司允许源）：

  ```powershell
  pip install -e ".[dev]" -i https://pypi.tuna.tsinghua.edu.cn/simple
  ```

### 2. 提示缺少 `setup.py` 或无法可编辑安装

先升级 **pip**（见「安装」一节）。仓库根目录已包含最小 `setup.py`，仅作兼容；构建仍以 `pyproject.toml` 为准。

### 3. 无法执行脚本：`power-aiops` 不是内部或外部命令

确认已激活虚拟环境，且 `pip install -e ".[dev]"` 成功；或使用 `python -m power_aiops`。

### 4. PowerShell 禁止运行脚本

若激活 venv 报错，可临时执行：

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

---

## 后续里程碑（未在仓库中实现）

- **图 RAG / 向量库**：长期记忆与案例检索落地。
- **K8s / 容器沙箱**：Code-Agent 隔离执行与预演。
- **人工审批流**：生产变更前人工确认与审计闭环。

详见 [DEVELOPMENT_PLAN.md](./DEVELOPMENT_PLAN.md) 阶段 D、E。

---

## 许可证与贡献

（待项目方补充许可证。）开发请跟随 `DEVELOPMENT_PLAN.md` 中的步骤顺序，便于在 Cursor 中逐步执行。
