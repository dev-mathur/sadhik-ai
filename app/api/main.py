"""FastAPI app: the REST surface in contracts/api.md, backed by SQLite and the deterministic engine.

Run:  .venv/bin/uvicorn api.main:app --port 8000

Environment:
  SADHIK_DB        SQLite path (default api/sadhik.db)
  SADHIK_DATA_DIR  directory holding the uploaded sources + sources.json (default data/out)

`create_app(...)` takes the same settings as arguments (plus an injectable clock) so tests can
build an isolated app; the module-level `app` reads the environment when it first serves.
"""

from __future__ import annotations

import os
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from api.schemas import ConfigBody, DispositionBody, RunBody
from api.service import DEFAULT_DATA_DIR, DEFAULT_DB, ApiError, Clock, Service


def create_app(
    db_path: str | Path | None = None,
    data_dir: str | Path | None = None,
    floors_path: str | Path | None = None,
    seed_config_path: str | Path | None = None,
    clock: Clock | None = None,
) -> FastAPI:
    holder: dict[str, Service] = {}
    guard = threading.Lock()

    def svc() -> Service:
        # Built on first use so importing this module never touches the filesystem.
        if "s" not in holder:
            with guard:
                if "s" not in holder:
                    holder["s"] = Service(
                        db_path or os.environ.get("SADHIK_DB") or DEFAULT_DB,
                        data_dir or os.environ.get("SADHIK_DATA_DIR") or DEFAULT_DATA_DIR,
                        floors_path=floors_path,
                        seed_config_path=seed_config_path,
                        clock=clock,
                    )
        return holder["s"]

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        svc()  # create the schema and seed the first config version at start-up
        yield

    app = FastAPI(title="Sadhik API", version="0.1.0", lifespan=lifespan)
    app.state.service = svc
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"https?://(localhost|127\.0\.0\.1|\[::1\])(:\d+)?",
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ------------------------------------------------------------------ errors
    def envelope(status: int, code: str, message: str) -> JSONResponse:
        return JSONResponse({"error": {"code": code, "message": message}}, status_code=status)

    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(exc.payload(), status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def _invalid(_: Request, exc: RequestValidationError) -> JSONResponse:
        errs = exc.errors()
        first = errs[0] if errs else {}
        loc = [str(p) for p in first.get("loc", ()) if p not in ("body", "query", "path")]
        msg = str(first.get("msg", "invalid request")).removeprefix("Value error, ")
        where = first.get("loc", ("",))[0]
        text = f"{'.'.join(loc)}: {msg}" if loc else msg
        return envelope(400 if where in ("query", "path") else 422, "invalid_request", text)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {404: "not_found", 405: "method_not_allowed"}.get(exc.status_code, f"http_{exc.status_code}")
        return envelope(exc.status_code, code, str(exc.detail) if exc.status_code != 404 else "No such endpoint")

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
        return envelope(500, "internal_error", f"The request could not be completed: {type(exc).__name__}")

    # ------------------------------------------------------------------ routes
    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {"ok": True}

    @app.get("/api/status")
    def status(period: str | None = None, as_of: str | None = None) -> dict[str, Any]:
        return svc().status(period, as_of)

    @app.get("/api/findings")
    def findings(
        severity: str | None = None,
        rule: str | None = None,
        status: str | None = None,
        period: str | None = None,
        domain: str | None = None,
    ) -> dict[str, Any]:
        return svc().list_findings(severity or None, rule or None, status or None, period or None, domain or None)

    @app.get("/api/findings/{finding_id}")
    def finding(finding_id: str) -> dict[str, Any]:
        return svc().get_finding(finding_id)

    @app.get("/api/findings/{finding_id}/evidence")
    def evidence(
        finding_id: str, page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=500)
    ) -> dict[str, Any]:
        return svc().get_evidence(finding_id, page, page_size)

    @app.post("/api/findings/{finding_id}/disposition")
    def disposition(finding_id: str, body: DispositionBody) -> dict[str, Any]:
        return svc().disposition(finding_id, body.disposition, body.reason_code, body.note, body.actor)

    @app.get("/api/rules")
    def rules(domain: str | None = None) -> dict[str, Any]:
        return svc().list_rules(domain or None)

    @app.get("/api/rules/{rule_id}")
    def rule(rule_id: str) -> dict[str, Any]:
        return svc().get_rule(rule_id)

    @app.get("/api/config")
    def config() -> dict[str, Any]:
        return svc().get_config()

    @app.post("/api/config/validate")
    def config_validate(body: ConfigBody) -> dict[str, Any]:
        return svc().validate_config(body.yaml)

    @app.put("/api/config")
    def config_put(body: ConfigBody) -> dict[str, Any]:
        return svc().put_config(body.yaml)

    @app.get("/api/runs")
    def runs() -> dict[str, Any]:
        return svc().list_runs()

    @app.post("/api/runs")
    def run_create(body: RunBody) -> dict[str, Any]:
        return svc().create_run(body.period, body.tier)

    @app.get("/api/runs/{run_id}")
    def run(run_id: str) -> dict[str, Any]:
        return svc().get_run(run_id)

    @app.post("/api/runs/{run_id}/replay")
    def run_replay(run_id: str) -> dict[str, Any]:
        return svc().replay_run(run_id)

    return app


app = create_app()
