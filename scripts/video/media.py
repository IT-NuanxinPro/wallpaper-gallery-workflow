"""FFmpeg 产物生成、完整性验收及可验证的本地断点复用。"""
import hashlib
import json
import math
import subprocess
import time
from collections import Counter
from pathlib import Path

PIPELINE_VERSION = 1
TIMEOUT_SECONDS = 1800


def execute(arguments, deadline, check=None):
    if check:
        check()
    process = subprocess.Popen(arguments, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Video processing exceeded 30 minutes")
            try:
                stdout, stderr = process.communicate(timeout=min(5, remaining))
                break
            except subprocess.TimeoutExpired:
                if check:
                    check()
        if process.returncode:
            # FFmpeg 只接收本地路径，不记录带签名的 URL。
            raise RuntimeError("Media command failed: " + stderr.decode(errors="replace")[-2500:])
        return stdout
    except BaseException:
        process.kill()
        process.communicate()
        raise


def probe(path, deadline=None, check=None):
    deadline = deadline or time.monotonic() + 60
    return json.loads(execute(["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)], deadline, check))


def video_stream(info):
    streams = [stream for stream in info.get("streams", []) if stream.get("codec_type") == "video" and not stream.get("disposition", {}).get("attached_pic")]
    if not streams:
        raise ValueError("Source has no video stream")
    return streams[0]


def reject_hdr(info):
    stream = video_stream(info)
    transfer = stream.get("color_transfer", "")
    side_data = json.dumps(stream.get("side_data_list", [])).lower()
    if transfer in ("smpte2084", "arib-std-b67") or any(marker in side_data for marker in ("dovi", "dolby vision", "mastering display", "content light level")):
        raise ValueError("HDR/Dolby Vision is unsupported: explicit SDR tone mapping is required before upload")
    if stream.get("color_primaries") == "bt2020" and transfer not in ("bt709", "gamma22", "gamma28"):
        raise ValueError("HDR/wide-gamut transfer is ambiguous; refusing an unverified color conversion")


def download_arguments(info):
    reject_hdr(info)
    stream = video_stream(info)
    compatible = stream.get("codec_name") == "h264" and stream.get("pix_fmt") == "yuv420p" and str(stream.get("field_order", "progressive")) in ("progressive", "unknown")
    args = ["-map", "0:v:0", "-map", "0:a:0?", "-c:v"]
    if compatible:
        args += ["copy"]
    else:
        args += ["libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p"]
    audio = next((stream for stream in info.get("streams", []) if stream.get("codec_type") == "audio"), None)
    args += ["-c:a", "copy" if audio and audio.get("codec_name") == "aac" else "aac"]
    if audio and audio.get("codec_name") != "aac":
        args += ["-b:a", "192k"]
    return args + ["-movflags", "+faststart", "-map_metadata", "0"]


def describe(path, content_type, key):
    path = Path(path)
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return {"key": key, "size": path.stat().st_size, "contentType": content_type, "sha256": digest.hexdigest()}


def verify_file(path, descriptor):
    path = Path(path)
    return path.is_file() and path.stat().st_size == descriptor.get("size") and describe(path, descriptor.get("contentType"), descriptor.get("key"))["sha256"] == descriptor.get("sha256")


def frame_score(pixels):
    if not pixels:
        return 0.0
    histogram = Counter(pixels)
    length = len(pixels)
    mean = sum(value * count for value, count in histogram.items()) / length
    variance = sum((value - mean) ** 2 * count for value, count in histogram.items()) / length
    entropy = -sum((count / length) * math.log2(count / length) for count in histogram.values())
    detail = sum(abs(pixels[index] - pixels[index - 1]) for index in range(1, length)) / max(1, length - 1)
    exposure = min(1.0, mean / 24.0, (255 - mean) / 24.0)
    return round(exposure * (entropy * 10 + math.sqrt(variance) + detail / 4), 6)


def duration(info):
    value = float(info.get("format", {}).get("duration") or video_stream(info).get("duration") or 0)
    if not math.isfinite(value) or value <= 0:
        raise ValueError("Source has no finite positive duration")
    return value


def display_dimensions(stream):
    width, height = int(stream["width"]), int(stream["height"])
    rotation = next((entry.get("rotation", 0) for entry in stream.get("side_data_list", []) if "rotation" in entry), 0)
    if abs(int(rotation)) % 180 == 90:
        width, height = height, width
    return width, height


def is_faststart(path):
    with Path(path).open("rb") as stream:
        while True:
            header = stream.read(8)
            if len(header) != 8:
                return False
            size, kind = int.from_bytes(header[:4], "big"), header[4:]
            header_size = 8
            if size == 1:
                extended = stream.read(8)
                if len(extended) != 8:
                    return False
                size, header_size = int.from_bytes(extended, "big"), 16
            if kind == b"moov":
                return True
            if kind == b"mdat" or size < header_size:
                return False
            stream.seek(size - header_size, 1)


def validate_video(path, source, preview, deadline, check=None):
    info = probe(path, deadline, check)
    stream = video_stream(info)
    reject_hdr(info)
    if stream.get("codec_name") != "h264" or stream.get("pix_fmt") != "yuv420p":
        raise ValueError("Output is not browser-compatible H264 yuv420p")
    if abs(duration(info) - duration(source)) > max(0.25, duration(source) * 0.005):
        raise ValueError("Output duration differs from full source")
    width, height = display_dimensions(stream)
    source_width, source_height = display_dimensions(video_stream(source))
    if preview:
        if max(width, height) > 1920 or width > source_width or height > source_height:
            raise ValueError("Preview exceeds dimensions or upscales source")
        if any(entry.get("codec_type") == "audio" for entry in info.get("streams", [])):
            raise ValueError("Preview must be silent")
    elif (width, height) != (source_width, source_height):
        raise ValueError("Download resolution differs from source")
    if not is_faststart(path):
        raise ValueError("MP4 moov atom must precede media data")
    execute(["ffmpeg", "-v", "error", "-xerror", "-i", str(path), "-map", "0:v:0", "-f", "null", "-"], deadline, check)
    return info


def manifest_files(result):
    files = [("download.mp4", result["download"]), ("preview.mp4", result["preview"])]
    for frame in result["frames"]:
        files += [(frame["id"] + ".jpg", {"key": frame["key"], "size": frame["size"], "sha256": frame["sha256"], "contentType": "image/jpeg"}),
                  (frame["id"] + ".webp", {"key": frame["thumbnailKey"], "size": frame["thumbnailSize"], "sha256": frame["thumbnailSha256"], "contentType": "image/webp"})]
    return files


def pillow_thumbnail(source, target):
    # 部分 macOS FFmpeg 发行版没有 libwebp；Pillow 仍通过 libwebp 编码。
    from PIL import Image
    with Image.open(source) as image:
        image = image.convert("RGB")
        image.thumbnail((480, 480), Image.Resampling.LANCZOS)
        image.save(target, "WEBP", quality=85, method=6)


def process_media(source_path, output_dir, job_id, source_sha256=None, deadline=None, check=None):
    deadline = deadline or time.monotonic() + TIMEOUT_SECONDS
    source_path, output_dir = Path(source_path), Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    source_descriptor = describe(source_path, "application/octet-stream", "source")
    if source_sha256 and source_descriptor["sha256"] != source_sha256:
        raise ValueError("Source SHA256 mismatch")
    source = probe(source_path, deadline, check)
    reject_hdr(source)
    source_duration = duration(source)
    manifest = output_dir / "result.json"
    if manifest.is_file():
        try:
            saved = json.loads(manifest.read_text())
            result = saved["result"]
            if saved["pipelineVersion"] == PIPELINE_VERSION and saved["sourceSha256"] == source_descriptor["sha256"] and saved["jobId"] == job_id and all(verify_file(output_dir / name, descriptor) for name, descriptor in manifest_files(result)):
                validate_video(output_dir / "download.mp4", source, False, deadline, check)
                validate_video(output_dir / "preview.mp4", source, True, deadline, check)
                return result
        except (KeyError, ValueError, OSError):
            pass
    base = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y"]
    download = output_dir / "download.mp4"
    execute(base + ["-i", str(source_path)] + download_arguments(source) + [str(download)], deadline, check)
    download_info = validate_video(download, source, False, deadline, check)
    preview = output_dir / "preview.mp4"
    scale = "scale=w='min(iw,1920)':h='min(ih,1920)':force_original_aspect_ratio=decrease:force_divisible_by=2"
    execute(base + ["-i", str(download), "-map", "0:v:0", "-vf", scale, "-c:v", "libx264", "-preset", "medium", "-crf", "24", "-pix_fmt", "yuv420p", "-an", "-movflags", "+faststart", str(preview)], deadline, check)
    validate_video(preview, source, True, deadline, check)
    prefix = f"jobs/{job_id}/"
    frames = []
    frame_hashes = set()
    has_webp = b"libwebp" in execute(["ffmpeg", "-hide_banner", "-encoders"], deadline, check)
    for index, fraction in enumerate((0.1, 0.3, 0.5, 0.7, 0.9), 1):
        identifier, timestamp = f"frame-{index}", round(source_duration * fraction, 6)
        frame, thumbnail = output_dir / f"{identifier}.jpg", output_dir / f"{identifier}.webp"
        # 单帧/低fps片段在接近末尾seek时可能成功返回但不输出图片；顺序解码的0秒兜底必须产生一帧。
        generated = False
        for candidate_time in dict.fromkeys((timestamp, source_duration / 2, 0)):
            try:
                execute(base + ["-ss", str(candidate_time), "-i", str(download), "-frames:v", "1", "-vf", "format=yuvj420p", "-q:v", "2", str(frame)], deadline, check)
            except RuntimeError:
                continue
            if frame.is_file() and frame.stat().st_size > 0:
                timestamp = round(candidate_time, 6)
                generated = True
                break
        if not generated:
            raise ValueError("Cannot extract a decodable candidate frame from video")
        frame_hash = describe(frame, "image/jpeg", prefix + frame.name)["sha256"]
        if frame_hash in frame_hashes:
            continue
        frame_hashes.add(frame_hash)
        if has_webp:
            execute(base + ["-i", str(frame), "-vf", "scale=w='min(iw,480)':h='min(ih,480)':force_original_aspect_ratio=decrease", "-frames:v", "1", "-c:v", "libwebp", "-quality", "85", str(thumbnail)], deadline, check)
        else:
            pillow_thumbnail(frame, thumbnail)
        thumb_info = video_stream(probe(thumbnail, deadline, check))
        if max(int(thumb_info["width"]), int(thumb_info["height"])) > 480:
            raise ValueError("Thumbnail exceeds 480 pixels")
        pixels = execute(base + ["-i", str(frame), "-vf", "scale=96:96,format=gray", "-frames:v", "1", "-f", "rawvideo", "-"], deadline, check)
        full, thumb = describe(frame, "image/jpeg", prefix + frame.name), describe(thumbnail, "image/webp", prefix + thumbnail.name)
        frames.append({"id": identifier, "key": full["key"], "thumbnailKey": thumb["key"], "timestamp": timestamp, "score": frame_score(pixels),
                       "size": full["size"], "sha256": full["sha256"], "contentType": full["contentType"], "thumbnailSize": thumb["size"], "thumbnailSha256": thumb["sha256"]})
    stream = video_stream(download_info)
    width, height = display_dimensions(stream)
    result = {"download": describe(download, "video/mp4", prefix + download.name), "preview": describe(preview, "video/mp4", prefix + preview.name),
              "frames": frames, "defaultFrameId": max(frames, key=lambda frame: frame["score"])["id"],
              "metadata": {"width": width, "height": height, "duration": duration(download_info), "size": download.stat().st_size, "format": "mp4", "codec": "h264", "pixelFormat": stream["pix_fmt"], "colorTransfer": stream.get("color_transfer", "unknown"), "sourceCodec": video_stream(source).get("codec_name"), "pipelineVersion": PIPELINE_VERSION}}
    manifest.write_text(json.dumps({"pipelineVersion": PIPELINE_VERSION, "sourceSha256": source_descriptor["sha256"], "jobId": job_id, "result": result}, ensure_ascii=False, indent=2))
    return result
