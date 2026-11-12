"""持久队列消费者；任何处理或回调失败均使 Actions 返回非零。"""
import hashlib
import json
import signal
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import quote

from . import media
from .api import read_signed, upload_file


@contextmanager
def processing_timeout(seconds):
    # Actions Linux 和本地 macOS 均支持 POSIX 定时器，网络阻塞也不能绕过30分钟上限。
    previous_handler = signal.getsignal(signal.SIGALRM)
    previous_timer = signal.getitimer(signal.ITIMER_REAL)
    def expired(signum, frame):
        raise TimeoutError("Video task exceeded 30 minutes")
    signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, *previous_timer)
        signal.signal(signal.SIGALRM, previous_handler)


class LeaseHeartbeat:
    def __init__(self, api, path, token, interval=20):
        self.api, self.path, self.token, self.interval = api, path, token, interval
        self.stopped = threading.Event()
        self.error = None
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        while not self.stopped.wait(self.interval):
            try:
                self.api.post(self.path, {"leaseToken": self.token})
            except Exception as error:
                self.error = error
                return

    def check(self):
        if self.error:
            raise RuntimeError("Lease heartbeat failed; processing stopped") from self.error

    def __enter__(self):
        self.api.post(self.path, {"leaseToken": self.token})
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.stopped.set()
        self.thread.join(timeout=60)


def download_source(url, path, size, sha256, deadline, check=None, max_bytes=250000000):
    path = Path(path)
    if media.verify_file(path, {"size": size, "sha256": sha256}):
        return
    temporary = path.with_suffix(".partial")
    digest, written = hashlib.sha256(), 0
    with read_signed(url) as response, temporary.open("wb") as output:
        for chunk in iter(lambda: response.read(1024 * 1024), b""):
            if time.monotonic() >= deadline:
                raise TimeoutError("Source download exceeded processing deadline")
            if check:
                check()
            written += len(chunk)
            if written > size or max_bytes and written > max_bytes:
                raise ValueError("Source is larger than approved upload")
            digest.update(chunk)
            output.write(chunk)
    if written != size or digest.hexdigest() != sha256:
        raise ValueError("Source size or SHA256 does not match uploaded job")
    temporary.replace(path)


def restore_checkpoint(source, directory, job_id, source_sha256, deadline, check=None):
    checkpoint = source.get("checkpoint")
    if not checkpoint or not checkpoint.get("objects"):
        return False
    result = checkpoint["result"]
    files = media.manifest_files(result)
    signed = {item["key"]: item for item in checkpoint["objects"]}
    if any(descriptor["key"] not in signed for _, descriptor in files):
        return False
    for filename, descriptor in files:
        if not descriptor["key"].startswith(f"jobs/{job_id}/"):
            raise ValueError("Checkpoint object belongs to another job")
        download_source(signed[descriptor["key"]]["url"], directory / filename, descriptor["size"], descriptor["sha256"], deadline, check, max_bytes=None)
    saved = {"pipelineVersion": media.PIPELINE_VERSION, "sourceSha256": source_sha256, "jobId": job_id, "result": result}
    (directory / "result.json").write_text(json.dumps(saved, ensure_ascii=False, indent=2))
    return True


def complete_process(api, job_id, token, result):
    return api.post(f"/internal/video/jobs/{quote(job_id, safe='')}/complete", {"leaseToken": token, "result": result})


def run_process(api, cache_dir, run_id, max_tasks=20):
    cache_dir = Path(cache_dir)
    processed = 0
    for _ in range(max_tasks):
        claim = api.post("/internal/video/claim", {"kind": "process", "runId": run_id})
        task = claim.get("task")
        if task is None:
            return processed
        job_id, token = task["id"], claim["leaseToken"]
        if not job_id or any(character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for character in job_id):
            raise ValueError("Unsafe claimed job id")
        root = f"/internal/video/jobs/{quote(job_id, safe='')}"
        directory = cache_dir / job_id
        directory.mkdir(parents=True, exist_ok=True)
        deadline, stage = time.monotonic() + media.TIMEOUT_SECONDS, "source"
        try:
            with processing_timeout(media.TIMEOUT_SECONDS), LeaseHeartbeat(api, root + "/heartbeat", token) as heartbeat:
                source = api.get(root + "/source?leaseToken=" + quote(token, safe=""))
                source_path = directory / "source.bin"
                download_source(source["url"], source_path, task["size"], task["sha256"], deadline, heartbeat.check)
                stage = "restore"
                restore_checkpoint(source, directory, job_id, task["sha256"], deadline, heartbeat.check)
                stage = "transcode"
                result = media.process_media(source_path, directory, job_id, task["sha256"], deadline, heartbeat.check)
                stage = "upload"
                api.post(root + "/checkpoint", {"leaseToken": token, "result": result})
                files = media.manifest_files(result)
                signed = api.post(root + "/objects", {"leaseToken": token, "objects": [{"key": item["key"], "contentType": item["contentType"]} for _, item in files]})
                urls = {item["key"]: item["url"] for item in signed["objects"]}
                for filename, descriptor in files:
                    heartbeat.check()
                    upload_file(urls[descriptor["key"]], directory / filename, descriptor, deadline, heartbeat.check)
                stage = "complete"
                heartbeat.check()
                complete_process(api, job_id, token, result)
            processed += 1
            print(f"Processed video job {job_id}", flush=True)
        except Exception as error:
            # stage/message 是固定内容，不把任意异常中的签名 URL 回传日志。
            try:
                api.post(root + "/fail", {"leaseToken": token, "stage": stage, "message": f"{stage} failed ({type(error).__name__}); inspect Actions diagnostics"})
            except Exception as callback_error:
                raise RuntimeError(f"Video {stage} failed and failure callback was rejected") from callback_error
            raise
    return processed
