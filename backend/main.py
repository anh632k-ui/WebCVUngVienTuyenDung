from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.api.v1.router import api_router
from app.core.config import get_settings
from app.core.database import SessionFactory, dispose_engine
from app.core.exceptions import APIError
from app.core.logging import configure_logging
from app.services.job_recovery import start_job_recovery, stop_job_recovery
from app.services.match_recovery import start_match_recovery, stop_match_recovery
from app.services.resume_recovery import start_resume_recovery, stop_resume_recovery

settings = get_settings()
configure_logging(settings)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    logger.info("application_started environment=%s", settings.app_env)
    resume_recovery_task = start_resume_recovery(settings, SessionFactory)
    job_recovery_task = start_job_recovery(settings, SessionFactory)
    match_recovery_task = start_match_recovery(settings, SessionFactory)
    try:
        yield
    finally:
        await stop_resume_recovery(resume_recovery_task)
        await stop_job_recovery(job_recovery_task)
        await stop_match_recovery(match_recovery_task)
        await dispose_engine()
        logger.info("application_stopped")


def create_app() -> FastAPI:
    application = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        lifespan=lifespan,
    )
    application.include_router(api_router, prefix="/api/v1")

    @application.exception_handler(APIError)
    async def api_exception_handler(_: Request, exc: APIError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "success": False,
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    "details": exc.details,
                },
            },
        )

    @application.exception_handler(RequestValidationError)
    async def validation_exception_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
        safe_errors = [
            {key: value for key, value in error.items() if key not in {"input", "ctx"}}
            for error in exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            content=jsonable_encoder(
                {
                    "success": False,
                    "error": {
                        "code": "VALIDATION_ERROR",
                        "message": "Request validation failed",
                        "details": safe_errors,
                    },
                }
            ),
        )

    @application.exception_handler(Exception)
    async def unhandled_exception_handler(_: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled_request_error", exc_info=exc)
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "error": {
                    "code": "INTERNAL_SERVER_ERROR",
                    "message": "An unexpected error occurred",
                    "details": None,
                },
            },
        )

    return application


app = create_app()
