from __future__ import annotations

import uuid

APP_IDEMPOTENCY_NAMESPACE = uuid.UUID("bd7b1f30-b2de-549c-a8dd-8d742ee5bc12")
RESUME_UPLOAD_ROUTE = "/api/v1/resumes/upload"
JOB_CREATE_ROUTE = "/api/v1/jobs"


def derive_idempotent_resource_id(
    actor_id: uuid.UUID,
    canonical_route: str,
    idempotency_key: uuid.UUID,
) -> uuid.UUID:
    name = f"{actor_id}\n{canonical_route}\n{str(idempotency_key).lower()}"
    return uuid.uuid5(APP_IDEMPOTENCY_NAMESPACE, name)
