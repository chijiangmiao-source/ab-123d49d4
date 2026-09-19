# Register Linearizability Audit Service

纯后端审计服务（Python 3.13 + FastAPI，无前端、不调用任何在线服务）：审计员提交单寄存器（read / write / compare-and-swap）的一段已完成操作历史，服务判定该历史是否可线性化，并给出裁决证据。

## 判定语义

- 每项操作带有整数调用时刻 `invoke` 与响应时刻 `respond`（要求 `respond >= invoke`）。
- 全序必须严格尊重实时先后约束：若操作 A 的响应时刻不晚于操作 B 的调用时刻（`A.respond <= B.invoke`），则 A 排在 B 之前。
- 按全序逐步执行寄存器语义：write 总是生效；read 必须返回当时的寄存器值；compare-and-swap 的成功标志必须与当时值是否等于期望值一致，成功时寄存器变为更新值，失败时保持不变。
- 可线性化时返回**按操作标识字典序裁决的最小合法全序**、每步执行前后的寄存器值，以及合法全序是否唯一。
- 不可线性化时明确返回 `linearizable: false`，`order` / `steps` 为 `null`，不伪造任何部分顺序。
- 非法区间、重复标识、类型字段不一致（如 `write` 携带 `expected`）、操作数不在 1–24 范围内、非整数时刻/取值等，整个请求以 HTTP 422 拒绝。

## 启动方式

### Docker Compose（推荐）

```bash
docker compose up --build
```

宿主机端口通过环境变量 `API_HOST_PORT` 配置（默认 8000）：

```bash
API_HOST_PORT=9000 docker compose up --build
```

Compose 服务配置了健康检查（轮询 `GET /health`），`docker compose ps` 可查看健康状态。

### Dockerfile 单独使用

```bash
docker build -t linearizability-audit .
docker run -p 8000:8000 linearizability-audit
# 容器内监听端口可用 PORT 环境变量调整：
docker run -e PORT=8080 -p 8080:8080 linearizability-audit
```

### 本地开发（无 Docker）

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
uvicorn app.main:app --port 8000
pytest            # 运行测试
```

## API

### `GET /health`

健康检查，返回 `{"status": "ok"}`。

### `POST /linearize`

请求体：

```json
{
  "initial_value": 0,
  "operations": [
    {"id": "w", "type": "write", "invoke": 1, "respond": 2, "value": 5},
    {"id": "r", "type": "read",  "invoke": 1, "respond": 4, "value": 5},
    {"id": "c", "type": "cas",   "invoke": 3, "respond": 6,
     "expected": 5, "update": 7, "success": true}
  ]
}
```

- `operations`：1 至 24 项，`id` 唯一。
- `type` 为 `write`（携带 `value` 写入值）、`read`（携带 `value` 返回值）或 `cas` / `compare-and-swap`（携带 `expected` 期望值、`update` 更新值、`success` 成功标志）。字段必须与类型一致，不得多带或少带。
- `invoke` / `respond` / `initial_value` / `value` / `expected` / `update` 均须为整数，`success` 须为布尔值。

可线性化响应（`200`）：

```json
{
  "linearizable": true,
  "unique": false,
  "order": ["w", "r", "c"],
  "steps": [
    {"id": "w", "before": 0, "after": 5},
    {"id": "r", "before": 5, "after": 5},
    {"id": "c", "before": 5, "after": 7}
  ],
  "detail": "linearizable: multiple legal total orders exist; returning the minimal one (lexicographic by operation identifier)"
}
```

不可线性化响应（`200`）：

```json
{
  "linearizable": false,
  "unique": null,
  "order": null,
  "steps": null,
  "detail": "not linearizable: no total order respects the real-time precedence constraints and the register semantics"
}
```

非法提交（非法区间、重复标识、类型字段不一致、数量越界等）整体以 `422` 拒绝。

交互式 API 文档见 `http://localhost:8000/docs`（Swagger UI）。

## 实现说明

- 求解器（`app/solver.py`）按操作标识升序深度优先枚举候选操作，逐步校验寄存器语义并剪枝；首个完整解即为字典序最小合法全序。搜索至多两个解即可判定唯一性；以「已放置集合 + 当前值」为状态做失败记忆化，保证 24 个操作规模下快速返回。
- 校验全部由 Pydantic 模型（`app/schemas.py`）完成，任何一项不合法即整体拒绝，不进入求解。
