# AgentScope 2.0.12 学习与业务接入指南

本项目已经接入 AgentScope 2.0.12、百炼模型、SQLite 业务工具、持久化会话、JWT 登录鉴权、写操作人工审批和 Docker Compose 部署。

> 安全提醒：API Key 已经写入本机 `.env`，该文件被 `.gitignore` 和 `.dockerignore` 忽略。由于 Key 曾出现在聊天记录中，完成验证后建议在百炼控制台轮换。登录账号和随机密码位于 `.env` 的 `BOOTSTRAP_ADMIN_*` 与 `DEMO_*` 配置中，不要提交或公开这些值。

## 1. 先理解整体架构

```text
浏览器
  │  JWT + SSE
  ▼
FastAPI Web 应用（web_app.py）
  ├── 登录鉴权与角色检查（auth.py）
  ├── 会话 API / 并发锁（web_app.py）
  ├── AgentState 持久化（business.py + SQLite）
  └── AgentScope Agent
        ├── DashScopeChatModel / OpenAIChatModel
        ├── Toolkit 业务工具
        │     ├── 订单查询（只读）
        │     ├── 工单查询（只读）
        │     ├── 创建工单（写入，需要确认）
        │     └── 关闭工单（写入，需要确认）
        └── AgentEvent / Msg 流式事件
```

当前“真实业务工具”是真实 SQLite CRUD，不是硬编码返回值。示例业务域为订单与客服工单。接入公司系统时，按第 6 节把工具内部替换为你的 HTTP API、数据库或 MCP Server 即可。

## 2. 本地启动

### 2.1 安装依赖

在项目根目录执行：

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt
```

Windows PowerShell 激活虚拟环境的命令是：

```powershell
.venv\Scripts\Activate.ps1
```

### 2.2 检查配置

`.env` 已经配置好百炼 Key 和随机登录密码。重点关注：

```dotenv
AGENT_PROVIDER=dashscope
DASHSCOPE_MODEL=qwen-plus
DATABASE_PATH=.runtime/business.sqlite3
BOOTSTRAP_ADMIN_USERNAME=admin
DEMO_USERNAME=demo
AUTH_JWT_SECRET=...
ACCESS_TOKEN_MINUTES=120
```

查看用户名，不输出密码：

```bash
PYTHONUTF8=1 .venv/Scripts/python.exe -c "import os; from dotenv import load_dotenv; load_dotenv(); print(os.getenv('DEMO_USERNAME'), os.getenv('BOOTSTRAP_ADMIN_USERNAME'))"
```

密码请直接在本机 `.env` 中查看 `DEMO_USER_PASSWORD` 和 `BOOTSTRAP_ADMIN_PASSWORD`。

### 2.3 启动 Web 应用

```bash
PYTHONUTF8=1 .venv/Scripts/python.exe -m web_app
```

也可以运行：

```bash
PYTHONUTF8=1 .venv/Scripts/python.exe run_demo.py 10
```

打开 `http://127.0.0.1:8000`，使用 `.env` 中的账号登录。

首次启动会自动：

1. 创建用户、订单、工单、会话、审计日志表。
2. 初始化管理员与演示用户。
3. 为演示用户插入 3 个示例订单和 1 个示例工单。
4. 启用 SQLite WAL 与外键约束。

建议依次测试：

- “查询我的全部订单”
- “查看我的未关闭工单”
- “为账号登录问题创建一个高优先级工单”
- 在审批卡片中批准或拒绝
- 刷新页面，确认历史会话仍然存在
- 退出后换管理员登录，确认看不到演示用户的私有会话

## 3. 循序渐进学习 AgentScope

先运行教学 Demo，再阅读业务版本。所有 Demo 都使用同一套模型配置。

| 顺序 | 命令 | 核心知识点 | 建议练习 |
|---|---|---|---|
| 00 | `python run_demo.py 00` | 环境、Provider、模型与 Key 检查 | 切换 DashScope/OpenAI Provider |
| 01 | `python run_demo.py 01 "用一句话介绍 AgentScope"` | 模型直接调用与流式响应 | 加入 `--no-stream` 对比 |
| 02 | `python run_demo.py 02` | Pydantic 结构化输出 | 增加一个字段并观察校验 |
| 03 | `python run_demo.py 03` | ReAct 循环与工具调用 | 增加一个确定性工具 |
| 04 | `python run_demo.py 04` | `AgentEvent` 流式事件 | 识别模型、文本、工具事件 |
| 05 | `python run_demo.py 05 save` | `AgentState` 序列化 | 修改消息后恢复 |
| 05 | `python run_demo.py 05 resume` | 跨进程恢复上下文 | 重启后再追问 |
| 06 | `python run_demo.py 06` | 手动多 Agent 协作 | 增加一个评审角色 |
| 07 | `python run_demo.py 07` | `TeamPipeline` 团队委派 | 调整成员职责 |
| 08 | `python run_demo.py 08` | 上下文压缩与工具结果截断 | 观察压缩前后消息数 |
| 09 | `python run_demo.py 09` | OpenTelemetry 与回复预算 | 接入真实 OTLP Collector |
| 10 | `python run_demo.py 10` | 完整 Web 业务应用 | 走通登录、工具、审批、持久化 |

### 3.1 模型层

核心入口是 `demos/common.py` 的 `create_model()`：

- DashScope：`DashScopeCredential` + `DashScopeChatModel`
- OpenAI 兼容接口：`OpenAICredential` + `OpenAIChatModel`
- 通过 `stream` 控制流式输出
- 通过 `context_size` 控制模型上下文配置

AgentScope 不是模型本身。模型负责推理，AgentScope 负责消息、工具、状态、事件和生命周期。

### 3.2 Agent 层

`build_agent()` 把以下内容组合起来：

```python
agent = build_agent(
    name="业务助手",
    system_prompt="只能使用工具获取业务数据。",
    tools=make_business_toolkit(user),
    stream=True,
    state=state,
)
```

需要理解四个概念：

- `system_prompt`：约束角色、边界和输出风格。
- `Toolkit`：声明 Agent 可以调用哪些工具。
- `AgentState`：保存会话、回复、权限和工具上下文。
- `reply_stream()`：持续产出 `AgentEvent`，最后可能产出 `Msg`。

### 3.3 消息与事件

用户消息使用 `UserMsg`，模型回复使用 `AssistantMsg` 或包含多个内容块的 `Msg`。

Web 层把 AgentScope 事件转换为 SSE：

| AgentScope 事件 | Web SSE 事件 |
|---|---|
| `TextBlockDeltaEvent` | `text_delta` |
| `ToolCallStartEvent` | `tool_start` |
| `ToolCallDeltaEvent` | `tool_delta` |
| `ToolResultTextDeltaEvent` | `tool_result_delta` |
| `RequireUserConfirmEvent` | `approval_required` |
| `ReplyEndEvent` | `reply_end` |
| 最终 `Msg` | `final` |

前端必须解析 SSE 的 `event:` 行，不能只依赖 JSON 中的字段。

### 3.4 状态持久化

`AgentState` 可以完整序列化为 JSON：

```python
state_json = agent.state.model_dump_json()
state = AgentState.model_validate_json(state_json)
```

业务版把 JSON 存入 `sessions.state_json`。服务重启后：

1. 根据用户 ID 和会话 ID 查询记录。
2. 反序列化 `AgentState`。
3. 重新创建 Agent，并注入同一个 state。
4. 继续对话或处理未完成的审批。

同一用户的同一会话使用 `asyncio.Lock`，避免并发修改同一份 Agent 状态。

### 3.5 多 Agent

先区分两种方式：

- **手动协作**：每个 Agent 有独立状态，通过 `observe()` 共享 `AssistantMsg`。
- **TeamPipeline**：由 Leader 通过 `TeamAssign` 委派成员任务。

多 Agent 适合角色、上下文或工具权限需要隔离的场景。简单问答不要强行拆成多个 Agent。

## 4. 认证、鉴权与数据隔离

### 4.1 当前实现

- 密码使用 `scrypt` 加盐哈希，不保存明文。
- 登录签发 JWT，默认 120 分钟过期。
- API 使用 `Authorization: Bearer <token>`。
- 普通用户与管理员通过 `role` 区分。
- 会话查询同时校验 `user_id` 和 `session_id`。
- 业务工具通过闭包绑定当前用户，模型不能传入任意 `user_id`。
- 管理员审计接口要求 `admin` 角色。

### 4.2 关键边界

- 未登录访问业务 API 返回 401。
- 访问他人会话返回 404，避免暴露资源是否存在。
- 普通用户访问管理员接口返回 403。
- 前端令牌保存在 `sessionStorage`，不放在 URL 或 LocalStorage。
- 所有动态文本使用 `textContent` 渲染，避免 XSS。

### 4.3 生产环境还需补充

当前实现适合本地与小型单实例部署。正式上线建议增加：

- 刷新令牌、令牌撤销和设备会话管理。
- 登录限流、验证码或 SSO。
- 组织/租户级 RBAC。
- 密钥托管与定期轮换。
- 集中日志、告警和审计保留策略。

## 5. 写操作审批（HITL）

创建和关闭工单的工具使用 `is_read_only=False`。AgentScope 不会自动执行，而是产出 `RequireUserConfirmEvent`。

流程如下：

```text
用户要求创建工单
  → 模型调用 create_support_ticket
  → AgentScope 暂停并保存 AgentState
  → Web 发送 approval_required
  → 用户批准或拒绝
  → POST /api/confirm
  → UserConfirmResultEvent 恢复 Agent
  → 工具执行并写入审计日志
  → 模型根据工具结果生成最终回答
```

确认接口会校验：

- 会话属于当前用户。
- `reply_id` 与当前暂停回复一致。
- 确认项与待审批工具调用完全匹配。

不要为了让 Demo 顺畅而把写工具标成只读。只读标记会改变权限语义。

## 6. 如何接入你的真实业务系统

### 6.1 添加数据库工具

在 `business.py` 中：

1. 添加表结构和索引。
2. 编写带 `user_id` 条件的数据访问函数。
3. 在 `make_business_toolkit()` 中创建闭包工具。
4. 查询工具设置 `is_read_only=True`。
5. 写入工具保持 `is_read_only=False`。
6. 写入后记录审计日志。

### 6.2 添加内部 HTTP API 工具

推荐模式：

```python
async def get_customer_order(order_no: str) -> str:
    """查询当前用户的订单详情。"""
    headers = {"Authorization": f"Bearer {os.environ['BUSINESS_API_TOKEN']}"}
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(
            f"{os.environ['BUSINESS_API_BASE_URL'].rstrip('/')}/orders/{order_no}",
            headers=headers,
        )
        response.raise_for_status()
        return json.dumps(response.json(), ensure_ascii=False)
```

安全要求：

- URL 必须来自受控环境变量，不能由模型任意指定。
- 服务端仍需按当前用户校验订单归属。
- 设置超时、重试上限和响应体大小限制。
- 不把 API 密钥写入系统提示或工具描述。
- 只开放必要端点，不提供任意 HTTP 请求工具。

### 6.3 添加 MCP 工具

AgentScope 支持 MCP。生产环境建议使用独立 MCP Server 暴露业务能力，并由 MCP Server 做认证、授权和审计。不要把高权限 MCP Server 直接暴露给不可信模型输入。

## 7. 部署

### 7.1 Docker Compose

构建并启动：

```bash
docker compose up -d --build
docker compose ps
docker compose logs -f app
```

应用默认只绑定宿主机 `127.0.0.1:8000`。数据保存在命名卷 `agentscope-data`，容器重建不会丢失。

停止：

```bash
docker compose down
```

### 7.2 HTTPS 反向代理

为真实域名配置 `.env`：

```dotenv
DOMAIN=your-domain.example
ACME_EMAIL=your-email@example.com
```

启动 Caddy：

```bash
docker compose --profile proxy up -d --build
```

Caddy 自动申请证书并反向代理到 `app:8000`。域名需要解析到服务器，80/443 端口需要可达。

### 7.3 备份 SQLite

在线备份示例：

```bash
docker compose exec app python -c "import sqlite3; source=sqlite3.connect('/data/business.sqlite3'); target=sqlite3.connect('/data/backup.sqlite3'); source.backup(target); target.close(); source.close()"
docker compose cp app:/data/backup.sqlite3 .runtime/
```

### 7.4 部署边界

- 当前使用单进程、单 Worker，避免 SQLite 多写者竞争。
- SQLite 适合本地、小团队和低并发业务。
- 多实例或高并发部署时，把会话与业务存储迁移到 PostgreSQL，并增加连接池与迁移工具。
- 容器以非 root 用户运行，启用只读根文件系统、丢弃 Linux capabilities，并禁止提权。
- 生产环境使用 HTTPS、 secret manager、集中日志和监控。

## 8. 测试

运行离线测试：

```bash
PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest -q
```

测试使用本地 OpenAI 兼容模拟服务，不消耗真实模型额度，覆盖：

- 模型结构化输出。
- 工具执行与流式事件。
- 多 Agent 委派。
- 密码哈希与 JWT。
- 未登录访问。
- 用户间会话隔离。
- 管理员权限。
- 数据库数据隔离。
- 会话重启恢复。
- 写操作审批与审计。

模拟模型只能验证框架编排，不能验证真实模型的语义质量。真实模型验证应单独执行 Demo 00、01、03 和 Web 业务对话。

## 9. 常见问题

### 登录失败

- 确认 `.env` 中密码至少 12 个字符。
- 修改密码环境变量不会自动更新已有用户密码；需要删除对应测试数据库后重新初始化，或实现密码重置流程。

### 模型调用失败

- 运行 `python run_demo.py 00`。
- 检查 Key、模型名、Provider 和账户额度。
- 百炼默认使用 SDK 官方地址；只有使用兼容网关时才设置 `DASHSCOPE_BASE_URL`。

### 写操作一直暂停

这是预期行为。前端需要调用 `/api/confirm`，不能直接再次发送普通聊天消息。

### 会话刷新后丢失

- 检查 `DATABASE_PATH` 是否指向持久化目录。
- Docker 部署必须使用 `/data` 卷。
- 不要在不同容器实例间使用未共享的本地 SQLite 文件。

### 数据库被锁定

- 保持单 Worker。
- 检查是否有多个进程同时写入同一 SQLite 文件。
- 高并发场景迁移 PostgreSQL。

## 10. 源码导航

| 文件 | 作用 |
|---|---|
| `demos/common.py` | Provider、Agent、事件渲染与模型清理 |
| `demos/00_doctor.py` 至 `demos/09_observability.py` | 渐进式教学 Demo |
| `business.py` | SQLite、业务数据、工具、审计与会话存储 |
| `auth.py` | scrypt 密码哈希与 JWT |
| `web_app.py` | FastAPI、登录、会话、SSE、HITL 与前端 |
| `tests/mock_provider.py` | 离线模型服务 |
| `tests/test_integration.py` | 集成与安全测试 |
| `Dockerfile` | 非 root 容器镜像 |
| `compose.yaml` | 应用、数据卷与可选 Caddy |
| `deploy/Caddyfile` | HTTPS 反向代理 |

## 11. 推荐学习顺序

1. 跑通 Demo 00–04，理解模型、消息、工具和事件。
2. 跑通 Demo 05，理解 `AgentState`。
3. 跑通 Demo 06–07，理解多 Agent 的两种组织方式。
4. 跑通 Demo 08–09，理解上下文与可观测性。
5. 在 Web 中走通订单查询和工单审批。
6. 阅读 `business.py`，自己增加一个只读工具。
7. 增加一个写工具，并验证审批与审计。
8. 用 Docker Compose 重启服务，验证会话仍在。
9. 最后再考虑 SSO、PostgreSQL、MCP、监控和生产发布。
