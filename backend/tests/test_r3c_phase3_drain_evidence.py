from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

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
HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64


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
            "commit_sha": HASH_A,
            "artifact_digest": f"sha256:{HASH_B}",
            "review_reference": "synthetic-review/r3c-phase2",
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
        },
        "validation_policy": {
            "evaluated_at": NOW,
            "maximum_evidence_age_seconds": 3600,
            "maximum_future_skew_seconds": 60,
            "maximum_cross_source_skew_seconds": 600,
        },
    }


def evaluate(payload: dict[str, Any]):
    manifest = EvidenceManifest.model_validate_json(json.dumps(payload))
    return validate_manifest(manifest)


def issue_codes(result: Any) -> set[str]:
    return {issue.code for issue in result.issues}


def test_complete_synthetic_manifest_is_ready_only_for_operator_review() -> None:
    result = evaluate(valid_manifest())

    assert result.status is GateStatus.READY_FOR_OPERATOR_REVIEW
    assert result.synthetic_evidence is True
    assert "authorize reclaim" in result.disclaimer


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
            "EVIDENCE_EXPIRED",
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
    result = load_and_validate(EXAMPLE_MANIFEST)
    assert result.status is GateStatus.READY_FOR_OPERATOR_REVIEW
    assert result.synthetic_evidence is True


def test_cli_is_offline_and_does_not_need_application_environment(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(valid_manifest()), encoding="utf-8")
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
