from __future__ import annotations

from functools import lru_cache
from importlib import import_module
from typing import Any

from app.core.config import Settings, get_settings
from app.tasks.celery_tasks import register_job_parse_task, register_resume_parse_task
from app.tasks.worker_runtime import shutdown_worker_runtime


class QueueRuntimeUnavailable(RuntimeError):
    """Celery cannot run because queue configuration or dependencies are absent."""


def _shutdown_child_runtime(**_: Any) -> None:
    shutdown_worker_runtime()


def create_celery_app(
    settings: Settings | None = None,
    *,
    celery_factory: Any | None = None,
    shutdown_signal: Any | None = None,
) -> Any:
    """Build the Celery app only for configured dispatch or an explicit worker command."""

    resolved_settings = settings or get_settings()
    if resolved_settings.celery_broker_url is None:
        raise QueueRuntimeUnavailable("CELERY_BROKER_URL is required for queue workers")
    if celery_factory is None:
        try:
            celery_factory = import_module("celery").Celery
        except (AttributeError, ImportError) as error:
            raise QueueRuntimeUnavailable(
                "Queue runtime is not installed; install the backend queue extra"
            ) from error

    application = celery_factory(
        "webcv_backend",
        broker=resolved_settings.celery_broker_url.get_secret_value(),
    )
    application.conf.update(
        task_serializer="json",
        accept_content=["json"],
        result_serializer="json",
        enable_utc=True,
        timezone="UTC",
        task_ignore_result=True,
        result_backend=None,
        task_publish_retry=False,
        broker_connection_retry_on_startup=False,
        broker_connection_timeout=5,
        broker_transport_options={
            "socket_connect_timeout": 5,
            "socket_timeout": 5,
            "retry_on_timeout": False,
        },
    )
    register_resume_parse_task(application)
    register_job_parse_task(application)

    shutdown_signals: tuple[Any, ...]
    if shutdown_signal is None:
        try:
            celery_signals = import_module("celery.signals")
            shutdown_signals = (
                celery_signals.worker_process_shutdown,
                celery_signals.worker_shutdown,
            )
        except (AttributeError, ImportError) as error:
            raise QueueRuntimeUnavailable("Celery worker signals are unavailable") from error
    else:
        shutdown_signals = (shutdown_signal,)
    for signal in shutdown_signals:
        signal.connect(
            _shutdown_child_runtime,
            weak=False,
            dispatch_uid="webcv-parse-worker-runtime-shutdown",
        )
    return application


@lru_cache
def get_celery_app() -> Any:
    return create_celery_app()
