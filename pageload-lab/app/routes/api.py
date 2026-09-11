"""Internal REST API consumed only by the Ingress administration UI."""

from __future__ import annotations

import io
from datetime import date as date_type
from datetime import datetime, time, timezone
from typing import Any

from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import StreamingResponse
from sqlalchemy import case, func, select

from ..models import RequestRecord, Test
from ..schemas import ProxyInput, SettingsInput, TestInput
from ..security import validate_target_url
from ..services.tests import serialize_test


router = APIRouter(prefix="/api")


@router.get("/dashboard")
async def dashboard(request: Request) -> dict[str, Any]:
    db = request.app.state.database
    engine = request.app.state.browser
    with db.session() as session:
        tests = session.scalars(select(Test).order_by(Test.updated_at.desc()).limit(20)).all()
        counts = dict(session.execute(select(Test.status, func.count(Test.id)).group_by(Test.status)).all())
        today = datetime.combine(datetime.now(timezone.utc).date(), time.min, tzinfo=timezone.utc)
        today_calls = session.scalar(select(func.count(RequestRecord.id)).where(RequestRecord.started_at >= today)) or 0
        total, successful, average = session.execute(select(
            func.count(RequestRecord.id),
            func.sum(case((RequestRecord.success.is_(True), 1), else_=0)),
            func.avg(RequestRecord.load_time_ms),
        )).one()
        total = int(total or 0)
        successful = int(successful or 0)
        valid_count = session.scalar(select(func.count(RequestRecord.id)).where(RequestRecord.load_time_ms.is_not(None))) or 0
        p95 = None
        if valid_count:
            p95 = session.scalar(select(RequestRecord.load_time_ms).where(RequestRecord.load_time_ms.is_not(None)).order_by(RequestRecord.load_time_ms).offset(round((valid_count - 1) * .95)).limit(1))
    active = sum(counts.get(status, 0) for status in ("running", "paused", "waiting_capacity"))
    return {
        "cards": {
            "active_tests": active,
            "scheduled_tests": counts.get("scheduled", 0),
            "completed_tests": counts.get("completed", 0),
            "calls_today": today_calls,
            "calls_total": total,
            "success_rate": round(successful * 100 / total, 2) if total else 0.0,
            "error_rate": round((total - successful) * 100 / total, 2) if total else 0.0,
            "average_ms": round(float(average), 2) if average is not None else None,
            "p95_ms": round(float(p95), 2) if p95 is not None else None,
            "active_browser_sessions": engine.active_sessions,
        },
        "tests": [serialize_test(row) for row in tests],
    }


@router.get("/tests")
async def list_tests(request: Request, limit: int = Query(200, ge=1, le=1000)) -> list[dict[str, Any]]:
    return request.app.state.test_service.list(limit)


@router.post("/tests", status_code=201)
async def create_test(request: Request, payload: TestInput, start: bool = False) -> dict[str, Any]:
    row, warnings = await request.app.state.test_service.create(payload)
    if start and row.start_type == "immediate":
        await request.app.state.runners.start(row.id)
        row = request.app.state.test_service.get(row.id)
    return {"test": serialize_test(row), "warnings": warnings}


@router.get("/tests/{test_id}")
async def get_test(request: Request, test_id: int) -> dict[str, Any]:
    row = request.app.state.test_service.get(test_id)
    return serialize_test(row)


@router.put("/tests/{test_id}")
async def update_test(request: Request, test_id: int, payload: TestInput) -> dict[str, Any]:
    row, warnings = await request.app.state.test_service.update(test_id, payload)
    return {"test": serialize_test(row), "warnings": warnings}


@router.delete("/tests/{test_id}", status_code=204)
async def delete_test(request: Request, test_id: int) -> Response:
    request.app.state.test_service.delete(test_id)
    return Response(status_code=204)


@router.post("/tests/{test_id}/duplicate", status_code=201)
async def duplicate_test(request: Request, test_id: int) -> dict[str, Any]:
    return serialize_test(request.app.state.test_service.duplicate(test_id))


@router.post("/tests/{test_id}/restart", status_code=201)
async def restart_test(request: Request, test_id: int) -> dict[str, Any]:
    row = request.app.state.test_service.restart_copy(test_id)
    await request.app.state.runners.start(row.id)
    return serialize_test(request.app.state.test_service.get(row.id))


@router.post("/tests/{test_id}/start", status_code=202)
async def start_test(request: Request, test_id: int) -> dict[str, str]:
    await request.app.state.runners.start(test_id)
    return {"status": "running"}


@router.post("/tests/{test_id}/pause", status_code=202)
async def pause_test(request: Request, test_id: int) -> dict[str, str]:
    await request.app.state.runners.pause(test_id)
    return {"status": "paused"}


@router.post("/tests/{test_id}/resume", status_code=202)
async def resume_test(request: Request, test_id: int) -> dict[str, str]:
    await request.app.state.runners.resume(test_id)
    return {"status": "running"}


@router.post("/tests/{test_id}/stop", status_code=202)
async def stop_test(request: Request, test_id: int) -> dict[str, str]:
    await request.app.state.runners.stop(test_id)
    return {"status": "stopped"}


@router.get("/tests/{test_id}/stats")
async def test_stats(request: Request, test_id: int) -> dict[str, Any]:
    result = request.app.state.reporting.stats(test_id)
    result["series"] = request.app.state.reporting.series(test_id)
    result["active_sessions"] = request.app.state.runners.active_sessions(test_id)
    return result


@router.get("/tests/{test_id}/requests")
async def test_requests(request: Request, test_id: int, page: int = 1, page_size: int = 50) -> dict[str, Any]:
    return request.app.state.reporting.requests(test_id, page, page_size)


@router.get("/tests/{test_id}/export/{format_name}")
async def export_test(request: Request, test_id: int, format_name: str) -> StreamingResponse:
    payload, content_type, filename = request.app.state.reporting.export(test_id, format_name)
    return StreamingResponse(io.BytesIO(payload), media_type=content_type, headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.get("/reports")
async def reports(
    request: Request,
    status: str | None = None,
    name: str | None = None,
    url: str | None = None,
    date_from: date_type | None = None,
    date_to: date_type | None = None,
) -> list[dict[str, Any]]:
    with request.app.state.database.session() as session:
        query = select(Test).where(Test.reporting_enabled.is_(True))
        if status:
            query = query.where(Test.status == status)
        if name:
            query = query.where(Test.name.contains(name))
        if url:
            query = query.where(Test.url.contains(url))
        if date_from:
            query = query.where(Test.created_at >= datetime.combine(date_from, time.min, tzinfo=timezone.utc))
        if date_to:
            query = query.where(Test.created_at <= datetime.combine(date_to, time.max, tzinfo=timezone.utc))
        rows = session.scalars(query.order_by(Test.created_at.desc()).limit(500)).all()
        return [serialize_test(row) for row in rows]


@router.get("/proxies")
async def proxies(request: Request) -> list[dict[str, Any]]:
    return request.app.state.proxies.list_public()


@router.post("/proxies", status_code=201)
async def create_proxy(request: Request, payload: ProxyInput) -> dict[str, Any]:
    return request.app.state.proxies.create(payload.model_dump())


@router.put("/proxies/{proxy_id}")
async def update_proxy(request: Request, proxy_id: int, payload: ProxyInput) -> dict[str, Any]:
    return request.app.state.proxies.update(proxy_id, payload.model_dump())


@router.delete("/proxies/{proxy_id}", status_code=204)
async def delete_proxy(request: Request, proxy_id: int) -> Response:
    request.app.state.proxies.delete(proxy_id)
    return Response(status_code=204)


@router.post("/proxies/{proxy_id}/test")
async def test_proxy(request: Request, proxy_id: int) -> dict[str, Any]:
    target = str(request.app.state.settings.get("proxy_test_url"))
    await validate_target_url(target, bool(request.app.state.settings.get("allow_private_targets", False)))
    return await request.app.state.proxies.test(proxy_id, target)


@router.get("/settings")
async def get_settings(request: Request) -> dict[str, Any]:
    return request.app.state.settings.all()


@router.put("/settings")
async def update_settings(request: Request, payload: SettingsInput) -> dict[str, Any]:
    values = payload.supplied()
    if "proxy_test_url" in values:
        await validate_target_url(str(values["proxy_test_url"]), bool(values.get("allow_private_targets", request.app.state.settings.get("allow_private_targets", False))))
    return request.app.state.settings.update(values)


@router.get("/system")
async def system_info(request: Request) -> dict[str, Any]:
    return await request.app.state.system.info()


@router.post("/system/database-check")
async def database_check(request: Request) -> dict[str, str]:
    return {"status": request.app.state.database.check_integrity()}


@router.post("/system/cleanup")
async def cleanup(request: Request) -> dict[str, int]:
    removed = request.app.state.reporting.cleanup(int(request.app.state.settings.get("retention_days", 90)))
    return {"removed_requests": removed}


@router.get("/system/logs")
async def logs(request: Request) -> StreamingResponse:
    path = request.app.state.log_file
    return StreamingResponse(path.open("rb"), media_type="application/x-ndjson", headers={"Content-Disposition": 'attachment; filename="pageload-lab.log"'})
