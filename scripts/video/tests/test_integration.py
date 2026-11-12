"""只访问本机临时 HTTP 服务；不调用 GitHub、R2 或生产 Worker。"""
import copy
import hashlib
import json
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.parse import quote, unquote

from video import catalog, media, publish, runner
from video.api import HTTPFailure, ServiceAPI, json_request
from test_video import job


class LocalStorage:
    def __init__(self, publication):
        self.publication = publication
        self.claimed = False
        self.calls = []
        self.uploaded = {}
        self.source = {item["sourceKey"]: bytes([index]) * item["size"] for index, item in enumerate(publish.publication_objects(publication), 1)}
        for descriptor in publish.publication_objects(publication):
            key = descriptor["sourceKey"]
            digest = hashlib.sha256(self.source[key]).hexdigest()
            result = self.publication["jobs"][0]["result"]
            if key == result["download"]["key"]:
                result["download"]["sha256"] = digest
            elif key == result["preview"]["key"]:
                result["preview"]["sha256"] = digest
            else:
                result["frames"][0]["thumbnailSha256"] = digest
        state = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def send_json(self, status, value):
                data = json.dumps(value).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            def do_GET(self):
                key = self.path.removeprefix("/source/")
                if key in state.source:
                    data = state.source[key]
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                else:
                    self.send_json(503, {"secret": "never log response bodies"})
            def do_PUT(self):
                size = int(self.headers["Content-Length"])
                state.uploaded[unquote(self.path.removeprefix("/put/"))] = (self.rfile.read(size), self.headers["Content-Type"])
                self.send_response(200)
                self.send_header("Content-Length", "0")
                self.end_headers()
            def do_POST(self):
                if self.headers.get("X-Video-Service-Token") != "test-service":
                    return self.send_json(401, {})
                value = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                state.calls.append((self.path, value))
                action = self.path.rsplit("/", 1)[1]
                if action == "claim":
                    task = None if state.claimed else state.publication
                    state.claimed = True
                    return self.send_json(200, {"task": task, "leaseToken": "local-lease"})
                if value.get("leaseToken") != "local-lease":
                    return self.send_json(409, {})
                if action == "verify":
                    valid = all(item["key"] in state.uploaded and len(state.uploaded[item["key"]][0]) == item["size"] and state.uploaded[item["key"]][1] == item["contentType"] for item in value["objects"])
                    return self.send_json(200 if valid else 409, {"ok": valid})
                if action == "objects":
                    objects = [{**item, "sourceUrl": state.url + "/source/" + quote(item["sourceKey"], safe="/"), "url": state.url + "/put/" + quote(item["key"], safe="/")} for item in value["objects"]]
                    return self.send_json(200, {"objects": objects})
                if action == "mirrors":
                    return self.send_json(200, {"objects": [{**item, "url": state.url + "/put/" + quote(item["key"], safe="/")} for item in value["objects"]]})
                return self.send_json(200, {})
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = "http://127.0.0.1:" + str(self.server.server_port)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()


class MemoryGit:
    def __init__(self):
        self.files = {"metadata/video.json": {"images": {}}, "data/video.json": {"blob": catalog.encode([])}, "stats.json": {"total": {"desktop": 21}, "releases": [{"id": "keep"}]}}
        self.writes = 0
        self.head = "a" * 40
    def snapshot(self):
        return self.head, copy.deepcopy(self.files)
    def commit(self, head, files, message):
        if head != self.head:
            raise publish.HeadConflict()
        self.writes += 1
        self.files.update(copy.deepcopy(files))
        self.head = "b" * 40
        return self.head
    def contains_ref(self, minimum, actual):
        return actual == "c" * 40 and minimum == self.head


class MemoryGallery:
    def __init__(self, conclusion="success"):
        self.dispatched = []
        self.runs = []
        self.conclusion = conclusion
    def dispatch(self, workflow, ref, publication_id, data_sha):
        self.dispatched.append((publication_id, data_sha))
        self.runs.append({"id": len(self.runs) + 100, "display_title": f"video:{publication_id}:{data_sha}", "status": "completed", "conclusion": self.conclusion})
    def workflow_runs(self, workflow):
        return self.runs
    def run(self, run_id):
        return next(item for item in self.runs if str(item["id"]) == str(run_id))
    def deployment_result(self, run_id):
        publication_id, minimum = self.dispatched[-1]
        return {"publicationId": publication_id, "minimumDataRef": minimum, "dataSha": "c" * 40, "pagesDeploymentId": "pages-actual-id"}


class HTTPIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.publication = {"id": "pub-123", "jobs": [job()], "createdAt": "2026-11-12T12:00:00Z"}
        self.storage = LocalStorage(self.publication)
        self.api = ServiceAPI(self.storage.url, "test-service")
        self.git = MemoryGit()
    def tearDown(self):
        self.storage.close()

    def test_signed_streams_json_atomic_commit_mirrors_and_success_callback(self):
        gallery = MemoryGallery()
        with patch.object(publish.time, "sleep", return_value=None):
            count = publish.run_publish(self.api, self.git, gallery, "deploy.yml", "main", "42", max_tasks=1)
        self.assertEqual(count, 1)
        self.assertEqual(self.git.writes, 1)
        self.assertEqual(len(catalog.decode(self.git.files["data/video.json"])), 1)
        self.assertEqual(self.git.files["stats.json"]["total"]["desktop"], 21)
        for descriptor in publish.publication_objects(self.publication):
            actual = self.storage.uploaded[descriptor["key"]][0]
            self.assertEqual(hashlib.sha256(actual).hexdigest(), descriptor["sha256"])
        mirrored = json.loads(self.storage.uploaded["data/video.json"][0])
        self.assertEqual(catalog.decode(mirrored)[0]["description"], "说明")
        complete = [body for path, body in self.storage.calls if path.endswith("/complete")]
        self.assertEqual(complete[0]["metadataCommitSha"], self.git.head)
        self.assertEqual(complete[0]["deploymentId"], "pages-actual-id")
        self.assertEqual(complete[0]["dataSha"], "c" * 40)
        self.assertTrue(any(body.get("deploymentRunId") == "100" for path, body in self.storage.calls if path.endswith("/progress")))

    def test_failed_deployment_keeps_metadata_and_retry_reuses_verified_stage(self):
        gallery = MemoryGallery("failure")
        with patch.object(publish.time, "sleep", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "without success"):
                publish.run_publish(self.api, self.git, gallery, "deploy.yml", "main", "42", max_tasks=1)
        self.assertEqual(self.git.writes, 1)
        self.assertFalse(any(path.endswith("/complete") for path, _ in self.storage.calls))
        self.assertEqual([body for path, body in self.storage.calls if path.endswith("/fail")][0]["stage"], "deployment")
        self.storage.claimed = False
        self.publication["metadataCommitSha"] = self.git.head
        self.publication["deploymentRunId"] = "100"
        gallery.conclusion = "success"
        with patch.object(publish.time, "sleep", return_value=None):
            self.assertEqual(publish.run_publish(self.api, self.git, gallery, "deploy.yml", "main", "43", max_tasks=1), 1)
        self.assertEqual(self.git.writes, 1)
        self.assertEqual(len(gallery.dispatched), 2)

    def test_successful_workflow_without_deployment_artifact_cannot_publish(self):
        gallery = MemoryGallery()
        gallery.deployment_result = lambda run_id: {}
        with patch.object(publish.time, "sleep", return_value=None):
            with self.assertRaisesRegex(ValueError, "deployment result"):
                publish.run_publish(self.api, self.git, gallery, "deploy.yml", "main", "42", max_tasks=1)
        self.assertFalse(any(path.endswith("/complete") for path, _ in self.storage.calls))

    def test_legacy_collision_is_rejected_before_any_formal_object_is_uploaded(self):
        item = catalog.published_job(self.publication["jobs"][0])[2]
        item.pop("jobId")
        self.git.files["data/video.json"] = {"blob": catalog.encode([item])}
        with self.assertRaisesRegex(ValueError, "collid"):
            publish.run_publish(self.api, self.git, MemoryGallery(), "deploy.yml", "main", "42", max_tasks=1)
        self.assertEqual(self.storage.uploaded, {})
        self.assertFalse(any(path.endswith("/objects") for path, _ in self.storage.calls))
        self.assertEqual(self.git.writes, 0)

    def test_deployment_retry_waits_for_new_run_instead_of_reusing_old_failure(self):
        gallery = MemoryGallery("failure")
        gallery.dispatch("deploy.yml", "main", self.publication["id"], self.git.head)
        original_runs = gallery.workflow_runs
        visible_polls = 0
        def eventually_consistent_runs(workflow):
            nonlocal visible_polls
            visible_polls += 1
            runs = original_runs(workflow)
            return runs[:1] if visible_polls < 4 else runs
        gallery.workflow_runs = eventually_consistent_runs
        gallery.conclusion = "success"
        with patch.object(publish.time, "sleep", return_value=None):
            result = publish.deploy_and_wait(gallery, "deploy.yml", "main", self.publication, self.git.head, lambda run_id: None, lambda: None, time.monotonic() + 2)
        self.assertEqual(result["deploymentRunId"], "101")
        self.assertGreaterEqual(visible_polls, 4)

    def test_non_2xx_response_does_not_include_remote_body_or_url(self):
        with self.assertRaises(HTTPFailure) as captured:
            json_request(self.storage.url + "/sensitive?signature=secret")
        self.assertEqual(captured.exception.status, 503)
        self.assertNotIn("secret", str(captured.exception))
        self.assertNotIn(self.storage.url, str(captured.exception))


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg tools are required for real media integration")
class FFmpegIntegrationTests(unittest.TestCase):
    def test_real_single_frame_clip_keeps_one_unique_candidate_and_handles_late_seek(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "one-frame.mp4"
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=blue:size=96x64:rate=10", "-frames:v", "1", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(source)], check=True, capture_output=True)
            result = media.process_media(source, Path(directory) / "media", "single-frame")
            self.assertEqual(len(result["frames"]), 1)
            self.assertEqual(result["defaultFrameId"], result["frames"][0]["id"])
            self.assertGreater(result["frames"][0]["size"], 0)

    def test_processing_consumer_uses_real_http_and_restores_private_checkpoint_after_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mp4"
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=96x64:rate=10", "-t", "1", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(source)], check=True, capture_output=True)
            source_data = source.read_bytes()
            state = {"claimed": False, "result": None, "uploaded": {}, "calls": []}
            task = {"id": "process-123", "size": len(source_data), "sha256": hashlib.sha256(source_data).hexdigest()}
            class Handler(BaseHTTPRequestHandler):
                def log_message(self, *args):
                    pass
                def reply(self, value):
                    data = json.dumps(value).encode()
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                def do_GET(self):
                    if "/source?" in self.path:
                        value = {"url": base + "/raw-source"}
                        if state["result"]:
                            value["checkpoint"] = {"result": state["result"], "objects": [{"key": key, "size": len(data), "url": base + "/get/" + key} for key, data in state["uploaded"].items()]}
                        return self.reply(value)
                    data = source_data if self.path == "/raw-source" else state["uploaded"][self.path.removeprefix("/get/")]
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                def do_PUT(self):
                    state["uploaded"][self.path.removeprefix("/put/")] = self.rfile.read(int(self.headers["Content-Length"]))
                    self.reply({})
                def do_POST(self):
                    body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                    action = self.path.rsplit("/", 1)[1]
                    state["calls"].append((action, body))
                    if action == "claim":
                        value = None if state["claimed"] else task
                        state["claimed"] = True
                        return self.reply({"task": value, "leaseToken": "process-lease"})
                    if action == "objects":
                        return self.reply({"objects": [{"key": item["key"], "url": base + "/put/" + item["key"]} for item in body["objects"]]})
                    if action in ("checkpoint", "complete"):
                        state["result"] = body["result"]
                    self.reply({})
            server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            base = "http://127.0.0.1:" + str(server.server_port)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                api = ServiceAPI(base, "service-token")
                self.assertEqual(runner.run_process(api, root / "first-run", "42", max_tasks=1), 1)
                self.assertEqual(len(state["uploaded"]), 12)
                self.assertTrue(any(action == "heartbeat" for action, _ in state["calls"]))
                self.assertTrue(any(action == "complete" for action, _ in state["calls"]))
                self.assertFalse(any(action == "fail" for action, _ in state["calls"]))
                state["claimed"] = False
                commands = []
                original = media.execute
                def record(arguments, *args, **kwargs):
                    commands.append(arguments)
                    return original(arguments, *args, **kwargs)
                with patch.object(media, "execute", side_effect=record):
                    self.assertEqual(runner.run_process(api, root / "fresh-run", "43", max_tasks=1), 1)
                self.assertFalse(any("-c:v" in command for command in commands))
                self.assertTrue((root / "fresh-run/process-123/result.json").exists())
            finally:
                server.shutdown()
                server.server_close()
                thread.join()

    def test_real_unsupported_pixel_format_transcodes_and_restart_reuses_validated_outputs(self):
        with tempfile.TemporaryDirectory() as directory:
            source, output = Path(directory) / "source.mp4", Path(directory) / "media"
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=96x64:rate=10", "-t", "1", "-c:v", "libx264", "-pix_fmt", "yuv444p", str(source)], check=True, capture_output=True)
            result = media.process_media(source, output, "integration-123")
            self.assertEqual(result["metadata"]["pixelFormat"], "yuv420p")
            self.assertEqual(len(result["frames"]), 5)
            self.assertEqual(result["metadata"]["duration"], 1)
            mtime = (output / "preview.mp4").stat().st_mtime_ns
            self.assertEqual(media.process_media(source, output, "integration-123"), result)
            self.assertEqual((output / "preview.mp4").stat().st_mtime_ns, mtime)
            (output / "preview.mp4").write_bytes(b"invalid")
            regenerated = media.process_media(source, output, "integration-123")
            self.assertTrue(media.verify_file(output / "preview.mp4", regenerated["preview"]))

    def test_expired_deadline_stops_a_real_subprocess(self):
        with self.assertRaises(TimeoutError):
            media.execute(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc", "-f", "null", "-"], time.monotonic() - 1)


if __name__ == "__main__":
    unittest.main()
