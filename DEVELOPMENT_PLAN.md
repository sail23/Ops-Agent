# Cursor 顺序开发计划（电力多智能体 AIOps 骨架）

本文档按**依赖顺序**排列任务。在 Cursor 中建议：**一次只让 AI 做「当前步骤」**，完成并自测后再做下一步。每步末尾有可复制的提示语，粘贴到新对话即可继续。

---

## 使用方式

1. 打开本文件，找到「当前步骤」。
2. 将对应 **Cursor 提示语** 整段复制到 Cursor Chat（可附上 `@DEVELOPMENT_PLAN.md`）。
3. 完成后在本文件勾选或打勾记录（可选），再执行下一步。

**环境前提**：Python **3.10+**（`pyproject.toml` 的 `requires-python`），仓库根目录 `agents-`。若系统默认是 3.9，请用 `py -3.10` 或 `py -3.14` 创建虚拟环境。

```powershell
cd C:\Users\cfyzy\Desktop\agents-
py -3.10 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -U pip setuptools wheel
pip install -e ".[dev]"
```

旧版 pip 若提示缺少 `setup.py`，仓库根目录已提供最小 `setup.py`；**务必先升级 pip** 再装依赖。

---

## 进度总览

| 阶段 | 说明 | 状态 |
|------|------|------|
| A | 工程基线与可安装包 | **已完成**（A1 安装/测试；A2 README 目录树、模块表、排障与里程碑） |
| B | 领域内核（Agent、Prompt、编排） | **已完成**（B1/B2/B3） |
| C | HTTP API 与 CLI | **已完成**（C1 API；C2 CLI） |
| D | 集成桩与持久化接口 | **已完成**（D1/D2/D3） |
| E | 质量与文档 | 待做 |

---

## 阶段 A — 工程基线

### 步骤 A1：确认可编辑安装与测试运行 ✅

**目标**：`pip install -e .` 成功；`pytest` 能跑。

**已落实**：`requires-python >=3.10`；`[tool.setuptools] package-dir`；`setuptools>=64`；根目录 `README.md`；`setup.py`（兼容旧 pip）；`src/power_aiops/__main__.py`（`power-aiops` / `python -m power_aiops`）；`tests/test_smoke.py`。

**验证**：

```powershell
py -3.10 -m pip install -U pip setuptools wheel
pip install -e ".[dev]"
pytest -q
python -c "import power_aiops; print(power_aiops.__version__)"
power-aiops --version
```

**Cursor 提示语**：

```
@DEVELOPMENT_PLAN.md 执行步骤 A1：检查 pyproject.toml 与 src/power_aiops 包布局，
确保在 Windows 下可 pip install -e ".[dev]"，并修复发现的问题。
```

---

### 步骤 A2：补齐 `README.md`（安装、目录说明、运行方式）✅

**目标**：新人 5 分钟内能装依赖并知道代码在哪。

**已落实**：完整仓库树、模块职责表、CLI/API 状态说明（API 标注为 C1 未实现）、`.env` 说明、`pytest`/`ruff`、Windows 排障（SSL/镜像、setup.py、PATH、ExecutionPolicy）、后续里程碑。

**交付**：根目录 `README.md`（中文为主）：项目简介、目录树说明、安装、如何跑 API/CLI（若尚未实现写「即将在步骤 C1/C2 提供」）。

**Cursor 提示语**：

```
@DEVELOPMENT_PLAN.md 执行步骤 A2：创建 README.md，与当前仓库 src/power_aiops 结构一致，
写清安装步骤与模块职责，不要编造尚未实现的命令；对未实现部分标注「见开发计划 C 阶段」。
```

---

## 阶段 B — 领域内核

### 步骤 B1：`prompts/` 运维五步思维链与角色片段 ✅

**目标**：`src/power_aiops/prompts/` 下可导入的常量或函数，包含文档中的 Step1–5 模板与四类 Agent 角色 system 片段（可先纯字符串，不接 LLM）。

**已落实**：`cot_steps.py`（单步模板、`format_cot_prompt`、`cot_step_labels`）、`roles.py`（四角色 system 文本 + `all_role_system_prompts`）、`tests/test_prompts.py`。

**交付**：`prompts/cot_steps.py`、`prompts/roles.py`、`prompts/__init__.py`。

**验证**：单元测试或简单 `python -c` 能 import 并打印模板片段。

**Cursor 提示语**：

```
@DEVELOPMENT_PLAN.md 执行步骤 B1：新增 src/power_aiops/prompts/，
实现运维五步思维链（现象确认→影响→根因→主/回滚方案→风险自检）模板字符串，
以及 Ops/SRE/Code/Report 四角色 system prompt 片段，风格保守严谨审计型，与开发文档一致。
```

---

### 步骤 B2：`agents/base.py` 与各 Agent 壳 ✅

**目标**：统一接口（如 `run(context) -> str` 或结构化 `AgentResult`），四个类继承或组合基类，**暂不调用真实大模型**，返回占位文本即可。

**已落实**：`AgentResult` + `BaseAgent`；`OpsAgent`/`SREAgent`/`CodeAgent`/`ReportAgent`；`CodeAgent` 对生成脚本调用 `fence_check_text`（`metadata["code_draft"]` 可注入高危内容演示拦截）；`tests/test_agents.py`。

**交付**：`src/power_aiops/agents/base.py`、`ops.py`、`sre.py`、`code.py`、`report.py`、`__init__.py`。

**验证**：编排层可依次调用四者而不报错。

**Cursor 提示语**：

```
@DEVELOPMENT_PLAN.md 执行步骤 B2：新增 src/power_aiops/agents/，
定义 BaseAgent 与 OpsAgent、SREAgent、CodeAgent、ReportAgent，
方法签名统一；先用占位逻辑返回固定字符串；Code 路径需调用 security.fences.fence_check_text 做演示。
```

---

### 步骤 B3：`orchestration/` 最小状态机 + `IncidentContext` 串联 ✅

**目标**：给定 `IncidentContext`（含若干 `EventObject`），按固定顺序：Ops 初判 → SRE 方案要点 → Code 脚本草案（过围栏）→ Report 摘要；把每步输出写入 `SharedBoard` 与 `ShortTermMemory`。

**已落实**：`PipelineState`、`run_pipeline()`、固定 `BOARD_KEY_*`；Code 拦截时 `state.code_blocked` 与 board 同步，仍执行 Report；`tests/test_pipeline.py`。

**交付**：`orchestration/state.py`（ TypedDict 或 Pydantic 状态）、`orchestration/pipeline.py` 或 `graph.py`、`__init__.py`。

**验证**：纯 Python 调用一次 pipeline，断言 `SharedBoard` 中有预期 key。

**Cursor 提示语**：

```
@DEVELOPMENT_PLAN.md 执行步骤 B3：新增 src/power_aiops/orchestration/，
实现不依赖 LangGraph 的线性 pipeline：接收 IncidentContext，依次调用四 Agent，
更新 SharedBoard 与 ShortTermMemory；对 Code 输出做 fence_check_text，不通过则在中 state 标记 blocked。
```

---

## 阶段 C — 对外接口

### 步骤 C1：FastAPI 最小服务 ✅

**目标**：`POST /incidents/demo` 或 `POST /incidents/run` 接收 JSON（设备、指标、可选事件列表），构建 `IncidentContext`，跑 pipeline，返回 trace 摘要与 board 快照。

**已落实**：`api/app.py`（`create_app`、`/health`、`/`）、`api/routes.py`、`api/schemas.py`；`tests/test_api.py`；README 中 `uvicorn` 启动说明。

**交付**：`src/power_aiops/api/app.py`、`routes.py`、`__init__.py`；`uvicorn` 启动说明写入 README。

**验证**：`uvicorn power_aiops.api.app:app --reload` 本地 curl 通。

**Cursor 提示语**：

```
@DEVELOPMENT_PLAN.md 执行步骤 C1：新增 src/power_aiops/api/，
用 FastAPI 暴露 POST 接口运行 orchestration pipeline，请求体用 Pydantic 模型，
响应包含 incident_id、trace_id、shared_board 快照、是否被 fence 拦截；提供 app 对象供 uvicorn 加载。
```

---

### 步骤 C2：CLI `python -m power_aiops` 或 `power-aiops` ✅

**目标**：命令行传入 JSON 文件或示例 flag，打印 pipeline 结果（与 API 复用同一入口函数）。

**已落实**：`run_incident.execute_incident_run` 供 API/CLI 共用；`cli.py` 实现 `run --demo`、`run --json`、`--pretty`；`__main__.py` 转调 `cli.main`；`pyproject.toml` 入口 `power_aiops.cli:main`；`tests/test_cli.py`。

**交付**：完善 `src/power_aiops/__main__.py` 中的 `main()`。

**验证**：`power-aiops run --demo` 或 `python -m power_aiops run --demo` 有输出。

**Cursor 提示语**：

```
@DEVELOPMENT_PLAN.md 执行步骤 C2：实现 src/power_aiops/__main__.py，
子命令 run：支持 --demo 使用内置示例 Incident；支持 --json path；
内部调用与 API 相同的 orchestration 入口；已在 pyproject.toml 的 scripts 中注册 power-aiops 则保持一致。
```

---

## 阶段 D — 集成桩与扩展点

### 步骤 D1：`integrations/` 监控接入桩 ✅

**目标**：`prometheus.py`、`elk.py` 等：函数签名固定，内部 `NotImplementedError` 或返回空列表，并注明未来实现方式。

**已落实**：`PrometheusClientConfig` / `ElkClientConfig`；`fetch_prometheus_events_stub`、`fetch_elk_events_stub`（空列表）；`map_prometheus_sample_to_event`、`map_elk_hit_to_event` 示例映射；`tests/test_integrations.py`。

**交付**：`src/power_aiops/integrations/*.py` + 将外部数据映射为 `EventObject` 的说明注释。

**Cursor 提示语**：

```
@DEVELOPMENT_PLAN.md 执行步骤 D1：新增 src/power_aiops/integrations/，
为 Prometheus、ELK 写 fetch_events_stub() 之类接口，返回 list[EventObject] 或空列表，
文档字符串说明生产环境如何配置 URL/鉴权。
```

---

### 步骤 D2：`models/messages.py` 与消息持久化接口 ✅

**目标**：`AgentMessage` 序列化 JSON；定义 `MessageLog` 协议 + 内存实现，便于日后换 Redis/Kafka。

**已落实**：`agent_message_to_json_dict` / `agent_message_from_json_dict`；`memory/message_log.py` 中 `MessageLog` 与 `InMemoryMessageLog`；`run_pipeline(..., message_log=)` 可选写入每步 handoff；`AgentMessage.timestamp` 改为 UTC；`tests/test_messages.py`、`test_message_log.py`、pipeline 用例。

**交付**：`src/power_aiops/messaging/` 或 `memory/message_log.py`（择一，保持单一职责）。

**Cursor 提示语**：

```
@DEVELOPMENT_PLAN.md 执行步骤 D2：为 AgentMessage 增加 to_json_dict/from_json_dict 或 model_dump 封装；
新增内存版 MessageLog（append + list_by_trace_id），供 orchestration 可选记录每步消息。
```

---

### 步骤 D3：LLM 适配层（可选开关） ✅

**目标**：`src/power_aiops/llm/client.py`：若配置了 `OPENAI_API_KEY` 则调用 OpenAI 兼容接口，否则走 stub。Agent 内通过依赖注入调用，**默认仍可用无 Key 演示**。

**已落实**：`OpenAICompatibleClient.chat`（`/v1/chat/completions`）、`chat_completion_stub`；`Settings.openai_timeout_seconds`；`OpsAgent(use_llm=False)` 默认占位，`use_llm=True` 时走 LLM（无 key 仍为 stub）；`tests/test_llm.py`。

**Cursor 提示语**：

```
@DEVELOPMENT_PLAN.md 执行步骤 D3：新增 src/power_aiops/llm/client.py 与配置项，
实现 OpenAI 兼容 chat 封装；无 key 时返回占位文本；将 B2 中至少一个 Agent 改为可选真实调用（通过参数 use_llm=False 默认关闭）。
```

---

## 阶段 E — 质量与文档

### 步骤 E1：单元测试

**目标**：覆盖 `fences`、`messages` 序列化、`pipeline`  happy path + fence blocked path。

**交付**：`tests/test_fences.py`、`tests/test_pipeline.py` 等。

**Cursor 提示语**：

```
@DEVELOPMENT_PLAN.md 执行步骤 E1：在 tests/ 添加 pytest 用例，覆盖 fence_check_text、
orchestration pipeline 成功与 Code 输出触发拦截分支，使用 faker/fixture 构建 IncidentContext。
```

---

### 步骤 E2：Ruff / 格式化（可选）

**目标**：`ruff check src tests` 无报错。

**Cursor 提示语**：

```
@DEVELOPMENT_PLAN.md 执行步骤 E2：对 src tests 运行 ruff 并修复可自动修复项，保持与 pyproject 中 ruff 配置一致。
```

---

### 步骤 E3：更新 README 与「下一步」

**目标**：README 与 `DEVELOPMENT_PLAN.md` 中阶段 B–E 状态一致；列出图数据库、向量库、K8s 沙箱等**后续里程碑**（不实现）。

**Cursor 提示语**：

```
@DEVELOPMENT_PLAN.md 执行步骤 E3：更新 README.md 与本文档顶部的进度总览表，
标记已完成的步骤，并在 README 末尾增加「后续里程碑」：图 RAG、向量库、沙箱执行、人工审批流。
```

---

## 附：与仓库当前文件的对应关系

已存在（可先不改动，除非某步要求重构）：

- `README.md`、`DEVELOPMENT_PLAN.md`、`pyproject.toml`、`setup.py`、`.env.example`
- `src/power_aiops/__main__.py` — CLI 入口
- `src/power_aiops/models/` — `events`, `messages`, `incident`
- `src/power_aiops/memory/` — `short_term`, `shared_board`, `long_term` stub
- `src/power_aiops/security/` — `fences`, `permissions`
- `src/power_aiops/config.py`
- `src/power_aiops/prompts/` — 五步思维链与四角色 system 片段
- `src/power_aiops/agents/` — BaseAgent、四角色占位实现
- `src/power_aiops/orchestration/` — `run_pipeline`、`PipelineState`
- `src/power_aiops/api/` — FastAPI、`/incidents/run`、`/incidents/demo`
- `src/power_aiops/cli.py`、`run_incident.py`
- `src/power_aiops/integrations/` — Prometheus / ELK 桩与映射函数
- `src/power_aiops/memory/message_log.py` — `MessageLog`、`InMemoryMessageLog`
- `src/power_aiops/llm/` — OpenAI 兼容 `OpenAICompatibleClient`
- `tests/` — 冒烟、prompts、agents、pipeline、api、cli、integrations、messages、llm 测试

阶段 D 相关交付已在仓库中落地；后续见 **阶段 E**（测试、Ruff、文档同步）。

---

## 建议的默认执行顺序（一条线走到底）

`A1 → A2 → B1 → B2 → B3 → C1 → C2 → D1 → D2 → E1 → E3`

`D3`、`E2` 可按需要插入在 `C2` 之后。

---

*文档版本：与仓库 `power-aiops-agents` 0.1.0 对齐。*
