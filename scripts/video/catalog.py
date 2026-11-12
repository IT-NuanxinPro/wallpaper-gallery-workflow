"""只合并视频 JSON；不根据本地媒体是否存在删除历史记录。"""
import copy
import hashlib
import json
from datetime import datetime, timezone
from pathlib import PurePosixPath
from urllib.parse import quote, unquote

_SOURCE = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/="
_TARGET = "QWERTYUIOPASDFGHJKLZXCVBNMqwertyuiopasdfghjklzxcvbnm5678901234-_."


def encode(value):
    import base64
    raw = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    mapped = base64.b64encode(raw.encode()).decode().translate(str.maketrans(_SOURCE, _TARGET))
    return "v1." + mapped[::-1]


def decode(value):
    import base64
    if isinstance(value, dict):
        value = value["blob"]
    if not isinstance(value, str) or not value.startswith("v1."):
        raise ValueError("Unsupported video JSON encoding")
    raw = value[3:][::-1].translate(str.maketrans(_TARGET, _SOURCE))
    return json.loads(base64.b64decode(raw, validate=True).decode())


def safe_segment(value):
    if not isinstance(value, str) or not value or value.strip() != value or value in (".", "..") or any(c in value for c in ("/", "\\", "\0")):
        raise ValueError("Unsafe video path segment")
    if any(ord(c) < 32 for c in value):
        raise ValueError("Unsafe video path control character")
    return value


def final_keys(job):
    review = job["review"]
    usage = safe_segment(review["usage"])
    if usage not in ("desktop", "mobile", "social-cover"):
        raise ValueError("Unsupported video usage")
    topic = safe_segment(review["topic"])
    filename = safe_segment(review["filename"])
    basename = filename.rsplit(".", 1)[0] if "." in filename else filename
    safe_segment(basename)
    suffix = safe_segment(str(job["id"])[:8])
    tail = f"video/{usage}/{topic}/{basename}-{suffix}"
    expected = {"download": f"wallpaper/{tail}.mp4", "preview": f"preview/{tail}.mp4", "thumbnail": f"thumbnail/{tail}.webp"}
    frozen = job.get("finalKeys")
    if frozen is not None and frozen != expected:
        raise ValueError("Frozen object paths do not match approved review")
    return expected


def normalized_path(value):
    value = unquote(value).lstrip("/")
    if not value.startswith("wallpaper/video/") or ".." in PurePosixPath(value).parts:
        raise ValueError("Invalid published video path")
    return value


def public_path(key):
    # 沿用现有数据：目录保持原样，文件名单独 URL 编码。
    directory, filename = key.rsplit("/", 1)
    return "/" + directory + "/" + quote(filename, safe="~!*'()")


def from_metadata(key, data):
    parts = key.split("/")
    filename = data.get("filename") or parts[-1]
    basename = parts[-1].rsplit(".", 1)[0]
    tail = "/".join(parts[2:-1] + [basename])
    ai = data.get("ai") or {}
    item = copy.deepcopy(data)
    item.update({"id": data.get("id") or "video-" + hashlib.sha256(key.encode()).hexdigest()[:20],
                 "filename": filename, "path": public_path(key), "category": data.get("category") or parts[2],
                 "thumbnailPath": data.get("thumbnailPath") or public_path(f"thumbnail/video/{tail}.webp"),
                 "previewPath": data.get("previewPath") or public_path(f"preview/video/{tail}.mp4"), "format": "MP4", "mediaType": "video",
                 "keywords": ai.get("keywords", []), "description": ai.get("description", ""), "displayTitle": ai.get("displayTitle", "")})
    item["tags"] = list(dict.fromkeys([item["category"], *([data["subcategory"]] if data.get("subcategory") else []), *item["keywords"]]))
    return item


def published_job(job, publication_id=None, publication_job_count=None):
    keys = final_keys(job)
    review, result = job["review"], job["result"]
    info = result["metadata"]
    longest = max(info["width"], info["height"])
    # 与图床process-metadata.js的既有分辨率徽章阈值保持一致，仅用于本次新增视频。
    thresholds = ((15360, "16K", "danger"), (7680, "8K", "danger"), (5120, "5K+", "danger"),
                  (4096, "4K+", "warning"), (3840, "4K", "success"), (2048, "2K", "info"),
                  (1920, "超清", "primary"), (1280, "高清", "secondary"), (0, "标清", "secondary"))
    label, badge_type = next((label, badge_type) for minimum, label, badge_type in thresholds if longest >= minimum)
    ai = copy.deepcopy(review.get("ai") or {})
    ai.update({"keywords": review["keywords"], "description": review["description"], "displayTitle": review["displayTitle"]})
    metadata = {"id": "video-" + job["id"], "jobId": job["id"], "category": review["usage"], "subcategory": review["topic"],
                "usage": review["usage"], "topic": review["topic"], "filename": keys["download"].rsplit("/", 1)[1],
                "createdAt": job.get("createdAt"), "size": result["download"]["size"], "format": "mp4", "mediaType": "video",
                "resolution": {"width": info["width"], "height": info["height"], "label": label, "type": badge_type}, "duration": info["duration"], "codec": info.get("codec", "h264"), "ai": ai,
                "sha256": result["download"]["sha256"], "storage": "r2",
                "previewPath": public_path(keys["preview"]), "thumbnailPath": public_path(keys["thumbnail"])}
    if publication_id:
        metadata.update({"publicationId": publication_id, "publicationJobCount": publication_job_count})
    item = from_metadata(keys["download"], metadata)
    item.update({"previewPath": public_path(keys["preview"]), "thumbnailPath": public_path(keys["thumbnail"]), "sha": result["download"]["sha256"]})
    return keys["download"], metadata, item


def build_catalog(metadata, published, jobs, stats, now=None, existing_files=None, publication_id=None):
    now = now or datetime.now(timezone.utc).isoformat()
    metadata, stats = copy.deepcopy(metadata), copy.deepcopy(stats)
    existing_files = existing_files or {}
    metadata.setdefault("images", {})
    by_path, ids = {}, {}
    for entry in published:
        key = normalized_path(entry["path"])
        identifier = entry.get("id")
        if identifier and identifier in ids and ids[identifier] != key:
            raise ValueError("Published video identity conflicts")
        if key in by_path and by_path[key] != entry:
            raise ValueError("Published video path conflicts")
        if identifier:
            ids[identifier] = key
        by_path[key] = copy.deepcopy(entry)
    for key, entry in metadata["images"].items():
        normalized_path(key)
        fallback = from_metadata(key, entry)
        if key in by_path:
            by_path[key] = {**fallback, **by_path[key]}
        else:
            by_path[key] = fallback
    for job in jobs:
        key, data, entry = published_job(job, publication_id, len(jobs) if publication_id else None)
        existing = by_path.get(key, {})
        existing_metadata = metadata["images"].get(key, {})
        if existing and existing.get("jobId") != job["id"]:
            raise ValueError("Frozen video collides with existing publication")
        if existing_metadata and existing_metadata.get("jobId") != job["id"]:
            raise ValueError("Frozen video collides with existing historical metadata")
        if existing:
            expected_fields = ("jobId", "publicationId", "publicationJobCount", "path", "previewPath", "thumbnailPath", "filename", "category", "subcategory", "usage", "topic", "displayTitle", "description", "keywords", "sha", "size", "duration", "resolution", "ai")
            if any(existing.get(field) != entry.get(field) for field in expected_fields):
                raise ValueError("Frozen video review or assets changed for an existing job")
        if existing_metadata and any(existing_metadata.get(field) != value for field, value in data.items()):
            raise ValueError("Frozen video metadata changed for an existing job")
        # 相同冻结任务重试保持身份和未知字段。
        metadata["images"][key] = {**metadata["images"].get(key, {}), **data}
        by_path[key] = {**existing, **entry, "id": existing.get("id", entry["id"])}
    # 与既有生成器保持同日文件名升序，stable sort 再应用日期降序。
    items = sorted(by_path.values(), key=lambda x: str(x.get("filename") or ""))
    items.sort(key=lambda x: str(x.get("createdAt") or ""), reverse=True)
    groups = {}
    for item in items:
        category = safe_segment(item.get("category") or "未分类")
        groups.setdefault(category, []).append(item)
    categories = []
    for category, entries in groups.items():
        subcategories = {}
        for entry in entries:
            subcategory = entry.get("subcategory")
            if subcategory:
                subcategories[subcategory] = subcategories.get(subcategory, 0) + 1
        index = {"id": category.replace(" ", "-").lower(), "name": category, "count": len(entries), "thumbnail": entries[0].get("thumbnailPath") or entries[0]["path"], "file": category + ".json"}
        if subcategories:
            index["subcategories"] = [{"name": name, "count": count} for name, count in sorted(subcategories.items(), key=lambda x: (-x[1], x[0]))]
        categories.append(index)
    categories.sort(key=lambda x: (-x["count"], x["name"]))
    metadata.update({"version": metadata.get("version", 2), "series": metadata.get("series", "video"), "count": len(metadata["images"]), "lastUpdated": now})
    files = {"metadata/video.json": metadata}
    def envelope(path, entries, **fields):
        files[path] = {**copy.deepcopy(existing_files.get(path, {})), "generatedAt": now, "series": "video", "total": len(entries), "blob": encode(entries), "schema": 2, **fields}
    envelope("data/video.json", items, schema=1, seriesName="video", env="production")
    envelope("data/video/index.json", categories, total=len(items), categoryCount=len(categories), seriesName="video", env="production")
    envelope("data/video/latest.json", items[:40], category="latest")
    for category, entries in groups.items():
        envelope(f"data/video/{category}.json", entries, category=category)
    stats.setdefault("total", {})["video"] = len(items)
    stats.setdefault("series", {})["video"] = {**stats.get("series", {}).get("video", {}), "count": len(items), "categories": [x["name"] for x in categories]}
    stats["lastUpdated"] = now
    files["stats.json"] = stats
    return files
