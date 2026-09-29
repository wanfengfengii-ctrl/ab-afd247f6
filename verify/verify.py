"""One-shot verification entrypoint for the ``verify`` compose service.

Runs four checks and exits with a bitmask summarizing them:

    bit 0 (1)  : backend unit tests failed
    bit 1 (2)  : frontend production build failed
    bit 2 (4)  : feasible-schedule API smoke test failed
    bit 3 (8)  : no-solution (409) API smoke test failed

Exit code 0 means everything passed.  The process always terminates on
its own (it is a one-shot service) and never accepts a partial schedule
as success.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

ROOT = os.environ.get("PROJECT_ROOT", "/project")
BACKEND = os.path.join(ROOT, "backend")
WEB = os.path.join(ROOT, "web")
API_BASE = os.environ.get("API_BASE_URL", "http://127.0.0.1:8000").rstrip("/")

FAIL_TESTS = 1
FAIL_BUILD = 2
FAIL_FEASIBLE = 4
FAIL_INFEASIBLE = 8

mask = 0


def section(title: str) -> None:
    print("\n" + "=" * 68)
    print(title)
    print("=" * 68, flush=True)


# ---------------------------------------------------------------------------
# 1. backend unit tests
# ---------------------------------------------------------------------------
section("[1/4] 后端代码测试（pytest，含暴力枚举等价校验）")
rc = subprocess.call(
    [sys.executable, "-m", "pytest", "tests", "-q"],
    cwd=BACKEND,
)
if rc != 0:
    print("!! 单元测试失败")
    mask |= FAIL_TESTS
else:
    print("++ 单元测试全部通过")


# ---------------------------------------------------------------------------
# 2. frontend production build
# ---------------------------------------------------------------------------
section("[2/4] 前端构建（vite build）")
npm = os.environ.get("NPM_BIN", "npm")
rc = subprocess.call([npm, "run", "build"], cwd=WEB)
if rc != 0:
    print("!! 前端构建失败")
    mask |= FAIL_BUILD
else:
    print("++ 前端构建成功")


# ---------------------------------------------------------------------------
# HTTP helpers (talk to the running api compose service)
# ---------------------------------------------------------------------------
def post(payload):
    req = urllib.request.Request(
        f"{API_BASE}/api/schedule",
        data=json.dumps(payload).encode(),
        headers={"content-type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())


def get(path: str):
    with urllib.request.urlopen(f"{API_BASE}{path}", timeout=10) as resp:
        return resp.status, json.loads(resp.read().decode())


def wait_for_api(timeout: float = 90.0) -> bool:
    deadline = time.time() + timeout
    last_err = None
    while time.time() < deadline:
        try:
            st, body = get("/health")
            if st == 200 and body.get("status") == "ok":
                return True
        except (OSError, urllib.error.URLError) as exc:
            last_err = exc
            time.sleep(1)
    print(f"!! API {API_BASE} 在超时内未通过健康检查: {last_err}")
    return False


# ---------------------------------------------------------------------------
# Wait for the API service (compose starts api before verify)
# ---------------------------------------------------------------------------
if not wait_for_api():
    sys.exit(mask | FAIL_FEASIBLE | FAIL_INFEASIBLE)
print(f"++ API 健康检查通过：{API_BASE}")


# ---------------------------------------------------------------------------
# 3. feasible schedule smoke
# ---------------------------------------------------------------------------
section("[3/4] 可行排程 API 冒烟")
feasible_payload = {
    "exposures": [
        {"name": "A", "duration": 3, "est": 0, "lst": 10,
         "equipment": "D1", "cooldown": 1},
        {"name": "B", "duration": 2, "est": 0, "lst": 10,
         "equipment": "D1", "cooldown": 0},
        {"name": "C", "duration": 4, "est": 1, "lst": 12,
         "equipment": "D2", "cooldown": 2},
        {"name": "D", "duration": 1, "est": 0, "lst": 20,
         "equipment": "D2", "cooldown": 0},
        {"name": "E", "duration": 2, "est": 0, "lst": 20,
         "equipment": "D1", "cooldown": 0},
    ],
    "links": [{"a": 0, "b": 1, "min_gap": 0, "max_gap": 9}],
}
st, body = post(feasible_payload)
ok = st == 200 and body.get("status") == "feasible"
if ok:
    try:
        exps = feasible_payload["exposures"]
        starts = body["starts"]
        assert len(starts) == 5 and all(isinstance(v, int) for v in starts)
        for i, e in enumerate(exps):
            assert e["est"] <= starts[i] <= e["lst"], "超出时间窗"
        # equipment occupancy + cooldown non-overlap
        by_dev: dict[str, list[int]] = {}
        for i, e in enumerate(exps):
            by_dev.setdefault(e["equipment"], []).append(i)
        for members in by_dev.values():
            members.sort(key=lambda i: starts[i])
            for x, y in zip(members, members[1:]):
                assert (
                    starts[y]
                    >= starts[x] + exps[x]["duration"] + exps[x]["cooldown"]
                ), "设备占用或冷却重叠"
        # link gap
        gap = starts[1] - starts[0]
        assert 0 <= gap <= 9, "衔接间隔越界"
        assert isinstance(body["final_end"], int)
        assert body["equipment_order"] and body["margins"]
        # no half answer
        assert all(v is not None for v in starts)
        print(
            f"++ 可行排程返回 200：starts={starts} "
            f"final_end={body['final_end']} sum={body['sum_starts']}"
        )
    except AssertionError as exc:
        ok = False
        print(f"!! 可行排程内容校验失败: {exc}")
else:
    print(f"!! 可行排程冒烟失败: status={st} body={body}")
if not ok:
    mask |= FAIL_FEASIBLE


# ---------------------------------------------------------------------------
# 4. no-solution smoke (valid input, infeasible resource usage)
# ---------------------------------------------------------------------------
section("[4/4] 无解 API 冒烟（须明确区分输入错误与无可执行时序）")
infeasible_payload = {
    "exposures": [
        {"name": f"N{i}", "duration": 10, "est": 0, "lst": 1,
         "equipment": "ONLY", "cooldown": 0}
        for i in range(5)
    ],
    "links": [],
}
st, body = post(infeasible_payload)
ok = (
    st == 409
    and body.get("status") == "infeasible"
    and body.get("reason") == "no_feasible_schedule"
    and body.get("starts") is None
    and body.get("schedule") is None
    and bool(body.get("reason_detail"))
)
if ok:
    print("++ 无解实例返回 409，starts/schedule 均为 null，且给出原因说明")
else:
    print(f"!! 无解冒烟失败: status={st} body={body}")
    mask |= FAIL_INFEASIBLE

# extra guard: malformed input is an input error, not an infeasibility
bad = json.loads(json.dumps(infeasible_payload))
bad["exposures"][0]["duration"] = -3
st, body = post(bad)
if not (st == 422 and body.get("status") == "invalid_input"):
    print(f"!! 输入错误未返回 422: status={st}")
    mask |= FAIL_INFEASIBLE
else:
    print("++ 输入错误返回 422 invalid_input，与 409 无解明确区分")


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
section("汇总")
labels = [
    (FAIL_TESTS, "代码测试"),
    (FAIL_BUILD, "前端构建"),
    (FAIL_FEASIBLE, "可行排程冒烟"),
    (FAIL_INFEASIBLE, "无解API冒烟"),
]
if mask == 0:
    print("ALL CHECKS PASSED (exit 0)")
else:
    failed = [name for bit, name in labels if mask & bit]
    print(f"FAILED: {', '.join(failed)}  (exit code {mask})")
sys.exit(mask)
