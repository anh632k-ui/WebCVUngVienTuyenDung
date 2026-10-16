from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock
from urllib.parse import urlsplit

import asyncpg
import pytest

from scripts.r3c_phase3_drain_evidence import (
    EvidenceManifest,
    GateStatus,
    load_and_validate,
    validate_manifest,
)

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = BACKEND_DIR.parent
EXAMPLE_MANIFEST = (
    REPOSITORY_ROOT / "docs" / "09_delivery" / "task6c_r3c_phase3_evidence_manifest.example.json"
)
INVENTORY_SQL = (
    REPOSITORY_ROOT / "docs" / "09_delivery" / "task6c_r3c_phase3_processing_inventory.sql"
)
VALIDATOR_SOURCE = BACKEND_DIR / "scripts" / "r3c_phase3_drain_evidence.py"
NOW = "2026-10-14T02:05:00Z"
CAPTURED = "2026-10-14T02:00:00Z"
FROZEN_NOW = datetime(2026, 10, 14, 2, 5, tzinfo=UTC)
GIT_HASH = "a" * 40
HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64
PHASE3_CI_DATABASE_NAME = "r3c_phase3_inventory"
PHASE3_CI_DATABASE_USER = "r3c_phase3_ci"
SAFE_PHASE3_TEST_DSN = "postgresql://r3c_phase3_ci:placeholder@127.0.0.1:5432/r3c_phase3_inventory"


def _source_ids() -> list[str]:
    return ["synthetic-fixture"]


def valid_manifest() -> dict[str, Any]:
    deployments = [
        {
            "instance_id": "api-1",
            "environment_identity": "phase3-test-env",
            "workload_identity": "synthetic/api-1",
            "process_kind": "API",
            "task_role": "PUBLISHER",
            "release_id": "release-r3c-p2",
            "artifact_digest": f"sha256:{HASH_B}",
            "compatibility": "FENCE_AWARE",
            "process_state": "RUNNING",
            "queue_subscriptions": [],
            "observed_at": CAPTURED,
            "source_ids": _source_ids(),
        },
        {
            "instance_id": "worker-parent-1",
            "environment_identity": "phase3-test-env",
            "workload_identity": "synthetic/worker-1/parent",
            "process_kind": "CELERY_PARENT",
            "task_role": "TERMINAL_WRITER",
            "release_id": "release-r3c-p2",
            "artifact_digest": f"sha256:{HASH_B}",
            "compatibility": "FENCE_AWARE",
            "process_state": "RUNNING",
            "queue_subscriptions": ["resume.parse", "job.parse", "match.calculate"],
            "observed_at": CAPTURED,
            "source_ids": _source_ids(),
        },
        {
            "instance_id": "worker-child-1",
            "environment_identity": "phase3-test-env",
            "workload_identity": "synthetic/worker-1/child-1",
            "process_kind": "CELERY_CHILD",
            "task_role": "TERMINAL_WRITER",
            "release_id": "release-r3c-p2",
            "artifact_digest": f"sha256:{HASH_B}",
            "compatibility": "FENCE_AWARE",
            "process_state": "RUNNING",
            "queue_subscriptions": [],
            "observed_at": CAPTURED,
            "source_ids": _source_ids(),
        },
    ]
    table = {
        "processing_total": 0,
        "legacy_unowned": 0,
        "fenced_owned": 0,
        "expired_lease": 0,
        "current_lease": 0,
        "inconsistent_metadata": 0,
        "snapshot_invalidated": 0,
        "classified_legacy": 0,
        "operator_live_worker": 0,
        "operator_investigation": 0,
        "operator_approved_recovery_candidate": 0,
        "terminally_resolved_since_capture": 0,
    }
    return {
        "schema_version": "r3c-phase3-v1",
        "environment": {
            "environment_name": "synthetic-phase3-test",
            "environment_identity": "phase3-test-env",
            "expected_environment_identity": "phase3-test-env",
            "evidence_kind": "SYNTHETIC_TEST",
        },
        "approved_release": {
            "release_id": "release-r3c-p2",
            "commit_sha": GIT_HASH,
            "artifact_digest": f"sha256:{HASH_B}",
            "review_reference": "synthetic-review/r3c-phase2",
            "source_ids": _source_ids(),
        },
        "captured_at": CAPTURED,
        "deployments": deployments,
        "legacy_processes": [
            {
                "process_identity": "legacy-worker-archived-1",
                "release_id": "release-legacy",
                "artifact_digest": f"sha256:{HASH_C}",
                "process_state": "TERMINATED",
                "restart_prevented": True,
                "observed_at": CAPTURED,
                "source_ids": _source_ids(),
            }
        ],
        "worker_nodes": [
            {
                "node_identity": "celery-node-1",
                "deployment_instance_id": "worker-parent-1",
                "reachable": True,
                "response_complete": True,
                "active": [],
                "reserved": [],
                "scheduled": [],
                "retry_delayed": [],
                "unacknowledged": [],
                "observed_at": CAPTURED,
                "source_ids": _source_ids(),
            }
        ],
        "queue_routes": [
            {
                "task_name": task,
                "exchange": "synthetic-exchange",
                "routing_key": task,
                "consumer_instance_ids": ["worker-parent-1"],
                "all_consumers_accounted": True,
                "pending_delivery_count": 0,
                "dead_letter_count": 0,
                "observed_at": CAPTURED,
                "source_ids": _source_ids(),
            }
            for task in ("resume.parse", "job.parse", "match.calculate")
        ],
        "restart_prevention": [
            {
                "legacy_release_id": "release-legacy",
                "replicas_zero": True,
                "autoscaling_cannot_launch": True,
                "restart_policy_disabled": True,
                "supervisors_disabled": True,
                "scheduled_launchers_disabled": True,
                "rollback_artifacts_blocked": True,
                "old_image_references_blocked": True,
                "observed_at": CAPTURED,
                "source_ids": _source_ids(),
            }
        ],
        "publishers": {
            "api_publishers_quiesced": True,
            "pending_recovery_loops_quiesced": True,
            "scheduled_publishers_quiesced": True,
            "other_publishers_quiesced": True,
            "observed_at": CAPTURED,
            "source_ids": _source_ids(),
        },
        "database_inventory": {
            "complete": True,
            "schema_readiness_verified": True,
            "tables": [
                {"table_name": "resumes", **table},
                {"table_name": "job_descriptions", **table},
                {"table_name": "match_results", **table},
            ],
            "captured_at": CAPTURED,
            "source_ids": _source_ids(),
        },
        "unresolved_items": [],
        "evidence_sources": [
            {
                "source_id": "synthetic-fixture",
                "source_type": "SYNTHETIC_FIXTURE",
                "reference": "repository synthetic fixture",
                "collector_identity": "ci-test-runner",
                "environment_identity": "phase3-test-env",
                "artifact_sha256": HASH_A,
                "captured_at": CAPTURED,
                "authenticity": "VERIFIED",
            }
        ],
        "operator_review": {
            "collection_reviewed": True,
            "reviewer_identity": "synthetic-test-reviewer",
            "review_reference": "synthetic-review-only",
            "reviewed_at": CAPTURED,
            "final_drain_approval": "PENDING",
            "source_ids": _source_ids(),
        },
        "validation_policy": {
            "evaluated_at": NOW,
            "maximum_evidence_age_seconds": 3600,
            "maximum_future_skew_seconds": 60,
            "maximum_evaluation_clock_skew_seconds": 60,
            "maximum_cross_source_skew_seconds": 600,
        },
    }


def live_manifest() -> dict[str, Any]:
    payload = valid_manifest()
    payload["environment"].update(
        environment_name="production",
        environment_identity="prod-a",
        expected_environment_identity="prod-a",
        evidence_kind="LIVE",
    )
    source_types = {
        "orchestrator": "ORCHESTRATOR",
        "process-inventory": "PROCESS_INVENTORY",
        "broker": "BROKER",
        "task-inspect": "TASK_INSPECT",
        "database-summary": "DATABASE_SUMMARY",
        "change-control": "CHANGE_CONTROL",
    }
    payload["evidence_sources"] = [
        {
            "source_id": source_id,
            "source_type": source_type,
            "reference": f"live-artifact/{source_id}",
            "collector_identity": "operator-collector",
            "environment_identity": "prod-a",
            "artifact_sha256": HASH_A,
            "captured_at": CAPTURED,
            "authenticity": "VERIFIED",
        }
        for source_id, source_type in source_types.items()
    ]
    payload["approved_release"]["source_ids"] = ["change-control"]
    for deployment in payload["deployments"]:
        deployment["environment_identity"] = "prod-a"
        deployment["source_ids"] = ["orchestrator", "process-inventory"]
    payload["legacy_processes"][0]["source_ids"] = ["orchestrator", "process-inventory"]
    payload["worker_nodes"][0]["source_ids"] = ["task-inspect"]
    for route in payload["queue_routes"]:
        route["source_ids"] = ["broker"]
    payload["restart_prevention"][0]["source_ids"] = ["orchestrator", "change-control"]
    payload["publishers"]["source_ids"] = ["process-inventory", "change-control"]
    payload["database_inventory"]["source_ids"] = ["database-summary"]
    payload["operator_review"]["source_ids"] = ["change-control"]
    return payload


def evaluate(payload: dict[str, Any]):
    manifest = EvidenceManifest.model_validate_json(json.dumps(payload))
    return validate_manifest(manifest, clock=lambda: FROZEN_NOW)


def issue_codes(result: Any) -> set[str]:
    return {issue.code for issue in result.issues}


def _validated_phase3_ci_dsn() -> str:
    if os.environ.get("R3C_PHASE3_POSTGRES_INTEGRATION") != "true":
        pytest.skip("Phase 3 PostgreSQL integration is not enabled")
    if not (
        os.environ.get("CI") == "true"
        and os.environ.get("GITHUB_ACTIONS") == "true"
        and os.environ.get("RUNNER_ENVIRONMENT") == "github-hosted"
    ):
        pytest.skip("Phase 3 PostgreSQL integration requires a GitHub-hosted runner")

    dsn = os.environ.get("R3C_PHASE3_DATABASE_URL")
    try:
        parsed = urlsplit(dsn or "")
        port = parsed.port
    except ValueError:
        pytest.fail("Phase 3 CI database target failed safety validation", pytrace=False)

    valid = (
        parsed.scheme == "postgresql"
        and parsed.hostname == "127.0.0.1"
        and port == 5432
        and parsed.username == PHASE3_CI_DATABASE_USER
        and bool(parsed.password)
        and parsed.path == f"/{PHASE3_CI_DATABASE_NAME}"
        and not parsed.query
        and not parsed.fragment
    )
    if not valid:
        pytest.fail("Phase 3 CI database target failed safety validation", pytrace=False)
    return dsn or ""


async def _connect_phase3_ci_database() -> asyncpg.Connection:
    dsn = _validated_phase3_ci_dsn()
    return await asyncpg.connect(dsn)


def _set_phase3_ci_identity(monkeypatch: pytest.MonkeyPatch, dsn: str) -> None:
    monkeypatch.setenv("R3C_PHASE3_POSTGRES_INTEGRATION", "true")
    monkeypatch.setenv("CI", "true")
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("RUNNER_ENVIRONMENT", "github-hosted")
    monkeypatch.setenv("R3C_PHASE3_DATABASE_URL", dsn)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "unsafe_dsn",
    [
        "postgresql://r3c_phase3_ci:placeholder@127.0.0.1:5432/webcv_ungvien",
        "postgresql://r3c_phase3_ci:placeholder@127.0.0.1:5432/webcv_task6b_clean",
        "postgresql://r3c_phase3_ci:placeholder@127.0.0.1:55432/r3c_phase3_inventory",
        "postgresql://r3c_phase3_ci:placeholder@db.example:5432/r3c_phase3_inventory",
        "postgresql://different_user:placeholder@127.0.0.1:5432/r3c_phase3_inventory",
        "postgresql://r3c_phase3_ci:placeholder@127.0.0.1:5432/different_database",
        "postgresql://r3c_phase3_ci:placeholder@127.0.0.1:5432/"
        "r3c_phase3_inventory?sslmode=require",
        "postgresql://r3c_phase3_ci:placeholder@127.0.0.1:not-a-port/r3c_phase3_inventory",
    ],
)
async def test_phase3_ci_database_guard_rejects_unsafe_target_before_connect(
    monkeypatch: pytest.MonkeyPatch, unsafe_dsn: str
) -> None:
    _set_phase3_ci_identity(monkeypatch, unsafe_dsn)
    connect = AsyncMock()
    monkeypatch.setattr(asyncpg, "connect", connect)

    with pytest.raises(pytest.fail.Exception, match="safety validation"):
        await _connect_phase3_ci_database()

    connect.assert_not_awaited()


@pytest.mark.asyncio
async def test_phase3_ci_database_guard_skips_without_github_identity_before_connect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_phase3_ci_identity(monkeypatch, SAFE_PHASE3_TEST_DSN)
    monkeypatch.delenv("GITHUB_ACTIONS")
    connect = AsyncMock()
    monkeypatch.setattr(asyncpg, "connect", connect)

    with pytest.raises(pytest.skip.Exception, match="GitHub-hosted"):
        await _connect_phase3_ci_database()

    connect.assert_not_awaited()


@pytest.mark.asyncio
async def test_phase3_ci_database_guard_fails_missing_target_before_connect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_phase3_ci_identity(monkeypatch, SAFE_PHASE3_TEST_DSN)
    monkeypatch.delenv("R3C_PHASE3_DATABASE_URL")
    connect = AsyncMock()
    monkeypatch.setattr(asyncpg, "connect", connect)

    with pytest.raises(pytest.fail.Exception, match="safety validation"):
        await _connect_phase3_ci_database()

    connect.assert_not_awaited()


def test_complete_synthetic_manifest_is_ready_only_for_operator_review() -> None:
    result = evaluate(valid_manifest())

    assert result.status is GateStatus.READY_FOR_OPERATOR_REVIEW
    assert result.synthetic_evidence is True
    assert "authorize reclaim" in result.disclaimer


def test_old_manifest_replay_is_blocked_by_trusted_clock() -> None:
    manifest = EvidenceManifest.model_validate_json(json.dumps(valid_manifest()))
    result = validate_manifest(manifest, clock=lambda: FROZEN_NOW + timedelta(days=1))
    assert result.status is GateStatus.BLOCKED
    assert "EVIDENCE_EXPIRED" in issue_codes(result)


@pytest.mark.parametrize("offset", [-3600, 3600])
def test_manifest_cannot_select_past_or_future_evaluation_time(offset: int) -> None:
    payload = valid_manifest()
    payload["validation_policy"]["evaluated_at"] = (
        FROZEN_NOW + timedelta(seconds=offset)
    ).isoformat()
    result = evaluate(payload)
    assert result.status is GateStatus.BLOCKED
    assert "DECLARED_EVALUATION_TIME_MISMATCH" in issue_codes(result)


def test_stale_but_internally_coherent_evidence_is_blocked() -> None:
    payload = valid_manifest()
    stale = (FROZEN_NOW - timedelta(hours=2)).isoformat()
    for key, container in _walk_dicts(payload):
        if key in {"captured_at", "observed_at", "reviewed_at"}:
            container[key] = stale
    result = evaluate(payload)
    assert result.status is GateStatus.BLOCKED
    assert "EVIDENCE_EXPIRED" in issue_codes(result)
    assert "EVIDENCE_TIMESTAMPS_NOT_COHERENT" not in issue_codes(result)


def test_timezone_missing_is_blocked() -> None:
    payload = valid_manifest()
    payload["captured_at"] = "2026-10-14T02:00:00"
    result = evaluate(payload)
    assert result.status is GateStatus.BLOCKED
    assert "TIMESTAMP_WITHOUT_TIMEZONE" in issue_codes(result)


@pytest.mark.parametrize("commit_sha", [HASH_A, "g" * 40, "a" * 39])
def test_release_commit_requires_full_40_hex_git_sha(commit_sha: str) -> None:
    payload = valid_manifest()
    payload["approved_release"]["commit_sha"] = commit_sha
    with pytest.raises(ValueError):
        EvidenceManifest.model_validate_json(json.dumps(payload))


@pytest.mark.parametrize(
    ("mutate", "expected_code"),
    [
        (
            lambda d: d["legacy_processes"][0].update(process_state="RUNNING"),
            "LEGACY_PROCESS_ACTIVE",
        ),
        (lambda d: d["deployments"][1].update(compatibility="UNKNOWN"), "UNKNOWN_WRITER_VERSION"),
        (
            lambda d: d["legacy_processes"][0].update(restart_prevented=False),
            "LEGACY_PROCESS_CAN_RESTART",
        ),
        (
            lambda d: d["restart_prevention"][0].update(autoscaling_cannot_launch=False),
            "LEGACY_DEPLOYMENT_CAN_RESTART",
        ),
        (
            lambda d: d["worker_nodes"][0]["active"].append(
                {"task_ref_sha256": HASH_A, "task_name": "resume.parse"}
            ),
            "ACTIVE_TASKS_NOT_DRAINED",
        ),
        (
            lambda d: d["worker_nodes"][0]["reserved"].append(
                {"task_ref_sha256": HASH_A, "task_name": "job.parse"}
            ),
            "RESERVED_OR_SCHEDULED_TASKS_NOT_DRAINED",
        ),
        (
            lambda d: d["worker_nodes"][0]["scheduled"].append(
                {"task_ref_sha256": HASH_A, "task_name": "match.calculate"}
            ),
            "RESERVED_OR_SCHEDULED_TASKS_NOT_DRAINED",
        ),
        (
            lambda d: d["queue_routes"][0].update(consumer_instance_ids=["worker-child-1"]),
            "QUEUE_ROUTES_TO_NON_FENCE_AWARE_CONSUMER",
        ),
        (
            lambda d: d["deployments"].append(
                {**copy.deepcopy(d["deployments"][1]), "instance_id": "worker-parent-2"}
            ),
            "WORKER_NODE_INVENTORY_INCOMPLETE",
        ),
        (lambda d: d["worker_nodes"][0].update(reachable=False), "WORKER_NODE_UNRESPONSIVE"),
        (
            lambda d: d["publishers"].update(api_publishers_quiesced=False),
            "API_PUBLISHERS_NOT_QUIESCED",
        ),
        (
            lambda d: d["publishers"].update(pending_recovery_loops_quiesced=False),
            "PENDING_RECOVERY_LOOPS_NOT_QUIESCED",
        ),
        (lambda d: d["database_inventory"].update(complete=False), "DATABASE_INVENTORY_INCOMPLETE"),
        (
            lambda d: d["database_inventory"]["tables"][0].update(
                processing_total=1, legacy_unowned=1
            ),
            "LEGACY_PROCESSING_UNCLASSIFIED",
        ),
        (
            lambda d: d["database_inventory"]["tables"][2].update(snapshot_invalidated=1),
            "DATABASE_PROCESSING_SNAPSHOT_INVALIDATED",
        ),
        (
            lambda d: d["evidence_sources"][0].update(captured_at="2026-10-14T00:00:00Z"),
            "EVIDENCE_TIMESTAMPS_NOT_COHERENT",
        ),
        (
            lambda d: d["validation_policy"].update(evaluated_at="2026-10-15T02:05:00Z"),
            "DECLARED_EVALUATION_TIME_MISMATCH",
        ),
        (
            lambda d: d["environment"].update(expected_environment_identity="different-env"),
            "ENVIRONMENT_IDENTITY_MISMATCH",
        ),
        (
            lambda d: d["deployments"][0].update(artifact_digest=f"sha256:{HASH_C}"),
            "ACTIVE_ARTIFACT_IDENTITY_MISMATCH",
        ),
        (
            lambda d: d["deployments"].append(copy.deepcopy(d["deployments"][0])),
            "DUPLICATE_OR_CONFLICTING_INSTANCE_ID",
        ),
        (
            lambda d: d["worker_nodes"].append(copy.deepcopy(d["worker_nodes"][0])),
            "DUPLICATE_OR_CONFLICTING_WORKER_IDENTITY",
        ),
        (
            lambda d: d["queue_routes"][0].update(pending_delivery_count=1),
            "BROKER_DELIVERIES_NOT_DRAINED",
        ),
        (
            lambda d: d.update(unresolved_items=["operator investigation open"]),
            "UNRESOLVED_ITEMS_PRESENT",
        ),
    ],
)
def test_unsafe_or_incomplete_evidence_fails_closed(mutate: Any, expected_code: str) -> None:
    payload = valid_manifest()
    mutate(payload)

    result = evaluate(payload)

    assert result.status is GateStatus.BLOCKED
    assert expected_code in issue_codes(result)


def test_unverified_provenance_requires_review() -> None:
    payload = valid_manifest()
    payload["evidence_sources"][0]["authenticity"] = "MANUAL_ASSERTION"
    result = evaluate(payload)
    assert result.status is GateStatus.REVIEW_REQUIRED
    assert "EVIDENCE_SOURCE_NOT_VERIFIED" in issue_codes(result)


def test_live_self_asserted_verified_hash_still_requires_independent_review() -> None:
    result = evaluate(live_manifest())
    assert result.status is GateStatus.REVIEW_REQUIRED
    assert "INDEPENDENT_PROVENANCE_REVIEW_REQUIRED" in issue_codes(result)


def test_live_evidence_cannot_reference_synthetic_fixture() -> None:
    payload = live_manifest()
    payload["evidence_sources"][0]["source_type"] = "SYNTHETIC_FIXTURE"
    result = evaluate(payload)
    assert result.status is GateStatus.BLOCKED
    assert "SYNTHETIC_SOURCE_FOR_LIVE_EVIDENCE" in issue_codes(result)


def test_database_inventory_cannot_be_proved_by_ci_source() -> None:
    payload = live_manifest()
    database_source = next(
        source
        for source in payload["evidence_sources"]
        if source["source_id"] == "database-summary"
    )
    database_source["source_type"] = "CI"
    result = evaluate(payload)
    assert result.status is GateStatus.BLOCKED
    assert "DATABASE_INVENTORY_SOURCE_TYPE_INVALID" in issue_codes(result)


def test_broker_route_cannot_be_proved_by_process_inventory() -> None:
    payload = live_manifest()
    broker_source = next(
        source for source in payload["evidence_sources"] if source["source_id"] == "broker"
    )
    broker_source["source_type"] = "PROCESS_INVENTORY"
    result = evaluate(payload)
    assert result.status is GateStatus.BLOCKED
    assert "QUEUE_ROUTE_SOURCE_TYPE_INVALID" in issue_codes(result)


def test_unreferenced_required_source_types_do_not_satisfy_live_provenance() -> None:
    payload = valid_manifest()
    for source_type in (
        "ORCHESTRATOR",
        "PROCESS_INVENTORY",
        "BROKER",
        "TASK_INSPECT",
        "DATABASE_SUMMARY",
        "CHANGE_CONTROL",
    ):
        payload["evidence_sources"].append(
            {
                **payload["evidence_sources"][0],
                "source_id": source_type.casefold().replace("_", "-"),
                "source_type": source_type,
            }
        )
    result = evaluate(payload)
    assert result.status is GateStatus.BLOCKED
    assert "UNUSED_EVIDENCE_SOURCE" in issue_codes(result)


def test_source_environment_mismatch_is_blocked() -> None:
    payload = live_manifest()
    payload["evidence_sources"][0]["environment_identity"] = "different-env"
    result = evaluate(payload)
    assert result.status is GateStatus.BLOCKED
    assert "EVIDENCE_SOURCE_ENVIRONMENT_MISMATCH" in issue_codes(result)


def test_duplicate_source_id_is_blocked() -> None:
    payload = live_manifest()
    payload["evidence_sources"].append(copy.deepcopy(payload["evidence_sources"][0]))
    result = evaluate(payload)
    assert result.status is GateStatus.BLOCKED
    assert "DUPLICATE_EVIDENCE_SOURCE_ID" in issue_codes(result)


def test_bad_source_id_is_structurally_rejected(tmp_path: Path) -> None:
    payload = valid_manifest()
    payload["evidence_sources"][0]["source_id"] = "bad source id"
    path = tmp_path / "bad-source.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert issue_codes(load_and_validate(path)) == {"MANIFEST_STRUCTURE_INVALID"}


def test_missing_operator_collection_review_requires_review() -> None:
    payload = valid_manifest()
    payload["operator_review"]["collection_reviewed"] = False
    assert evaluate(payload).status is GateStatus.REVIEW_REQUIRED


def test_operator_review_not_requested_requires_review() -> None:
    payload = valid_manifest()
    payload["operator_review"]["final_drain_approval"] = "NOT_REQUESTED"
    assert evaluate(payload).status is GateStatus.REVIEW_REQUIRED


def test_ci_only_cannot_stand_in_for_live_evidence() -> None:
    payload = valid_manifest()
    payload["environment"]["evidence_kind"] = "LIVE"
    payload["evidence_sources"][0]["source_type"] = "CI"
    result = evaluate(payload)
    assert result.status is GateStatus.BLOCKED
    assert "CI_IS_NOT_LIVE_DRAIN_EVIDENCE" in issue_codes(result)


def test_invalid_manifest_structure_is_blocked(tmp_path: Path) -> None:
    payload = valid_manifest()
    del payload["worker_nodes"]
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    result = load_and_validate(path)
    assert result.status is GateStatus.BLOCKED
    assert issue_codes(result) == {"MANIFEST_STRUCTURE_INVALID"}


def test_sensitive_content_is_rejected_without_echo(tmp_path: Path) -> None:
    payload = valid_manifest()
    leaked_value = "Bearer do-not-print-this-value"
    payload["unresolved_items"] = [leaked_value]
    path = tmp_path / "sensitive.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    result = load_and_validate(path)
    output = result.model_dump_json()
    assert result.status is GateStatus.BLOCKED
    assert issue_codes(result) == {"SENSITIVE_CONTENT_DETECTED"}
    assert leaked_value not in output


def test_committed_example_matches_the_positive_fixture() -> None:
    result = load_and_validate(EXAMPLE_MANIFEST, clock=lambda: FROZEN_NOW)
    assert result.status is GateStatus.READY_FOR_OPERATOR_REVIEW
    assert result.synthetic_evidence is True


def test_cli_is_offline_and_does_not_need_application_environment(tmp_path: Path) -> None:
    payload = valid_manifest()
    runtime_now = datetime.now(UTC)
    runtime_captured = (runtime_now - timedelta(seconds=5)).isoformat()
    for key, value in _walk_dicts(payload):
        if key in {"captured_at", "observed_at", "reviewed_at"}:
            value[key] = runtime_captured
    payload["validation_policy"]["evaluated_at"] = runtime_now.isoformat()
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    environment = {
        "PATH": os.environ.get("PATH", ""),
        "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
        "DATABASE_URL": "not-a-database-url",
        "CELERY_BROKER_URL": "not-a-broker-url",
        "PYTHONPATH": str(BACKEND_DIR),
    }
    completed = subprocess.run(
        [sys.executable, "-m", "scripts.r3c_phase3_drain_evidence", str(manifest)],
        cwd=BACKEND_DIR,
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert completed.returncode == 0
    assert json.loads(completed.stdout)["status"] == "READY_FOR_OPERATOR_REVIEW"
    assert completed.stderr == ""
    assert "DATABASE_URL" not in completed.stdout
    assert "CELERY_BROKER_URL" not in completed.stdout


def test_cli_ignores_environment_clock_override_and_rejects_stale_fixture(tmp_path: Path) -> None:
    payload = valid_manifest()
    runtime_now = datetime.now(UTC)
    stale = (runtime_now - timedelta(hours=2)).isoformat()
    for key, container in _walk_dicts(payload):
        if key in {"captured_at", "observed_at", "reviewed_at"}:
            container[key] = stale
    payload["validation_policy"]["evaluated_at"] = runtime_now.isoformat()
    manifest = tmp_path / "stale.json"
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    environment = {
        "PATH": os.environ.get("PATH", ""),
        "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
        "PYTHONPATH": str(BACKEND_DIR),
        "R3C_EVALUATED_AT": stale,
    }
    completed = subprocess.run(
        [sys.executable, "-m", "scripts.r3c_phase3_drain_evidence", str(manifest)],
        cwd=BACKEND_DIR,
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    result = json.loads(completed.stdout)
    assert completed.returncode == 2
    assert result["status"] == "BLOCKED"
    assert "EVIDENCE_EXPIRED" in {issue["code"] for issue in result["issues"]}


def _walk_dicts(value: Any) -> Iterator[tuple[str, dict[str, Any]]]:
    if isinstance(value, dict):
        for key in list(value):
            yield key, value
            yield from _walk_dicts(value[key])
    elif isinstance(value, list):
        for item in value:
            yield from _walk_dicts(item)


def test_validator_has_no_application_or_external_client_imports() -> None:
    source = VALIDATOR_SOURCE.read_text(encoding="utf-8")

    for forbidden_import in (
        "from app",
        "import app",
        "sqlalchemy",
        "asyncpg",
        "celery",
        "requests",
        "httpx",
        "socket",
    ):
        assert forbidden_import not in source


def test_processing_inventory_template_is_read_only() -> None:
    sql = INVENTORY_SQL.read_text(encoding="utf-8")
    statements = "\n".join(
        line for line in sql.splitlines() if not line.lstrip().startswith("--")
    ).casefold()

    for forbidden_statement in ("update ", "delete ", "insert ", "truncate ", "alter ", "drop "):
        assert forbidden_statement not in statements
    assert "begin transaction read only" in statements
    assert "commit" in statements


def test_processing_inventory_always_emits_all_canonical_tables() -> None:
    sql = INVENTORY_SQL.read_text(encoding="utf-8").casefold()
    assert "canonical_tables(table_name)" in sql
    assert "left join aggregated using (table_name)" in sql
    assert "coalesce(aggregated.processing_total, 0)" in sql
    assert "aggregated.minimum_attempt_epoch" in sql
    assert "aggregated.maximum_attempt_epoch" in sql
    for table_name in ("resumes", "job_descriptions", "match_results"):
        assert f"('{table_name}'::text)" in sql


@pytest.mark.asyncio
async def test_processing_inventory_postgresql_empty_one_and_all_tables() -> None:
    connection = await _connect_phase3_ci_database()
    try:
        await connection.execute(
            """
            CREATE TEMP TABLE resumes (
                id bigint, revision integer, is_deleted boolean, parsing_status text,
                processing_attempt_epoch integer, processing_owner_token text,
                processing_activation_token text, processing_heartbeat_at timestamptz,
                processing_lease_expires_at timestamptz
            );
            CREATE TEMP TABLE job_descriptions (
                id bigint, revision integer, is_deleted boolean, parsing_status text,
                processing_attempt_epoch integer, processing_owner_token text,
                processing_activation_token text, processing_heartbeat_at timestamptz,
                processing_lease_expires_at timestamptz
            );
            CREATE TEMP TABLE match_results (
                status text, resume_id bigint, resume_revision integer,
                job_id bigint, job_revision integer, processing_attempt_epoch integer,
                processing_owner_token text, processing_activation_token text,
                processing_heartbeat_at timestamptz, processing_lease_expires_at timestamptz
            );
            """
        )
        query = (
            INVENTORY_SQL.read_text(encoding="utf-8")
            .replace("BEGIN TRANSACTION READ ONLY;", "")
            .replace("COMMIT;", "")
            .strip()
        )

        async def snapshot() -> dict[str, dict[str, Any]]:
            rows = await connection.fetch(query)
            return {row["table_name"]: dict(row) for row in rows}

        empty = await snapshot()
        assert set(empty) == {"resumes", "job_descriptions", "match_results"}
        assert all(row["processing_total"] == 0 for row in empty.values())
        assert all(row["minimum_attempt_epoch"] is None for row in empty.values())
        assert all(row["maximum_attempt_epoch"] is None for row in empty.values())

        await connection.execute(
            """
            INSERT INTO resumes
                (id, revision, is_deleted, parsing_status, processing_attempt_epoch)
            VALUES (1, 1, false, 'PROCESSING', 7);
            INSERT INTO job_descriptions
                (id, revision, is_deleted, parsing_status, processing_attempt_epoch)
            VALUES (10, 1, false, 'PROCESSING', 8);
            """
        )
        active_snapshots = await snapshot()
        assert active_snapshots["resumes"]["snapshot_invalidated"] == 0
        assert active_snapshots["job_descriptions"]["snapshot_invalidated"] == 0
        assert active_snapshots["match_results"]["processing_total"] == 0

        await connection.execute(
            """
            INSERT INTO resumes
                (id, revision, is_deleted, parsing_status, processing_attempt_epoch)
            VALUES (2, 1, true, 'PROCESSING', 11);
            INSERT INTO job_descriptions
                (id, revision, is_deleted, parsing_status, processing_attempt_epoch)
            VALUES (11, 1, true, 'PROCESSING', 12);
            INSERT INTO match_results
                (status, resume_id, resume_revision, job_id, job_revision,
                 processing_attempt_epoch)
            VALUES
                ('PROCESSING', 1, 1, 10, 1, 9),
                ('PROCESSING', 2, 1, 10, 1, 13),
                ('PROCESSING', 1, 1, 11, 1, 14);
            """
        )
        all_tables = await snapshot()
        assert {name: row["processing_total"] for name, row in all_tables.items()} == {
            "resumes": 2,
            "job_descriptions": 2,
            "match_results": 3,
        }
        assert all_tables["resumes"]["snapshot_invalidated"] == 1
        assert all_tables["job_descriptions"]["snapshot_invalidated"] == 1
        assert all_tables["match_results"]["snapshot_invalidated"] == 2
        assert all_tables["resumes"]["minimum_attempt_epoch"] == 7
        assert all_tables["resumes"]["maximum_attempt_epoch"] == 11
        assert all_tables["job_descriptions"]["minimum_attempt_epoch"] == 8
        assert all_tables["job_descriptions"]["maximum_attempt_epoch"] == 12
        assert all_tables["match_results"]["minimum_attempt_epoch"] == 9
        assert all_tables["match_results"]["maximum_attempt_epoch"] == 14
    finally:
        await connection.close()
