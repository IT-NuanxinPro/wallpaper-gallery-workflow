"""正式对象验收 → 原子 JSON 提交 → R2 镜像 → 跟踪真实部署。"""
import base64
import hashlib
import io
import json
import re
import time
import zipfile
from datetime import datetime, timezone
from urllib.parse import quote, urlencode

from . import catalog
from .api import HTTPFailure, github_artifact_bytes, json_request, put_stream, read_signed
from .runner import LeaseHeartbeat


class HeadConflict(RuntimeError):
    pass


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode()


class GitHub:
    def __init__(self, repository, token, branch="main"):
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository or "") or not token:
            raise ValueError("A GitHub repository and REPO_TOKEN are required")
        self.repository, self.branch = repository, branch
        self.base = "https://api.github.com/repos/" + repository
        self.headers = {"Authorization": "Bearer " + token, "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "wallpaper-video-workflow"}

    def request(self, path, method="GET", body=None):
        return json_request(self.base + path, method, body, self.headers)

    def snapshot(self):
        head = self.request("/git/ref/heads/" + quote(self.branch, safe=""))["object"]["sha"]
        commit = self.request("/git/commits/" + head)
        tree = self.request("/git/trees/" + commit["tree"]["sha"] + "?recursive=1")
        if tree.get("truncated"):
            raise ValueError("GitHub tree is truncated; refusing an incomplete video catalog")
        files = {}
        for entry in tree["tree"]:
            path = entry["path"]
            if entry["type"] == "blob" and (path in ("metadata/video.json", "stats.json", "data/video.json") or path.startswith("data/video/") and path.endswith(".json")):
                blob = self.request("/git/blobs/" + entry["sha"])
                if blob.get("encoding") != "base64":
                    raise ValueError("Unexpected GitHub blob encoding")
                files[path] = json.loads(base64.b64decode(blob["content"]))
        if not {"metadata/video.json", "data/video.json", "stats.json"}.issubset(files):
            raise ValueError("Required historical video JSON is missing")
        return head, files

    def commit(self, head, files, message):
        parent = self.request("/git/commits/" + head)
        entries = []
        for path, value in files.items():
            blob = self.request("/git/blobs", "POST", {"content": json_bytes(value).decode(), "encoding": "utf-8"})
            entries.append({"path": path, "mode": "100644", "type": "blob", "sha": blob["sha"]})
        tree = self.request("/git/trees", "POST", {"base_tree": parent["tree"]["sha"], "tree": entries})
        commit = self.request("/git/commits", "POST", {"message": message, "tree": tree["sha"], "parents": [head]})
        try:
            self.request("/git/refs/heads/" + quote(self.branch, safe=""), "PATCH", {"sha": commit["sha"], "force": False})
        except HTTPFailure as error:
            if error.status in (409, 422):
                raise HeadConflict("GitHub HEAD changed during catalog publication") from None
            raise
        return commit["sha"]

    def dispatch(self, workflow, ref, publication_id, minimum_data_ref):
        return self.request("/actions/workflows/" + quote(workflow, safe="") + "/dispatches", "POST", {"ref": ref, "inputs": {"publication_id": publication_id, "minimum_data_ref": minimum_data_ref}})

    def workflow_runs(self, workflow):
        return self.request("/actions/workflows/" + quote(workflow, safe="") + "/runs?" + urlencode({"event": "workflow_dispatch", "per_page": 100}))["workflow_runs"]

    def run(self, run_id):
        if not str(run_id).isdigit():
            raise ValueError("Invalid GitHub deployment run id")
        return self.request("/actions/runs/" + str(run_id))

    def deployment_result(self, run_id):
        artifacts = self.request("/actions/runs/" + str(run_id) + "/artifacts?name=video-publication-result&per_page=100")["artifacts"]
        artifacts = [item for item in artifacts if item.get("name") == "video-publication-result" and not item.get("expired")]
        if not artifacts:
            raise ValueError("Gallery deployment result artifact is missing or expired")
        artifact = max(artifacts, key=lambda item: item["id"])
        if artifact.get("size_in_bytes", 0) > 1048576:
            raise ValueError("Gallery deployment result artifact is too large")
        data = github_artifact_bytes(self.base + "/actions/artifacts/" + str(artifact["id"]) + "/zip", self.headers)
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = [item for item in archive.infolist() if not item.is_dir() and item.filename.endswith(".json")]
            if len(entries) != 1 or entries[0].file_size > 1048576:
                raise ValueError("Gallery deployment result artifact must contain one small JSON file")
            return json.loads(archive.read(entries[0]))

    def contains_ref(self, minimum, actual):
        comparison = self.request("/compare/" + quote(minimum, safe="") + "..." + quote(actual, safe=""))
        return comparison.get("status") in ("ahead", "identical")


def publication_objects(publication):
    objects = []
    for job in publication["jobs"]:
        keys, result = catalog.final_keys(job), job["result"]
        frame = next((item for item in result["frames"] if item["id"] == job["review"]["selectedFrameId"]), None)
        if not frame:
            raise ValueError("Frozen selected frame is missing")
        for kind in ("download", "preview"):
            source = result[kind]
            objects.append({"sourceKey": source["key"], "key": keys[kind], "contentType": "video/mp4", "size": source["size"], "sha256": source["sha256"]})
        if not frame.get("thumbnailSize") or not frame.get("thumbnailSha256"):
            raise ValueError("Frozen thumbnail lacks verifiable size/SHA256; reprocess the video")
        objects.append({"sourceKey": frame["thumbnailKey"], "key": keys["thumbnail"], "contentType": "image/webp", "size": frame["thumbnailSize"], "sha256": frame["thumbnailSha256"]})
    if len({item["key"] for item in objects}) != len(objects):
        raise ValueError("Publication object paths collide")
    return objects


def contains_jobs(files, publication):
    metadata = files.get("metadata/video.json", {}).get("images", {})
    try:
        published = {catalog.normalized_path(item["path"]): item for item in catalog.decode(files["data/video.json"])}
        for job in publication["jobs"]:
            key, expected_metadata, expected_item = catalog.published_job(job, publication["id"], len(publication["jobs"]))
            if key not in metadata or key not in published:
                return False
            for field, value in expected_metadata.items():
                if metadata[key].get(field) != value:
                    return False
            for field in ("jobId", "path", "previewPath", "thumbnailPath", "displayTitle", "description", "keywords", "sha"):
                if published[key].get(field) != expected_item.get(field):
                    return False
        return True
    except (KeyError, ValueError, TypeError):
        return False


def commit_catalog(github, publication, attempts=5):
    for _ in range(attempts):
        head, previous = github.snapshot()
        # 从当前最新 HEAD 确认完整冻结记录，不能只信任上次保存的 SHA。
        if contains_jobs(previous, publication):
            return head, previous
        metadata = previous["metadata/video.json"]
        published = catalog.decode(previous["data/video.json"])
        files = catalog.build_catalog(metadata, published, publication["jobs"], previous["stats.json"], now=publication.get("createdAt"), existing_files=previous, publication_id=publication["id"])
        try:
            sha = github.commit(head, files, "feat(video): publish " + publication["id"])
            return sha, {**previous, **files}
        except HeadConflict:
            continue
    raise HeadConflict("GitHub HEAD kept changing; catalog was not published")


def deployment_succeeded(run):
    return run.get("status") == "completed" and run.get("conclusion") == "success"


def validate_deployment_result(result, publication_id, minimum_data_ref):
    if not isinstance(result, dict) or result.get("publicationId") != publication_id or result.get("minimumDataRef") != minimum_data_ref or not re.fullmatch(r"[a-f0-9]{40}", str(result.get("dataSha", ""))) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", str(result.get("pagesDeploymentId", ""))):
        raise ValueError("Gallery deployment result does not match the publication or actual deployment")
    return result


def deploy_and_wait(gallery, workflow, ref, publication, data_sha, progress, check, deadline, poll_seconds=15):
    marker = f"video:{publication['id']}:{data_sha}"
    saved_id = publication.get("deploymentRunId")
    run = gallery.run(saved_id) if saved_id else None
    if run and run.get("display_title") != marker:
        run = None
    # 重试或 dispatch 后进程中断时，按唯一 run-name 找回真实运行。
    if run is None:
        candidates = [item for item in gallery.workflow_runs(workflow) if item.get("display_title") == marker]
        run = max(candidates, key=lambda item: item["id"]) if candidates else None
    if run and run.get("status") == "completed" and not deployment_succeeded(run):
        run = None
    if run is not None:
        progress(str(run["id"]))
    dispatch_after_id = 0
    if run is None:
        # dispatch 返回时新 run 可能尚未出现在列表中，不能再次捡起旧失败 run。
        known = [item for item in gallery.workflow_runs(workflow) if item.get("display_title") == marker]
        dispatch_after_id = max((item["id"] for item in known), default=0)
        gallery.dispatch(workflow, ref, publication["id"], data_sha)
    while time.monotonic() < deadline:
        check()
        if run is None:
            candidates = [item for item in gallery.workflow_runs(workflow) if item.get("display_title") == marker and item["id"] > dispatch_after_id]
            if candidates:
                run = max(candidates, key=lambda item: item["id"])
                progress(str(run["id"]))
        else:
            run = gallery.run(run["id"])
            if run.get("display_title") != marker:
                raise ValueError("Deployment run no longer matches publication and data SHA")
            if run.get("status") == "completed":
                if not deployment_succeeded(run):
                    raise RuntimeError("Gallery deployment completed without success")
                result = validate_deployment_result(gallery.deployment_result(run["id"]), publication["id"], data_sha)
                return {**result, "deploymentRunId": str(run["id"])}
        time.sleep(min(poll_seconds, max(0, deadline - time.monotonic())))
    raise TimeoutError("Gallery deployment did not complete before timeout")


def run_publish(api, github, gallery, workflow, ref, run_id, max_tasks=10, timeout_seconds=5400):
    processed = 0
    for _ in range(max_tasks):
        claim = api.post("/internal/video/claim", {"kind": "publish", "runId": run_id})
        publication = claim.get("task")
        if publication is None:
            return processed
        token, stage = claim["leaseToken"], "objects"
        root = "/internal/video/publications/" + quote(publication["id"], safe="")
        deadline = time.monotonic() + timeout_seconds
        def callback(action, **body):
            return api.post(root + "/" + action, {"leaseToken": token, **body})
        try:
            with LeaseHeartbeat(api, root + "/heartbeat", token) as heartbeat:
                stage = "preflight"
                _, previous = github.snapshot()
                # 正式对象 PUT 前验证历史路径和冻结身份，避免 JSON 拒绝时对象已被覆盖。
                catalog.build_catalog(previous["metadata/video.json"], catalog.decode(previous["data/video.json"]), publication["jobs"], previous["stats.json"], now=publication.get("createdAt"), existing_files=previous, publication_id=publication["id"])
                stage = "objects"
                objects = publication_objects(publication)
                callback("progress", status="publishing")
                verified = False
                try:
                    callback("verify", objects=[{key: item[key] for key in ("key", "size", "contentType")} for item in objects])
                    verified = True
                except HTTPFailure as error:
                    if error.status != 409:
                        raise
                if not verified:
                    # 每次只为一个对象签名，避免长视频复制使后续签名过期。
                    for descriptor in objects:
                        heartbeat.check()
                        signed = callback("objects", objects=[{key: descriptor[key] for key in ("sourceKey", "key", "contentType", "size")}])["objects"][0]
                        if signed["key"] != descriptor["key"] or signed["sourceKey"] != descriptor["sourceKey"]:
                            raise ValueError("Signed formal object differs from frozen publication")
                        with read_signed(signed["sourceUrl"]) as source:
                            put_stream(signed["url"], source, descriptor["size"], descriptor["contentType"], descriptor["sha256"], deadline, heartbeat.check)
                    callback("verify", objects=[{key: item[key] for key in ("key", "size", "contentType")} for item in objects])
                stage = "metadata"
                heartbeat.check()
                data_sha, files = commit_catalog(github, publication)
                callback("progress", status="publishing", metadataCommitSha=data_sha)
                stage = "mirrors"
                mirrors = {path: value for path, value in files.items() if path == "data/video.json" or path.startswith("data/video/") and path.endswith(".json")}
                for key, value in mirrors.items():
                    heartbeat.check()
                    signed = callback("mirrors", objects=[{"key": key, "contentType": "application/json"}])["objects"][0]
                    if signed["key"] != key:
                        raise ValueError("Signed mirror key does not match requested JSON")
                    data = json_bytes(value)
                    put_stream(signed["url"], io.BytesIO(data), len(data), "application/json", hashlib.sha256(data).hexdigest(), deadline, heartbeat.check)
                stage = "deployment"
                callback("progress", status="deploying", metadataCommitSha=data_sha)
                def progress(deployment_run_id):
                    callback("progress", status="deploying", metadataCommitSha=data_sha, deploymentRunId=deployment_run_id)
                deployment = deploy_and_wait(gallery, workflow, ref, publication, data_sha, progress, heartbeat.check, deadline)
                if not github.contains_ref(data_sha, deployment["dataSha"]):
                    raise ValueError("Actual gallery data SHA does not contain the minimum metadata commit")
                stage = "complete"
                heartbeat.check()
                callback("complete", metadataCommitSha=data_sha, deploymentId=deployment["pagesDeploymentId"], dataSha=deployment["dataSha"])
            processed += 1
            print("Published video publication " + publication["id"], flush=True)
        except Exception as error:
            try:
                callback("fail", stage=stage, message=f"{stage} failed ({type(error).__name__}); inspect Actions diagnostics")
            except Exception as callback_error:
                raise RuntimeError("Publication failed and failure callback was rejected") from callback_error
            raise
    return processed
