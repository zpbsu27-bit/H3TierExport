"""Offline contracts for cloud motion provider boundaries and failure semantics."""
from __future__ import annotations

import copy
import base64
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


PRODUCT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PRODUCT / "scripts"))
SCRIPT = PRODUCT / "scripts" / "cloud_animation.py"
SPEC = importlib.util.spec_from_file_location("cloud_animation", SCRIPT)
assert SPEC and SPEC.loader
cloud = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cloud)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def response(status: int, document: dict | list | None = None, *, headers: dict | None = None) -> cloud.HttpResponse:
    body = b"" if document is None else json.dumps(document, separators=(",", ":")).encode("utf-8")
    return cloud.HttpResponse(status, headers or {"Content-Type": "application/json"}, body)


class FakeTransport:
    def __init__(self, handler):
        self.handler = handler
        self.calls: list[dict] = []

    def __call__(self, method: str, url: str, headers: dict, body: bytes | None, timeout: int):
        call = {"method": method, "url": url, "headers": dict(headers), "body": body, "timeout": timeout}
        self.calls.append(call)
        return self.handler(call)


class CloudAnimationContract(unittest.TestCase):
    def setUp(self) -> None:
        test_root = PRODUCT / "build" / "tests"
        test_root.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="cloud-motion-contract-", dir=test_root)
        self.root = Path(self.temp.name).resolve()
        (self.root / "discoveries").mkdir()
        self.source = self.root / "hero.png"
        self.source.write_bytes(b"synthetic-png-bytes")
        self.glb = self.root / "hero.glb"
        self.glb.write_bytes(b"synthetic-glb-bytes")
        self.environ = {
            "MESHY_API_KEY": "test-meshy-key-never-print",
            "SCENARIO_API_KEY": "test-scenario-key-never-print",
            "SCENARIO_API_SECRET": "test-scenario-secret-never-print",
        }
        self.workflow = {
            "id": "workflow_1234",
            "name": "Action Sprite Studio test fixture",
            "description": "Synthetic workflow fixture",
            "shortDescription": "Synthetic",
            "privacy": "public",
            "status": "ready",
            "inputs": [
                {"name": "character_image", "type": "file", "kind": "image",
                 "required": {"always": True}},
                {"name": "motion", "type": "string", "required": {"always": True}},
            ],
        }

    def tearDown(self) -> None:
        self.temp.cleanup()

    def make_meshy_plan(self, operation_id: str = "meshy-pilot", *, stage: str = "image-to-3d",
                        payload: dict | None = None, source_paths: list[str] | None = None) -> Path:
        if payload is None:
            payload = {"image_url": {"source_index": 0}, "target_formats": ["glb"]}
        if source_paths is None:
            source_paths = [str(self.source)] if any(cloud._walk_source_markers(payload)) else []
        plan = cloud.build_operation_plan(provider="meshy", stage=stage, payload=payload,
                                          source_paths=source_paths, operation_id=operation_id,
                                          development_root=self.root)
        return cloud.write_plan(plan, development_root=self.root)

    def make_scenario_discovery(self) -> Path:
        workflow = cloud._normalize_workflow(self.workflow)
        document = {"schema": cloud.DISCOVERY_SCHEMA, "provider": "scenario",
                    "workflow": workflow, "inputs_sha256": cloud._workflow_schema_hash(workflow),
                    "api_endpoint": "/workflows/{workflowId}"}
        return cloud._save_discovery(document, workflow["id"], self.root)

    def make_scenario_plan(self, operation_id: str = "scenario-pilot", *,
                           payload: dict | None = None, source_paths: list[str] | None = None) -> Path:
        discovery = self.make_scenario_discovery()
        if payload is None:
            payload = {"character_image": "asset_1234", "motion": "walk in place"}
        if source_paths is None:
            source_paths = []
        plan = cloud.build_operation_plan(provider="scenario", stage="workflow-run", payload=payload,
                                          source_paths=source_paths, operation_id=operation_id,
                                          workflow_discovery_path=str(discovery),
                                          development_root=self.root)
        return cloud.write_plan(plan, development_root=self.root)

    def scenario_handler(self, *, run_response=None, dry_response=None, job_response=None,
                         asset_response=None, upload_response=None, download_body=b"png-candidate",
                         workflow=None):
        workflow = workflow or self.workflow
        def handler(call):
            url = call["url"]
            if call["method"] == "GET" and "/workflows/workflow_1234" in url:
                return response(200, {"workflow": workflow})
            if call["method"] == "GET" and url.endswith("/jobs/job-1234"):
                return response(200, job_response or {"job": {"id": "job-1234", "status": "success",
                                                               "metadata": {"assetIds": ["asset_5678"]}}})
            if call["method"] == "GET" and url.endswith("/assets/asset_5678"):
                return response(200, asset_response or {"asset": {"assetId": "asset_5678",
                                                                     "url": "https://cdn.example.test/frame.png",
                                                                     "mimeType": "image/png"}})
            if call["method"] == "GET" and url.endswith("/assets/asset_9000"):
                return response(200, {"asset": {"assetId": "asset_9000", "url": "https://cdn.example.test/source.png"}})
            if call["method"] == "GET" and url.startswith("https://cdn.example.test/"):
                return cloud.HttpResponse(200, {"Content-Type": "image/png"}, download_body)
            if call["method"] == "PUT" and "dryRun=" in url:
                return response(269, dry_response or {"billing": {"cost": 14}, "dryRun": True})
            if call["method"] == "PUT" and url.endswith("/workflows/workflow_1234/run"):
                return response(200, run_response or {"job": {"jobId": "job-1234", "status": "pending"}})
            if call["method"] == "POST" and url.endswith("/assets"):
                return response(200, upload_response or {"asset": {"assetId": "asset_9000"}})
            return response(404, {"message": "unexpected fake route"})
        return FakeTransport(handler)

    def test_catalog_marks_unimplemented_services_without_claiming_support(self) -> None:
        providers = {item["provider"]: item for item in cloud.catalog()["providers"]}
        self.assertEqual(providers["meshy"]["status"], "implemented")
        self.assertEqual(providers["scenario"]["status"], "workflow-adapter")
        self.assertEqual(providers["anything-world"]["status"], "not-adapted")
        self.assertEqual(providers["mixamo"]["status"], "not-adapted")
        self.assertIn("no public official API", providers["mixamo"]["api"])
        self.assertTrue(any("4,000,000" in note and "multipart" in note.lower()
                            for note in providers["scenario"]["limits"]))

    def test_meshy_local_source_is_hash_bound_data_uri_and_duplicate_submit_never_posts_again(self) -> None:
        plan_path = self.make_meshy_plan()
        check = cloud.check_operation("meshy-pilot", development_root=self.root)
        self.assertEqual(check["status"], "READY")
        fake = FakeTransport(lambda call: response(200, {"result": "mesh-task-1"}))
        with self.assertRaisesRegex(cloud.CloudAnimationError, "UPLOAD_AUTHORIZATION_REQUIRED"):
            cloud.submit_operation("meshy-pilot", authorize_cost=True, environ=self.environ,
                                   transport=fake, development_root=self.root)
        with self.assertRaisesRegex(cloud.CloudAnimationError, "COST_AUTHORIZATION_REQUIRED"):
            cloud.submit_operation("meshy-pilot", authorize_upload=True, environ=self.environ,
                                   transport=fake, development_root=self.root)
        self.assertEqual(fake.calls, [])

        result = cloud.submit_operation("meshy-pilot", authorize_cost=True, authorize_upload=True,
                                        environ=self.environ, transport=fake, development_root=self.root)
        self.assertEqual(result["status"], "SUBMITTED")
        self.assertEqual(len(fake.calls), 1)
        sent = json.loads(fake.calls[0]["body"])
        self.assertEqual(sent["image_url"], "data:image/png;base64," + __import__("base64").b64encode(self.source.read_bytes()).decode())
        with self.assertRaisesRegex(cloud.CloudAnimationError, "OUTPUT_ALREADY_EXISTS"):
            cloud.submit_operation("meshy-pilot", authorize_cost=True, authorize_upload=True,
                                   environ=self.environ, transport=fake, development_root=self.root)
        self.assertEqual(len(fake.calls), 1)
        self.assertTrue(plan_path.is_file())

    def test_credentials_are_never_written_to_plan_state_or_command_result(self) -> None:
        self.make_meshy_plan("secret-audit")
        fake = FakeTransport(lambda call: response(200, {"result": "task-secret-audit"}))
        result = cloud.submit_operation("secret-audit", authorize_cost=True, authorize_upload=True,
                                        environ=self.environ, transport=fake, development_root=self.root)
        operation = self.root / "operations" / "secret-audit"
        serialized = "\n".join(path.read_text(encoding="utf-8", errors="replace") for path in operation.rglob("*") if path.is_file())
        output = json.dumps(result)
        for secret in (self.environ["MESHY_API_KEY"], self.environ["SCENARIO_API_KEY"], self.environ["SCENARIO_API_SECRET"]):
            self.assertNotIn(secret, serialized)
            self.assertNotIn(secret, output)
        self.assertTrue(fake.calls[0]["headers"]["Authorization"].endswith(self.environ["MESHY_API_KEY"]))

    def test_ambiguous_meshy_submission_is_persisted_and_never_retried(self) -> None:
        self.make_meshy_plan("ambiguous-meshy")
        def handler(call):
            raise cloud.CloudAnimationError("NETWORK_OUTCOME_UNKNOWN", "synthetic disconnect")
        fake = FakeTransport(handler)
        with self.assertRaisesRegex(cloud.CloudAnimationError, "NETWORK_OUTCOME_UNKNOWN"):
            cloud.submit_operation("ambiguous-meshy", authorize_cost=True, authorize_upload=True,
                                   environ=self.environ, transport=fake, development_root=self.root)
        self.assertEqual(len(fake.calls), 1)
        status = cloud.status_operation("ambiguous-meshy", environ=self.environ,
                                        transport=fake, development_root=self.root)
        self.assertEqual(status["status"], "OUTCOME_UNKNOWN_NO_RETRY")
        with self.assertRaises(cloud.CloudAnimationError):
            cloud.submit_operation("ambiguous-meshy", authorize_cost=True, authorize_upload=True,
                                   environ=self.environ, transport=fake, development_root=self.root)
        self.assertEqual(len(fake.calls), 1)

    def test_async_status_error_preserves_known_job_id_without_claiming_success(self) -> None:
        self.make_meshy_plan("status-error")
        calls = 0
        def handler(call):
            nonlocal calls
            calls += 1
            if call["method"] == "POST":
                return response(200, {"result": "known-task"})
            return response(503, {"message": "opaque provider error"})
        fake = FakeTransport(handler)
        cloud.submit_operation("status-error", authorize_cost=True, authorize_upload=True,
                               environ=self.environ, transport=fake, development_root=self.root)
        status = cloud.status_operation("status-error", environ=self.environ,
                                        transport=fake, development_root=self.root)
        self.assertEqual(status["status"], "STATUS_UNAVAILABLE")
        state = cloud._read_state(self.root / "operations" / "status-error")
        self.assertEqual(state["provider_job_id"], "known-task")
        self.assertNotEqual(status["status"], "CLOUD_SUCCEEDED_REVIEW_REQUIRED")

    def test_meshy_submission_rehashes_actual_bytes_after_plan_validation_before_network_send(self) -> None:
        self.make_meshy_plan("mesh-toctou")
        original_load = cloud._load_plan
        def validate_then_change(*args, **kwargs):
            loaded = original_load(*args, **kwargs)
            self.source.write_bytes(b"replaced-after-plan-check")
            return loaded
        fake = FakeTransport(lambda call: response(200, {"result": "should-not-exist"}))
        with patch.object(cloud, "_load_plan", side_effect=validate_then_change):
            with self.assertRaisesRegex(cloud.CloudAnimationError, "SOURCE_HASH_MISMATCH"):
                cloud.submit_operation("mesh-toctou", authorize_cost=True, authorize_upload=True,
                                       environ=self.environ, transport=fake, development_root=self.root)
        self.assertEqual(fake.calls, [])

    def test_scenario_upload_rehashes_actual_bytes_after_plan_validation_before_post(self) -> None:
        payload = {"character_image": {"source_index": 0}, "motion": "idle"}
        self.make_scenario_plan("scenario-toctou", payload=payload, source_paths=[str(self.source)])
        def handler(call):
            if call["method"] == "GET" and "/workflows/workflow_1234" in call["url"]:
                self.source.write_bytes(b"replaced-after-plan-check")
                return response(200, {"workflow": self.workflow})
            return response(200, {"asset": {"assetId": "should-not-upload"}})
        fake = FakeTransport(handler)
        with self.assertRaisesRegex(cloud.CloudAnimationError, "SOURCE_HASH_MISMATCH"):
            cloud.submit_operation("scenario-toctou", upload_only=True, authorize_upload=True,
                                   environ=self.environ, transport=fake, development_root=self.root)
        self.assertFalse(any(call["method"] == "POST" for call in fake.calls))

    def test_payload_source_hash_and_output_collisions_are_preflighted_offline(self) -> None:
        plan_path = self.make_meshy_plan("frozen-plan")
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        plan["payload"]["image_url"] = "https://cdn.example.test/new.png"
        plan_path.write_text(json.dumps(plan), encoding="utf-8")
        with self.assertRaisesRegex(cloud.CloudAnimationError, "PLAN_SHA256_MISMATCH"):
            cloud.check_operation("frozen-plan", development_root=self.root)

        source_path = self.root / "changed.png"
        source_path.write_bytes(b"before")
        second = cloud.build_operation_plan(provider="meshy", stage="image-to-3d",
                                            payload={"image_url": {"source_index": 0}},
                                            source_paths=[str(source_path)], operation_id="source-drift",
                                            development_root=self.root)
        second_path = cloud.write_plan(second, development_root=self.root)
        source_path.write_bytes(b"after")
        with self.assertRaisesRegex(cloud.CloudAnimationError, "SOURCE_HASH_MISMATCH"):
            cloud.validate_plan(second_path, development_root=self.root)

        third_path = self.make_meshy_plan("output-collision")
        output = self.root / "operations" / "output-collision" / "outputs"
        output.mkdir()
        with self.assertRaisesRegex(cloud.CloudAnimationError, "OUTPUT_COLLISION"):
            cloud.check_operation("output-collision", development_root=self.root)

    def test_meshy_allowlist_rejects_action_overflow_and_signed_source_urls(self) -> None:
        with self.assertRaisesRegex(cloud.CloudAnimationError, "action_ids"):
            cloud.build_operation_plan(provider="meshy", stage="animation",
                                      payload={"rig_task_id": "rig-1", "action_ids": list(range(11))},
                                      source_paths=[], operation_id="too-many-actions",
                                      development_root=self.root)
        with self.assertRaisesRegex(cloud.CloudAnimationError, "URL_CREDENTIALS_FORBIDDEN"):
            cloud.build_operation_plan(provider="meshy", stage="image-to-3d",
                                      payload={"image_url": "https://images.example.test/x.png?X-Amz-Signature=private"},
                                      source_paths=[], operation_id="signed-url",
                                      development_root=self.root)
        for operation_id, url in (
            ("encoded-api-key", "https://images.example.test/x.png?api%5Fkey=do-not-save"),
            ("goog-credential", "https://images.example.test/x.png?X-Goog-Credential=private%40example"),
            ("amz-credential", "https://images.example.test/x.png?X-Amz-Credential=private%2Fkey"),
        ):
            with self.subTest(operation_id=operation_id):
                with self.assertRaisesRegex(cloud.CloudAnimationError, "URL_CREDENTIALS_FORBIDDEN"):
                    cloud.build_operation_plan(provider="meshy", stage="image-to-3d",
                                              payload={"image_url": url}, source_paths=[],
                                              operation_id=operation_id, development_root=self.root)
                self.assertFalse((self.root / "operations" / operation_id).exists())
        with self.assertRaisesRegex(cloud.CloudAnimationError, "URL_PRIVATE_HOST_FORBIDDEN"):
            cloud.build_operation_plan(provider="meshy", stage="image-to-3d",
                                      payload={"image_url": "https://127.0.0.1/private.png"},
                                      source_paths=[], operation_id="private-url",
                                      development_root=self.root)

    def test_scenario_discovery_reads_live_schema_without_hardcoding_workflow_id(self) -> None:
        fake = FakeTransport(lambda call: response(200, {"workflow": self.workflow}))
        result = cloud.scenario_discover(workflow_id="workflow_1234", environ=self.environ,
                                         transport=fake, development_root=self.root)
        self.assertEqual(result["workflow_id"], "workflow_1234")
        self.assertEqual(result["input_count"], 2)
        self.assertTrue(Path(result["path"]).is_file())
        self.assertEqual(fake.calls[0]["method"], "GET")
        self.assertNotIn("workflow_1234", json.dumps(cloud.catalog()))

    def test_scenario_job_retrieve_envelope_and_all_documented_statuses_are_normalized(self) -> None:
        expected = {
            "pending": "CLOUD_RUNNING", "queued": "CLOUD_RUNNING", "warming-up": "CLOUD_RUNNING",
            "in-progress": "CLOUD_RUNNING", "finalizing": "CLOUD_RUNNING",
            "success": "CLOUD_SUCCEEDED_REVIEW_REQUIRED", "failure": "CLOUD_FAILED",
            "canceled": "CLOUD_CANCELED",
        }
        for provider_status, normalized in expected.items():
            with self.subTest(provider_status=provider_status):
                actual = cloud._normalize_cloud_status("scenario", {"job": {"status": provider_status, "progress": 0.6}})
                self.assertEqual(actual["status"], normalized)

    def test_scenario_check_receipt_exposes_small_upload_limit_and_multipart_gap(self) -> None:
        payload = {"character_image": {"source_index": 0}, "motion": "walk"}
        self.make_scenario_plan("scenario-limit-note", payload=payload, source_paths=[str(self.source)])
        checked = cloud.check_operation("scenario-limit-note", development_root=self.root)
        self.assertEqual(checked["provider_limits"], {
            "scenario_asset_post_max_bytes_exclusive": 4_000_000,
            "scenario_multipart_upload_implemented": False,
        })

    def test_scenario_small_asset_limit_rejects_at_exact_four_megabytes_before_plan_or_upload(self) -> None:
        large = self.root / "four-megabyte.png"
        large.write_bytes(b"x" * 4_000_000)
        discovery = self.make_scenario_discovery()
        with self.assertRaisesRegex(cloud.CloudAnimationError, "SCENARIO_MULTIPART_NOT_SUPPORTED"):
            cloud.build_operation_plan(provider="scenario", stage="workflow-run",
                                      payload={"character_image": {"source_index": 0}, "motion": "walk"},
                                      source_paths=[str(large)], operation_id="scenario-too-large",
                                      workflow_discovery_path=str(discovery), development_root=self.root)
        self.assertFalse((self.root / "operations" / "scenario-too-large").exists())

    def test_scenario_dry_run_is_separate_from_explicit_run_then_status_and_fetch_remain_candidates(self) -> None:
        self.make_scenario_plan()
        fake = self.scenario_handler()
        dry = cloud.scenario_dry_run("scenario-pilot", environ=self.environ,
                                     transport=fake, development_root=self.root)
        self.assertEqual(dry["status"], "ESTIMATED")
        self.assertEqual(dry["http_status"], 269)
        self.assertFalse(dry["job_submitted"])
        self.assertTrue(any("dryRun=api" in call["url"] for call in fake.calls))
        with self.assertRaisesRegex(cloud.CloudAnimationError, "COST_AUTHORIZATION_REQUIRED"):
            cloud.submit_operation("scenario-pilot", authorize_upload=True, environ=self.environ,
                                   transport=fake, development_root=self.root)
        run = cloud.submit_operation("scenario-pilot", authorize_cost=True, authorize_upload=True,
                                     environ=self.environ, transport=fake, development_root=self.root)
        self.assertEqual(run["provider_job_id"], "job-1234")
        self.assertTrue(run["h3_action_group_complete"] is False)
        status = cloud.status_operation("scenario-pilot", environ=self.environ,
                                        transport=fake, development_root=self.root)
        self.assertEqual(status["status"], "CLOUD_SUCCEEDED_REVIEW_REQUIRED")
        self.assertFalse(status["alpha_verified"])
        fetched = cloud.fetch_operation("scenario-pilot", environ=self.environ,
                                        transport=fake, development_root=self.root)
        self.assertEqual(fetched["status"], "CANDIDATE_REVIEW_REQUIRED")
        self.assertEqual(len(fetched["files"]), 1)
        self.assertFalse(fetched["art_approved"])
        self.assertFalse(fetched["alpha_verified"])
        self.assertTrue(Path(fetched["files"][0]["path"]).is_relative_to(self.root))
        self.assertTrue((Path(fetched["files"][0]["path"]).parent / "fetch-receipt.json").is_file())
        with self.assertRaisesRegex(cloud.CloudAnimationError, "OUTPUT_COLLISION"):
            cloud.fetch_operation("scenario-pilot", environ=self.environ,
                                  transport=fake, development_root=self.root)

    def test_scenario_local_source_upload_requires_its_own_flag_and_is_never_repeated(self) -> None:
        payload = {"character_image": {"source_index": 0}, "motion": "idle breathing"}
        self.make_scenario_plan("scenario-upload", payload=payload, source_paths=[str(self.source)])
        fake = self.scenario_handler()
        with self.assertRaisesRegex(cloud.CloudAnimationError, "UPLOAD_AUTHORIZATION_REQUIRED"):
            cloud.submit_operation("scenario-upload", upload_only=True, environ=self.environ,
                                   transport=fake, development_root=self.root)
        self.assertEqual(fake.calls, [])
        uploaded = cloud.submit_operation("scenario-upload", upload_only=True, authorize_upload=True,
                                          environ=self.environ, transport=fake, development_root=self.root)
        self.assertEqual(uploaded["status"], "ASSETS_UPLOADED")
        asset_posts = [call for call in fake.calls if call["method"] == "POST" and call["url"].endswith("/assets")]
        self.assertEqual(len(asset_posts), 1)
        decoded_auth = base64.b64decode(fake.calls[0]["headers"]["Authorization"].split(" ", 1)[1]).decode()
        self.assertIn("test-scenario-secret-never-print", decoded_auth)
        persisted = json.dumps(cloud._read_state(self.root / "operations" / "scenario-upload"))
        self.assertNotIn("test-scenario-secret-never-print", persisted)
        again = cloud.submit_operation("scenario-upload", upload_only=True, authorize_upload=True,
                                       environ=self.environ, transport=fake, development_root=self.root)
        self.assertTrue(again["duplicate_submit_blocked"])
        self.assertEqual(len([call for call in fake.calls if call["method"] == "POST" and call["url"].endswith("/assets")]), 1)

    def test_scenario_workflow_schema_drift_blocks_cost_call(self) -> None:
        self.make_scenario_plan("scenario-drift")
        changed = copy.deepcopy(self.workflow)
        changed["inputs"].append({"name": "new_input", "type": "boolean"})
        fake = self.scenario_handler(workflow=changed)
        with self.assertRaisesRegex(cloud.CloudAnimationError, "SCENARIO_WORKFLOW_SCHEMA_DRIFT"):
            cloud.scenario_dry_run("scenario-drift", environ=self.environ,
                                   transport=fake, development_root=self.root)
        self.assertFalse(any(call["method"] == "PUT" for call in fake.calls))

    def test_scenario_source_references_are_hash_bound_and_uploaded_as_private_asset(self) -> None:
        payload = {"character_image": {"source_index": 0}, "motion": "walk"}
        self.make_scenario_plan("scenario-source-hash", payload=payload, source_paths=[str(self.source)])
        fake = self.scenario_handler()
        cloud.submit_operation("scenario-source-hash", upload_only=True, authorize_upload=True,
                               environ=self.environ, transport=fake, development_root=self.root)
        upload_call = next(call for call in fake.calls if call["method"] == "POST")
        body = json.loads(upload_call["body"])
        self.assertEqual(body["privacy"], "private")
        uploaded_bytes = base64.b64decode(body["image"])
        self.assertEqual(uploaded_bytes, self.source.read_bytes())
        upload_state = cloud._read_state(self.root / "operations" / "scenario-source-hash")
        self.assertEqual(upload_state["asset_uploads"]["0"]["source_sha256"], hashlib.sha256(uploaded_bytes).hexdigest())
        self.assertEqual(upload_state["asset_uploads"]["0"]["source_bytes"], len(uploaded_bytes))
        plan = json.loads((self.root / "operations" / "scenario-source-hash" / "plan.json").read_text(encoding="utf-8"))
        self.assertEqual(plan["sources"][0]["sha256"], digest(self.source))

    def test_scenario_asset_limit_rejects_more_than_64_before_download_or_output_creation(self) -> None:
        plan_path = self.make_scenario_plan("scenario-asset-limit")
        operation_dir = plan_path.parent
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        state = cloud._state_default(plan)
        state.update({"phase": "SUCCEEDED_REVIEW_REQUIRED", "provider_job_id": "job-many-assets",
                      "last_status": {"status": "CLOUD_SUCCEEDED_REVIEW_REQUIRED"}})
        cloud._write_state(operation_dir / "state.json", state, root=self.root)
        asset_ids = [f"asset_{index:04d}" for index in range(65)]
        fake = FakeTransport(lambda call: response(200, {"job": {"jobId": "job-many-assets", "status": "success",
                                                                    "metadata": {"assetIds": asset_ids}}}))
        with self.assertRaisesRegex(cloud.CloudAnimationError, "ASSET_LIMIT_EXCEEDED"):
            cloud.fetch_operation("scenario-asset-limit", environ=self.environ,
                                  transport=fake, development_root=self.root)
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(fake.calls[0]["method"], "GET")
        self.assertFalse((operation_dir / "outputs").exists())

    def test_scenario_file_input_rejects_non_asset_ids_and_unbound_sources(self) -> None:
        discovery = self.make_scenario_discovery()
        with self.assertRaisesRegex(cloud.CloudAnimationError, "SCENARIO_ASSET_ID_INVALID"):
            cloud.build_operation_plan(provider="scenario", stage="workflow-run",
                                      payload={"character_image": "https://evil.example/a.png", "motion": "walk"},
                                      source_paths=[], operation_id="bad-asset-id",
                                      workflow_discovery_path=str(discovery), development_root=self.root)
        with self.assertRaisesRegex(cloud.CloudAnimationError, "SOURCE_UNUSED"):
            cloud.build_operation_plan(provider="scenario", stage="workflow-run",
                                      payload={"character_image": "asset_1234", "motion": "walk"},
                                      source_paths=[str(self.source)], operation_id="unbound-source",
                                      workflow_discovery_path=str(discovery), development_root=self.root)


if __name__ == "__main__":
    unittest.main()
