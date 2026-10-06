"""Explicit Celery CLI entry point; not imported during ordinary API startup."""

from app.tasks.celery_app import create_celery_app

app = create_celery_app()
