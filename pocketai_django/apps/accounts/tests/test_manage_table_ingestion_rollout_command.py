from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone

from apps.accounts.constants import FEATURE_FLAG_METADATA_KEY
from apps.accounts.feature_flags import FeatureFlagService
from apps.accounts.models import BusinessProfile, RegistrationSession
from apps.knowledge.models import KnowledgeUpload


User = get_user_model()


class ManageTableIngestionRolloutCommandTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user(
            email="phase6-owner@example.com",
            password="changeme123",
            first_name="Owner",
        )
        self.registration_canary = RegistrationSession.objects.create(user=self.user)
        self.registration_control = RegistrationSession.objects.create(user=self.user)
        self.canary_business = BusinessProfile.objects.create(
            user=self.user,
            registration_session=self.registration_canary,
            name="Canary Co",
            industry="Retail",
            status="active",
            metadata={"cohort": "canary"},
        )
        self.control_business = BusinessProfile.objects.create(
            user=self.user,
            registration_session=self.registration_control,
            name="Control Co",
            industry="Retail",
            status="active",
            metadata={"cohort": "control"},
        )

    def test_enable_then_rollback_updates_phase6_flag_bundle_for_target_cohort(self) -> None:
        call_command(
            "manage_table_ingestion_rollout",
            "--action",
            "enable",
            "--cohort",
            "canary",
        )
        canary_state = FeatureFlagService.snapshot(BusinessProfile.objects.get(id=self.canary_business.id))
        control_state = FeatureFlagService.snapshot(BusinessProfile.objects.get(id=self.control_business.id))

        self.assertTrue(canary_state.rag_shadow_ingestion)
        self.assertTrue(canary_state.rag_eval_logging)
        self.assertFalse(control_state.rag_shadow_ingestion)
        self.assertFalse(control_state.rag_eval_logging)

        call_command(
            "manage_table_ingestion_rollout",
            "--action",
            "rollback",
            "--cohort",
            "canary",
        )
        canary_state = FeatureFlagService.snapshot(BusinessProfile.objects.get(id=self.canary_business.id))
        self.assertFalse(canary_state.rag_shadow_ingestion)
        self.assertFalse(canary_state.rag_eval_logging)

    def test_dashboard_enforcement_and_output(self) -> None:
        upload = KnowledgeUpload.objects.create(
            business_profile=self.canary_business,
            user=self.user,
            display_name="Sample",
            source_name="sample.pdf",
            source_type="file",
            status="active",
            last_ingested_at=timezone.now(),
            ingestion_metadata={},
        )

        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            baseline_path = tmp_path / "baseline.json"
            output_path = tmp_path / "dashboard.json"
            baseline_path.write_text(
                json.dumps(
                    {
                        "snapshot_label": "baseline",
                        "upload_id": "baseline-upload",
                        "row_chunks": [],
                        "tables": [],
                    }
                ),
                encoding="utf-8",
            )

            with (
                mock.patch(
                    "apps.accounts.management.commands.manage_table_ingestion_rollout.capture_upload_snapshot",
                    return_value={"snapshot_label": "candidate", "upload_id": str(upload.id), "row_chunks": [], "tables": []},
                ),
                mock.patch(
                    "apps.accounts.management.commands.manage_table_ingestion_rollout.compare_snapshots",
                    return_value={
                        "quality_gate": {
                            "passed": True,
                            "failed_checks": [],
                            "regressions": [],
                            "metrics": {"row_recall": 1.0},
                        }
                    },
                ),
            ):
                call_command(
                    "manage_table_ingestion_rollout",
                    "--action",
                    "dashboard",
                    "--cohort",
                    "canary",
                    "--baseline-json",
                    str(baseline_path),
                    "--output",
                    str(output_path),
                    "--enforce",
                    "--min-pass-rate",
                    "1.0",
                )

            self.assertTrue(output_path.exists())
            payload = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(payload.get("uploads_evaluated"), 1)
            self.assertEqual(payload.get("pass_rate"), 1.0)

            with (
                mock.patch(
                    "apps.accounts.management.commands.manage_table_ingestion_rollout.capture_upload_snapshot",
                    return_value={"snapshot_label": "candidate", "upload_id": str(upload.id), "row_chunks": [], "tables": []},
                ),
                mock.patch(
                    "apps.accounts.management.commands.manage_table_ingestion_rollout.compare_snapshots",
                    return_value={
                        "quality_gate": {
                            "passed": False,
                            "failed_checks": ["scope_f1"],
                            "regressions": [],
                            "metrics": {"scope_f1": 0.3},
                        }
                    },
                ),
            ):
                with self.assertRaises(CommandError):
                    call_command(
                        "manage_table_ingestion_rollout",
                        "--action",
                        "dashboard",
                        "--cohort",
                        "canary",
                        "--baseline-json",
                        str(baseline_path),
                        "--enforce",
                        "--min-pass-rate",
                        "1.0",
                    )

    def test_phase6_flag_bundle_is_initialized_for_business_profiles(self) -> None:
        metadata = BusinessProfile.objects.get(id=self.control_business.id).metadata
        features = metadata.get(FEATURE_FLAG_METADATA_KEY) if isinstance(metadata, dict) else {}
        self.assertNotIn("rag_table_pipeline_v2", features)
        self.assertIn("rag_shadow_ingestion", features)
        self.assertIn("rag_eval_logging", features)
        self.assertFalse(bool(features.get("rag_shadow_ingestion")))
        self.assertFalse(bool(features.get("rag_eval_logging")))
