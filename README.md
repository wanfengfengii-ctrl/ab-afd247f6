# 同步辐射束线曝光排程系统

值班工程师在有限机时内编排 5–10 项探测器曝光：录入每项曝光的持续时间、
最早/最晚开始时刻、所用设备与结束后冷却时间，并设置曝光之间的最小/最大
衔接间隔。服务端联合求解全部**整数**开始时刻，保证：

- `est[i] ≤ s[i] ≤ lst[i]`（时间窗）；
- 同一设备上的曝光占用区间含**前序冷却**互不重叠；
- 所有衔接约束 `min_gap ≤ s[b] − s[a] ≤ max_gap` 同时成立；
- 按字典序依次最小化
  **最终结束时刻 → 开始时刻总和 → 按录入顺序展开的开始时刻序列**。

## 目录结构

```
backend/            FastAPI 服务 + 纯 Python 约束求解器
  solver.py         区间域传播 + 设备序列化分支定界（无外部求解器依赖）
  validation.py     输入校验（输入错误与“无解”严格区分）
  main.py           POST /api/schedule、GET /health
  tests/            pytest（含对随机实例的暴力枚举等价校验）
web/                Vite 原生 JS 前端（时间轴 / 设备顺序 / 每条约束余量）
  nginx.conf        生产镜像静态托管 + /api 反向代理，含 /health
verify/             一次性校验服务
docker-compose.yml  api / web / verify 三个服务
```

## 运行（Docker Compose）

```bash
# 可选：复制并修改宿主机端口
cp .env.example .env

docker compose build
docker compose up api web        # 常驻服务
docker compose run --rm verify   # 一次性校验（见下）
```

- Web 界面：`http://localhost:${WEB_HOST_PORT:-8080}`
- API：`http://localhost:${API_HOST_PORT:-8000}`（默认仅绑定宿主机回环）
- 健康检查：`GET http://localhost:8080/health`（Web/nginx）、
  `GET http://localhost:8000/health`（API），compose 同时配置了容器级
  healthcheck 与启动依赖。

端口可通过环境变量配置：`WEB_HOST_PORT`、`WEB_HOST_BIND`、
`API_HOST_PORT`、`API_HOST_BIND`。

## verify 一次性服务

`verify` 服务构建后依次执行：

1. 后端代码测试（`pytest`，含 40+ 组随机实例与暴力枚举的最优解等价校验）；
2. 前端生产构建（`vite build`）；
3. **可行排程** API 冒烟（校验 200、时间窗、设备占用/冷却不重叠、衔接间隔）；
4. **无解** API 冒烟（校验 409、`starts/schedule` 均为 null、原因说明，
   并额外确认畸形输入返回 422 而非 409）。

它自行退出，退出码为位掩码汇总（0 = 全部通过）：

| 退出码 | 含义 |
| --- | --- |
| 0 | 全部通过 |
| 1 | 代码测试失败 |
| 2 | 前端构建失败 |
| 4 | 可行排程冒烟失败 |
| 8 | 无解 API 冒烟失败 |

组合失败时为各位之和（例如 5 = 测试与可行冒烟均失败）。

```bash
docker compose up verify          # 结束后看 Exited (0) ...
```

## API

### `POST /api/schedule`

请求：

```json
{
  "exposures": [
    {"name": "A", "duration": 3, "est": 0, "lst": 10,
     "equipment": "D1", "cooldown": 1}
  ],
  "links": [
    {"a": 0, "b": 1, "min_gap": 0, "max_gap": 9}
  ]
}
```

`max_gap` 为 `null`/缺省表示不限上界。响应分三类，互不为歧义：

- `200 {"status":"feasible", ...}`：`starts` 整数序列、`final_end`、
  字典序目标、`equipment_order`、每类约束的 `margins`（余量）；
- `422 {"status":"invalid_input", "errors":[...]}`：**输入错误**，附逐项
  原因与字段路径，未执行求解；
- `409 {"status":"infeasible", "reason":"no_feasible_schedule", ...}`：
  输入合法但**无可执行时序**，`starts` 与 `schedule` 均为 `null`，
  并区分“时间窗/衔接自身矛盾”与“共用设备串行容量不足”。

服务端为原子求解：要么给出全部曝光的完整最优排程，要么明确报错——
**不会返回部分曝光**。页面在任何草稿修改后立即将旧结果置灰并显示
“旧方案已失效”横幅，重新求解前不会沿用旧方案。

## 本地开发（无 Docker）

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r backend/requirements.txt
uvicorn main:app --reload --app-dir backend        # :8000

cd web && npm install && npm run dev               # :5173 -> :8000
```
