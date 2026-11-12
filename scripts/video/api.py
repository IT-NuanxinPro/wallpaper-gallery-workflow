"""HTTP 调用不输出 token、响应正文或带签名的 URL。"""
import hashlib
import http.client
import json
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

USER_AGENT = "wallpaper-video-workflow/1.0"


def request_context(url, method):
    # 只输出固定API阶段，不输出任务ID、签名查询、未知路径或域名。
    path = urlsplit(url).path
    if path == "/internal/video/claim":
        stage = "internal/video/claim"
    else:
        match = re.fullmatch(r"/internal/video/(jobs|publications)/[A-Za-z0-9_-]+/(source|objects|heartbeat|complete|checkpoint|progress|fail|verify|mirrors)", path)
        if match:
            stage = f"internal/video/{match[1]}/:id/{match[2]}"
        elif path.startswith("/repos/"):
            stage = "GitHub API"
        else:
            stage = "HTTP request"
    return f"{method} {stage}"


class HTTPFailure(RuntimeError):
    def __init__(self, status, context="HTTP request"):
        self.status = status
        super().__init__(f"{context} failed (HTTP {status})")


def json_request(url, method="GET", body=None, headers=None, timeout=60):
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode()
    context = request_context(url, method)
    request = urllib.request.Request(url, data=data, method=method, headers={"Accept": "application/json", "User-Agent": USER_AGENT, **({"Content-Type": "application/json"} if data is not None else {}), **(headers or {})})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = response.read()
            return json.loads(data) if data else {}
    except urllib.error.HTTPError as error:
        status = error.code
        error.close()
        raise HTTPFailure(status, context) from None
    except urllib.error.URLError:
        raise RuntimeError(context + " connection failed") from None


class ServiceAPI:
    def __init__(self, base_url, token):
        if not base_url or not token:
            raise ValueError("VIDEO_API_URL and VIDEO_SERVICE_TOKEN are required")
        self.base_url = base_url.rstrip("/")
        self.headers = {"X-Video-Service-Token": token}

    def get(self, path):
        return json_request(self.base_url + path, headers=self.headers)

    def post(self, path, body):
        return json_request(self.base_url + path, "POST", body, self.headers)


def read_signed(url):
    try:
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        return urllib.request.urlopen(request, timeout=60)
    except urllib.error.HTTPError as error:
        status = error.code
        error.close()
        raise HTTPFailure(status, "Signed source request") from None
    except urllib.error.URLError:
        raise RuntimeError("Signed source connection failed") from None


def put_stream(url, source, size, content_type, expected_sha256=None, deadline=None, check=None):
    parsed = urlsplit(url)
    if parsed.scheme not in ("https", "http") or not parsed.hostname:
        raise ValueError("Invalid signed upload URL")
    connection_type = http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
    connection = connection_type(parsed.hostname, parsed.port, timeout=60)
    digest, written = hashlib.sha256(), 0
    try:
        connection.putrequest("PUT", parsed.path + ("?" + parsed.query if parsed.query else ""))
        connection.putheader("Content-Type", content_type)
        connection.putheader("Content-Length", str(size))
        connection.putheader("User-Agent", USER_AGENT)
        connection.endheaders()
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            if deadline and time.monotonic() >= deadline:
                raise TimeoutError("Object transfer timed out")
            if check:
                check()
            written += len(chunk)
            if written > size:
                raise ValueError("Object stream exceeds expected size")
            digest.update(chunk)
            connection.send(chunk)
        if written != size or expected_sha256 and digest.hexdigest() != expected_sha256:
            raise ValueError("Object stream size or SHA256 mismatch")
        response = connection.getresponse()
        response.read()
        if not 200 <= response.status < 300:
            raise HTTPFailure(response.status, "Signed object PUT")
    except (OSError, http.client.HTTPException):
        raise RuntimeError("Signed object transfer failed") from None
    finally:
        connection.close()
    return {"size": written, "sha256": digest.hexdigest()}


def upload_file(url, path, descriptor, deadline=None, check=None):
    with Path(path).open("rb") as source:
        return put_stream(url, source, descriptor["size"], descriptor["contentType"], descriptor.get("sha256"), deadline, check)


def github_artifact_bytes(url, headers, maximum=1048576):
    # GitHub 返回跨域临时下载地址时不把 GitHub token 转发给对象存储域名。
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, request, file, code, message, response_headers, target):
            return None
    opener = urllib.request.build_opener(NoRedirect())
    try:
        response = opener.open(urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **headers}), timeout=60)
    except urllib.error.HTTPError as error:
        status, location = error.code, error.headers.get("Location")
        error.close()
        if status not in (301, 302, 303, 307, 308) or not location or urlsplit(location).scheme != "https":
            raise HTTPFailure(status, "GitHub deployment artifact request") from None
        response = read_signed(location)
    except urllib.error.URLError:
        raise RuntimeError("GitHub deployment artifact connection failed") from None
    with response:
        data = response.read(maximum + 1)
    if len(data) > maximum:
        raise ValueError("Deployment result artifact is too large")
    return data
