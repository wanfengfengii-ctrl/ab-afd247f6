"""HTTP API for the beamline exposure scheduler."""

from __future__ import annotations

import os
from dataclasses import asdict
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from solver import INF, solve
from validation import ValidationError, validate

app = FastAPI(title="Beamline Exposure Scheduler", version="1.0.0")


class Health(BaseModel):
    status: str
    service: str


@app.get("/health", response_model=Health)
def health() -> Health:
    return Health(status="ok", service="scheduler-api")


# Alias used by the dev proxy / monitoring paths that keep the /api prefix.
@app.get("/api/health", response_model=Health)
def health_alias() -> Health:
    return health()


@app.post("/api/schedule")
async def schedule(request: Request) -> JSONResponse:
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse(
            status_code=400,
            content=_error_body(
                "invalid_input",
                "请求体不是合法的 JSON。",
                [
                    {
                        "code": "bad_json",
                        "message": "请求体必须是合法的 JSON 对象。",
                    }
                ],
            ),
        )

    try:
        exposures, links = validate(payload)
    except ValidationError as exc:
        return JSONResponse(
            status_code=422,
            content=_error_body(
                "invalid_input",
                f"输入存在 {len(exc.errors)} 处错误，未执行求解，也不会返回"
                "任何旧排程。",
                exc.errors,
            ),
        )

    result = solve(exposures, links)

    if not result.feasible:
        # 409 + reason=no_feasible_schedule: input is valid, but no
        # executable integer timeline exists. No partial schedule and no
        # stale result is ever returned.
        return JSONResponse(
            status_code=409,
            content={
                "status": "infeasible",
                "reason": result.reason,
                "reason_detail": result.reason_detail,
                "starts": None,
                "schedule": None,
            },
        )

    body: dict[str, Any] = {
        "status": "feasible",
        "final_end": result.final_end,
        "starts": result.starts,
        "sum_starts": sum(result.starts or []),
        "objective": result.stats.get("objective"),
        "equipment_order": result.equipment_order,
        "margins": result.margins,
        "exposures": [asdict(e) for e in exposures],
        "links": [
            {
                "a": lk.a,
                "b": lk.b,
                "min_gap": lk.min_gap,
                "max_gap": None if lk.max_gap >= INF else lk.max_gap,
            }
            for lk in links
        ],
        "nodes": result.stats.get("nodes"),
    }
    return JSONResponse(status_code=200, content=body)


def _error_body(
    reason: str, detail: str, errors: list[dict]
) -> dict[str, Any]:
    return {
        "status": "invalid_input",
        "reason": reason,
        "reason_detail": detail,
        "errors": errors,
        "starts": None,
        "schedule": None,
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=int(os.environ.get("API_PORT", "8000")),
    )
