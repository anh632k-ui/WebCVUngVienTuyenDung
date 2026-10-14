"""Offline, fail-closed validator for the R3C Phase 3 drain evidence manifest.

This module deliberately imports no application settings, database engine, Celery,
broker, or orchestrator client. It validates an already-collected evidence bundle;
it cannot execute or certify a live drain and cannot authorize reclaim.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

MAX_MANIFEST_BYTES = 1_000_000
CANONICAL_TASKS = {"resume.parse", "job.parse", "match.calculate"}
REQUIRED_LIVE_SOURCE_TYPES = {
    "ORCHESTRATOR",
    "PROCESS_INVENTORY",
    "BROKER",
    "TASK_INSPECT",
    "DATABASE_SUMMARY",
    "CHANGE_CONTROL",
}
SHA256 = r"^[0-9a-f]{64}$"
DIGEST = r"^sha256:[0-9a-f]{64}$"
IDENTITY = r"^[A-Za-z0-9][A-Za-z0-9_.:/@-]{0,199}$"

_FORBIDDEN_KEYS = {
    "access_token",
    "authorization",
    "credential",
    "email",
    "embedding",
    "error_message",
    "password",
    "processing_activation_token",
    "processing_owner_token",
    "profile",
    "raw_content",
    "raw_text",
    "secret",
    "token",
}
_SENSITIVE_VALUE = re.compile(
    r"(?i)(bearer\s+\S+|password\s*[:=]|secret\s*[:=]|token\s*[:=]|"
    r"postgres(?:ql)?://[^/\s:]+:[^@\s]+@|redis://[^/\s:]+:[^@\s]+@|"
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----|AKIA[0-9A-Z]{16}|"
    r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)"
)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Compatibility(StrEnum):
    LEGACY_UNFENCED = "LEGACY_UNFENCED"
    FENCE_AWARE = "FENCE_AWARE"
    UNKNOWN = "UNKNOWN"


class GateStatus(StrEnum):
    BLOCKED = "BLOCKED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    READY_FOR_OPERATOR_REVIEW = "READY_FOR_OPERATOR_REVIEW"


class EvidenceSource(StrictModel):
    source_id: str = Field(pattern=IDENTITY)
    source_type: Literal[
        "ORCHESTRATOR",
        "PROCESS_INVENTORY",
        "BROKER",
        "TASK_INSPECT",
        "DATABASE_SUMMARY",
        "CHANGE_CONTROL",
        "CI",
        "SYNTHETIC_FIXTURE",
    ]
    reference: str = Field(min_length=1, max_length=300)
    collector_identity: str = Field(pattern=IDENTITY)
    artifact_sha256: str = Field(pattern=SHA256)
    captured_at: datetime
    authenticity: Literal["VERIFIED", "MANUAL_ASSERTION", "UNVERIFIED"]


class EnvironmentIdentity(StrictModel):
    environment_name: str = Field(pattern=IDENTITY)
    environment_identity: str = Field(pattern=IDENTITY)
    expected_environment_identity: str = Field(pattern=IDENTITY)
    evidence_kind: Literal["LIVE", "SYNTHETIC_TEST"]


class ApprovedRelease(StrictModel):
    release_id: str = Field(pattern=IDENTITY)
    commit_sha: str = Field(pattern=SHA256)
    artifact_digest: str = Field(pattern=DIGEST)
    review_reference: str = Field(min_length=1, max_length=300)


class DeploymentInstance(StrictModel):
    instance_id: str = Field(pattern=IDENTITY)
    environment_identity: str = Field(pattern=IDENTITY)
    workload_identity: str = Field(pattern=IDENTITY)
    process_kind: Literal[
        "API", "CELERY_PARENT", "CELERY_CHILD", "BACKGROUND", "SCHEDULER", "OTHER_WRITER"
    ]
    task_role: Literal["PUBLISHER", "TERMINAL_WRITER", "BUSINESS_WRITER", "SCHEDULER"]
    release_id: str = Field(pattern=IDENTITY)
    artifact_digest: str = Field(pattern=DIGEST)
    compatibility: Compatibility
    process_state: Literal["RUNNING", "STOPPED", "TERMINATED"]
    queue_subscriptions: list[str] = Field(max_length=3)
    observed_at: datetime
    source_ids: list[str] = Field(min_length=1)

    @field_validator("queue_subscriptions")
    @classmethod
    def validate_subscriptions(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)) or not set(values) <= CANONICAL_TASKS:
            raise ValueError("queue subscriptions must be unique canonical task names")
        return values


class LegacyProcess(StrictModel):
    process_identity: str = Field(pattern=IDENTITY)
    release_id: str = Field(pattern=IDENTITY)
    artifact_digest: str = Field(pattern=DIGEST)
    process_state: Literal["RUNNING", "STOPPED", "TERMINATED"]
    restart_prevented: bool
    observed_at: datetime
    source_ids: list[str] = Field(min_length=1)


class TaskItem(StrictModel):
    task_ref_sha256: str = Field(pattern=SHA256)
    task_name: Literal["resume.parse", "job.parse", "match.calculate"]


class WorkerNodeSnapshot(StrictModel):
    node_identity: str = Field(pattern=IDENTITY)
    deployment_instance_id: str = Field(pattern=IDENTITY)
    reachable: bool
    response_complete: bool
    active: list[TaskItem]
    reserved: list[TaskItem]
    scheduled: list[TaskItem]
    retry_delayed: list[TaskItem]
    unacknowledged: list[TaskItem]
    observed_at: datetime
    source_ids: list[str] = Field(min_length=1)


class QueueRoute(StrictModel):
    task_name: Literal["resume.parse", "job.parse", "match.calculate"]
    exchange: str = Field(min_length=1, max_length=200)
    routing_key: str = Field(min_length=1, max_length=200)
    consumer_instance_ids: list[str] = Field(min_length=1)
    all_consumers_accounted: bool
    pending_delivery_count: Annotated[int, Field(ge=0)]
    dead_letter_count: Annotated[int, Field(ge=0)]
    observed_at: datetime
    source_ids: list[str] = Field(min_length=1)


class RestartPrevention(StrictModel):
    legacy_release_id: str = Field(pattern=IDENTITY)
    replicas_zero: bool
    autoscaling_cannot_launch: bool
    restart_policy_disabled: bool
    supervisors_disabled: bool
    scheduled_launchers_disabled: bool
    rollback_artifacts_blocked: bool
    old_image_references_blocked: bool
    observed_at: datetime
    source_ids: list[str] = Field(min_length=1)


class PublisherState(StrictModel):
    api_publishers_quiesced: bool
    pending_recovery_loops_quiesced: bool
    scheduled_publishers_quiesced: bool
    other_publishers_quiesced: bool
    observed_at: datetime
    source_ids: list[str] = Field(min_length=1)


class ProcessingTableSummary(StrictModel):
    table_name: Literal["resumes", "job_descriptions", "match_results"]
    processing_total: Annotated[int, Field(ge=0)]
    legacy_unowned: Annotated[int, Field(ge=0)]
    fenced_owned: Annotated[int, Field(ge=0)]
    expired_lease: Annotated[int, Field(ge=0)]
    current_lease: Annotated[int, Field(ge=0)]
    inconsistent_metadata: Annotated[int, Field(ge=0)]
    snapshot_invalidated: Annotated[int, Field(ge=0)]
    classified_legacy: Annotated[int, Field(ge=0)]
    operator_live_worker: Annotated[int, Field(ge=0)]
    operator_investigation: Annotated[int, Field(ge=0)]
    operator_approved_recovery_candidate: Annotated[int, Field(ge=0)]
    terminally_resolved_since_capture: Annotated[int, Field(ge=0)]


class DatabaseInventory(StrictModel):
    complete: bool
    schema_readiness_verified: bool
    tables: list[ProcessingTableSummary] = Field(min_length=3, max_length=3)
    captured_at: datetime
    source_ids: list[str] = Field(min_length=1)


class OperatorReview(StrictModel):
    collection_reviewed: bool
    reviewer_identity: str = Field(pattern=IDENTITY)
    review_reference: str = Field(min_length=1, max_length=300)
    reviewed_at: datetime
    final_drain_approval: Literal["NOT_REQUESTED", "PENDING", "APPROVED"]


class ValidationPolicy(StrictModel):
    evaluated_at: datetime
    maximum_evidence_age_seconds: Annotated[int, Field(ge=60, le=86400)]
    maximum_future_skew_seconds: Annotated[int, Field(ge=0, le=300)]
    maximum_cross_source_skew_seconds: Annotated[int, Field(ge=1, le=3600)]


class EvidenceManifest(StrictModel):
    schema_version: Literal["r3c-phase3-v1"]
    environment: EnvironmentIdentity
    approved_release: ApprovedRelease
    captured_at: datetime
    deployments: list[DeploymentInstance] = Field(min_length=1)
    legacy_processes: list[LegacyProcess]
    worker_nodes: list[WorkerNodeSnapshot] = Field(min_length=1)
    queue_routes: list[QueueRoute] = Field(min_length=3)
    restart_prevention: list[RestartPrevention]
    publishers: PublisherState
    database_inventory: DatabaseInventory
    unresolved_items: list[str]
    evidence_sources: list[EvidenceSource] = Field(min_length=1)
    operator_review: OperatorReview
    validation_policy: ValidationPolicy


class GateIssue(StrictModel):
    code: str
    category: Literal["BLOCKER", "REVIEW"]


class GateResult(StrictModel):
    status: GateStatus
    issues: list[GateIssue]
    synthetic_evidence: bool
    disclaimer: str


def _is_aware(value: datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


def _contains_sensitive_content(value: object) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = str(key).casefold()
            if normalized in _FORBIDDEN_KEYS or _contains_sensitive_content(item):
                return True
        return False
    if isinstance(value, list):
        return any(_contains_sensitive_content(item) for item in value)
    return isinstance(value, str) and _SENSITIVE_VALUE.search(value) is not None


def _references_exist(source_ids: list[str], known: set[str]) -> bool:
    return bool(source_ids) and len(source_ids) == len(set(source_ids)) and set(source_ids) <= known


def validate_manifest(manifest: EvidenceManifest) -> GateResult:
    """Evaluate a structurally valid manifest without contacting external systems."""

    blockers: set[str] = set()
    reviews: set[str] = set()
    timestamps: list[datetime] = [manifest.captured_at]

    if (
        manifest.environment.environment_identity
        != manifest.environment.expected_environment_identity
    ):
        blockers.add("ENVIRONMENT_IDENTITY_MISMATCH")

    source_ids = [source.source_id for source in manifest.evidence_sources]
    known_sources = set(source_ids)
    if len(source_ids) != len(known_sources):
        blockers.add("DUPLICATE_EVIDENCE_SOURCE_ID")
    for source in manifest.evidence_sources:
        timestamps.append(source.captured_at)
        if source.authenticity != "VERIFIED":
            reviews.add("EVIDENCE_SOURCE_NOT_VERIFIED")

    approved = manifest.approved_release
    instance_ids = [item.instance_id for item in manifest.deployments]
    if len(instance_ids) != len(set(instance_ids)):
        blockers.add("DUPLICATE_OR_CONFLICTING_INSTANCE_ID")
    for instance in manifest.deployments:
        timestamps.append(instance.observed_at)
        if not _references_exist(instance.source_ids, known_sources):
            blockers.add("DEPLOYMENT_PROVENANCE_MISSING")
        if instance.environment_identity != manifest.environment.environment_identity:
            blockers.add("DEPLOYMENT_ENVIRONMENT_MISMATCH")
        if instance.compatibility is Compatibility.UNKNOWN:
            blockers.add("UNKNOWN_WRITER_VERSION")
        if instance.process_state == "RUNNING":
            if instance.compatibility is Compatibility.LEGACY_UNFENCED:
                blockers.add("LEGACY_WRITER_ACTIVE")
            if (
                instance.release_id != approved.release_id
                or instance.artifact_digest != approved.artifact_digest
            ):
                blockers.add("ACTIVE_ARTIFACT_IDENTITY_MISMATCH")

    for process in manifest.legacy_processes:
        timestamps.append(process.observed_at)
        if not _references_exist(process.source_ids, known_sources):
            blockers.add("LEGACY_PROCESS_PROVENANCE_MISSING")
        if process.process_state == "RUNNING":
            blockers.add("LEGACY_PROCESS_ACTIVE")
        if not process.restart_prevented:
            blockers.add("LEGACY_PROCESS_CAN_RESTART")

    worker_instance_ids: list[str] = []
    worker_node_ids: list[str] = []
    running_worker_ids = {
        item.instance_id
        for item in manifest.deployments
        if item.process_state == "RUNNING" and item.process_kind == "CELERY_PARENT"
    }
    for node in manifest.worker_nodes:
        timestamps.append(node.observed_at)
        worker_node_ids.append(node.node_identity)
        worker_instance_ids.append(node.deployment_instance_id)
        if not _references_exist(node.source_ids, known_sources):
            blockers.add("WORKER_NODE_PROVENANCE_MISSING")
        if not node.reachable:
            blockers.add("WORKER_NODE_UNRESPONSIVE")
        if not node.response_complete:
            blockers.add("WORKER_RESPONSE_INCOMPLETE")
        if node.active:
            blockers.add("ACTIVE_TASKS_NOT_DRAINED")
        if node.reserved or node.scheduled:
            blockers.add("RESERVED_OR_SCHEDULED_TASKS_NOT_DRAINED")
        if node.retry_delayed or node.unacknowledged:
            blockers.add("UNRESOLVED_IN_FLIGHT_TASKS")
    if len(worker_instance_ids) != len(set(worker_instance_ids)):
        blockers.add("DUPLICATE_OR_CONFLICTING_WORKER_IDENTITY")
    if len(worker_node_ids) != len(set(worker_node_ids)):
        blockers.add("DUPLICATE_OR_CONFLICTING_WORKER_IDENTITY")
    if running_worker_ids != set(worker_instance_ids):
        blockers.add("WORKER_NODE_INVENTORY_INCOMPLETE")

    routes_by_task = Counter(route.task_name for route in manifest.queue_routes)
    if set(routes_by_task) != CANONICAL_TASKS or any(
        count != 1 for count in routes_by_task.values()
    ):
        blockers.add("QUEUE_ROUTE_INVENTORY_INCOMPLETE")
    deployments_by_id = {item.instance_id: item for item in manifest.deployments}
    routed_consumers: dict[str, set[str]] = {}
    for route in manifest.queue_routes:
        timestamps.append(route.observed_at)
        routed_consumers[route.task_name] = set(route.consumer_instance_ids)
        if not _references_exist(route.source_ids, known_sources):
            blockers.add("QUEUE_ROUTE_PROVENANCE_MISSING")
        if not route.all_consumers_accounted:
            blockers.add("QUEUE_CONSUMER_INVENTORY_INCOMPLETE")
        if route.pending_delivery_count or route.dead_letter_count:
            blockers.add("BROKER_DELIVERIES_NOT_DRAINED")
        for consumer_id in route.consumer_instance_ids:
            consumer = deployments_by_id.get(consumer_id)
            if consumer is None:
                blockers.add("UNKNOWN_QUEUE_CONSUMER")
            elif (
                consumer.compatibility is not Compatibility.FENCE_AWARE
                or consumer.process_state != "RUNNING"
                or route.task_name not in consumer.queue_subscriptions
            ):
                blockers.add("QUEUE_ROUTES_TO_NON_FENCE_AWARE_CONSUMER")
    for task_name in CANONICAL_TASKS:
        expected_consumers = {
            item.instance_id
            for item in manifest.deployments
            if item.process_state == "RUNNING" and task_name in item.queue_subscriptions
        }
        if routed_consumers.get(task_name, set()) != expected_consumers:
            blockers.add("QUEUE_AND_DEPLOYMENT_CONSUMERS_CONFLICT")

    if not manifest.restart_prevention:
        blockers.add("RESTART_PREVENTION_EVIDENCE_MISSING")
    legacy_releases = {item.release_id for item in manifest.legacy_processes}
    guarded_releases = {item.legacy_release_id for item in manifest.restart_prevention}
    if legacy_releases != guarded_releases:
        blockers.add("RESTART_PREVENTION_INVENTORY_INCOMPLETE")
    for item in manifest.restart_prevention:
        timestamps.append(item.observed_at)
        if not _references_exist(item.source_ids, known_sources):
            blockers.add("RESTART_PREVENTION_PROVENANCE_MISSING")
        if not all(
            (
                item.replicas_zero,
                item.autoscaling_cannot_launch,
                item.restart_policy_disabled,
                item.supervisors_disabled,
                item.scheduled_launchers_disabled,
                item.rollback_artifacts_blocked,
                item.old_image_references_blocked,
            )
        ):
            blockers.add("LEGACY_DEPLOYMENT_CAN_RESTART")

    publisher = manifest.publishers
    timestamps.append(publisher.observed_at)
    if not _references_exist(publisher.source_ids, known_sources):
        blockers.add("PUBLISHER_PROVENANCE_MISSING")
    if not publisher.api_publishers_quiesced:
        blockers.add("API_PUBLISHERS_NOT_QUIESCED")
    if not publisher.pending_recovery_loops_quiesced:
        blockers.add("PENDING_RECOVERY_LOOPS_NOT_QUIESCED")
    if not publisher.scheduled_publishers_quiesced or not publisher.other_publishers_quiesced:
        blockers.add("OTHER_PUBLISHERS_NOT_QUIESCED")

    inventory = manifest.database_inventory
    timestamps.append(inventory.captured_at)
    if not _references_exist(inventory.source_ids, known_sources):
        blockers.add("DATABASE_INVENTORY_PROVENANCE_MISSING")
    if not inventory.complete or not inventory.schema_readiness_verified:
        blockers.add("DATABASE_INVENTORY_INCOMPLETE")
    table_names = [table.table_name for table in inventory.tables]
    if set(table_names) != {"resumes", "job_descriptions", "match_results"} or len(
        table_names
    ) != len(set(table_names)):
        blockers.add("DATABASE_TABLE_INVENTORY_INCOMPLETE")
    for table in inventory.tables:
        if table.processing_total != table.legacy_unowned + table.fenced_owned:
            blockers.add("DATABASE_PROCESSING_COUNTS_CONFLICT")
        if table.fenced_owned != table.expired_lease + table.current_lease:
            blockers.add("DATABASE_LEASE_COUNTS_CONFLICT")
        if table.inconsistent_metadata:
            blockers.add("DATABASE_OWNERSHIP_METADATA_INCONSISTENT")
        if table.snapshot_invalidated:
            blockers.add("DATABASE_PROCESSING_SNAPSHOT_INVALIDATED")
        classified = (
            table.operator_live_worker
            + table.operator_investigation
            + table.operator_approved_recovery_candidate
            + table.terminally_resolved_since_capture
        )
        if table.classified_legacy != table.legacy_unowned or classified != table.legacy_unowned:
            blockers.add("LEGACY_PROCESSING_UNCLASSIFIED")
        if table.operator_live_worker:
            blockers.add("LEGACY_PROCESSING_HAS_LIVE_OWNER")
        if table.operator_investigation:
            blockers.add("LEGACY_PROCESSING_REQUIRES_INVESTIGATION")

    if manifest.unresolved_items:
        blockers.add("UNRESOLVED_ITEMS_PRESENT")

    review = manifest.operator_review
    timestamps.append(review.reviewed_at)
    if not review.collection_reviewed:
        reviews.add("OPERATOR_COLLECTION_REVIEW_MISSING")
    if review.final_drain_approval == "NOT_REQUESTED":
        reviews.add("OPERATOR_REVIEW_NOT_REQUESTED")
    if not review.review_reference.strip():
        reviews.add("OPERATOR_REVIEW_PROVENANCE_MISSING")

    policy = manifest.validation_policy
    evaluated_at = policy.evaluated_at.astimezone(UTC)
    if any(not _is_aware(item) for item in timestamps) or not _is_aware(policy.evaluated_at):
        blockers.add("TIMESTAMP_WITHOUT_TIMEZONE")
    else:
        utc_times = [item.astimezone(UTC) for item in timestamps]
        future_limit = policy.maximum_future_skew_seconds
        if any((item - evaluated_at).total_seconds() > future_limit for item in utc_times):
            blockers.add("EVIDENCE_TIMESTAMP_IN_FUTURE")
        if any(
            (evaluated_at - item).total_seconds() > policy.maximum_evidence_age_seconds
            for item in utc_times
        ):
            blockers.add("EVIDENCE_EXPIRED")
        if (max(utc_times) - min(utc_times)).total_seconds() > (
            policy.maximum_cross_source_skew_seconds
        ):
            blockers.add("EVIDENCE_TIMESTAMPS_NOT_COHERENT")

    source_types = {source.source_type for source in manifest.evidence_sources}
    synthetic = manifest.environment.evidence_kind == "SYNTHETIC_TEST"
    if not synthetic and not source_types >= REQUIRED_LIVE_SOURCE_TYPES:
        blockers.add("LIVE_EVIDENCE_SOURCES_INCOMPLETE")
    if not synthetic and source_types <= {"CI"}:
        blockers.add("CI_IS_NOT_LIVE_DRAIN_EVIDENCE")

    issues = [GateIssue(code=code, category="BLOCKER") for code in sorted(blockers)]
    issues.extend(GateIssue(code=code, category="REVIEW") for code in sorted(reviews))
    if blockers:
        status = GateStatus.BLOCKED
    elif reviews:
        status = GateStatus.REVIEW_REQUIRED
    else:
        status = GateStatus.READY_FOR_OPERATOR_REVIEW
    return GateResult(
        status=status,
        issues=issues,
        synthetic_evidence=synthetic,
        disclaimer=(
            "Offline validation only; this result does not certify a live drain "
            "or authorize reclaim."
        ),
    )


def load_and_validate(path: Path) -> GateResult:
    """Load one bounded JSON file and return a sanitized result."""

    try:
        if path.stat().st_size > MAX_MANIFEST_BYTES:
            raise ValueError("manifest too large")
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        return _blocked("MANIFEST_UNREADABLE_OR_INVALID_JSON")
    if _contains_sensitive_content(payload):
        return _blocked("SENSITIVE_CONTENT_DETECTED")
    try:
        manifest = EvidenceManifest.model_validate_json(json.dumps(payload))
    except ValidationError:
        return _blocked("MANIFEST_STRUCTURE_INVALID")
    return validate_manifest(manifest)


def _blocked(code: str) -> GateResult:
    return GateResult(
        status=GateStatus.BLOCKED,
        issues=[GateIssue(code=code, category="BLOCKER")],
        synthetic_evidence=False,
        disclaimer=(
            "Offline validation only; this result does not certify a live drain "
            "or authorize reclaim."
        ),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    args = parser.parse_args(argv)
    result = load_and_validate(args.manifest)
    print(json.dumps(result.model_dump(mode="json"), sort_keys=True))
    return 0 if result.status is GateStatus.READY_FOR_OPERATOR_REVIEW else 2


if __name__ == "__main__":
    sys.exit(main())
