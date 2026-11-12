import copy
import json
from pathlib import Path
import tempfile
import unittest
import time
from unittest.mock import patch

from video import catalog, media, publish, runner


def job(identifier="01234567-aaaa", name="新的壁纸.mp4"):
    return {"id": identifier, "revision": 4, "createdAt": "2026-11-12T10:00:00Z",
            "review": {"usage": "desktop", "topic": "动漫", "filename": name,
                       "displayTitle": "标题", "description": "说明", "keywords": ["精灵"],
                       "selectedFrameId": "frame-3"},
            "result": {"download": {"key": f"jobs/{identifier}/download.mp4", "size": 25, "sha256": "a" * 64, "contentType": "video/mp4"},
                       "preview": {"key": f"jobs/{identifier}/preview.mp4", "size": 15, "sha256": "b" * 64, "contentType": "video/mp4"},
                       "frames": [{"id": "frame-3", "key": f"jobs/{identifier}/frame-3.jpg", "thumbnailKey": f"jobs/{identifier}/frame-3.webp", "thumbnailSize": 5, "thumbnailSha256": "c" * 64}],
                       "metadata": {"width": 3840, "height": 2160, "duration": 20, "size": 25, "codec": "h264", "format": "mp4"}}}


class CatalogTests(unittest.TestCase):
    def test_new_4k_video_has_matching_resolution_badge(self):
        _, metadata, item = catalog.published_job(job())
        expected = {"width": 3840, "height": 2160, "label": "4K", "type": "success"}
        self.assertEqual(metadata["resolution"], expected)
        self.assertEqual(item["resolution"], expected)

    def test_new_1080p_video_has_matching_resolution_badge(self):
        incoming = job()
        incoming["result"]["metadata"].update(width=1920, height=1080)
        self.assertEqual(catalog.published_job(incoming)[1]["resolution"], {"width": 1920, "height": 1080, "label": "超清", "type": "primary"})

    def test_new_portrait_video_badge_uses_long_edge(self):
        incoming = job()
        incoming["result"]["metadata"].update(width=2160, height=3840)
        self.assertEqual(catalog.published_job(incoming)[1]["resolution"], {"width": 2160, "height": 3840, "label": "4K", "type": "success"})

    def fixture(self):
        key = "wallpaper/video/mobile/风景/旧图.mp4"
        metadata = {"version": 2, "series": "video", "customRoot": 7, "images": {key: {"category": "mobile", "subcategory": "风景", "filename": "旧图.mp4", "createdAt": "2026-01-01", "extraMetadata": {"keep": True}}}}
        published = [{"id": "video-61", "path": "/wallpaper/video/mobile/风景/%E6%97%A7%E5%9B%BE.mp4", "category": "mobile", "subcategory": "风景", "filename": "旧图.mp4", "createdAt": "2026-01-01", "customPublished": {"retain": 1}, "thumbnailPath": "/old.webp"}]
        stats = {"total": {"desktop": 22, "video": 1}, "series": {"desktop": {"count": 22}, "video": {"count": 1, "future": 9}}, "releases": [{"id": 1}], "custom": 4}
        return metadata, published, stats

    def test_v1_codec_roundtrip_unicode_and_existing_envelope(self):
        value = [{"title": "发光宝可梦", "extra": [1, True]}]
        self.assertEqual(catalog.decode(catalog.encode(value)), value)
        self.assertEqual(catalog.decode({"blob": catalog.encode(value)}), value)

    def test_merge_preserves_history_unknown_fields_and_stable_ids_without_media(self):
        metadata, published, stats = self.fixture()
        before = copy.deepcopy((metadata, published, stats))
        files = catalog.build_catalog(metadata, published, [job()], stats, now="2026-11-12T12:00:00Z")
        combined = catalog.decode(files["data/video.json"])
        self.assertEqual(len(combined), 2)
        old = next(x for x in combined if x["id"] == "video-61")
        self.assertEqual(old["customPublished"], {"retain": 1})
        self.assertEqual(old["thumbnailPath"], "/old.webp")
        self.assertEqual(files["metadata/video.json"]["customRoot"], 7)
        self.assertEqual(files["metadata/video.json"]["images"][next(iter(metadata["images"]))]["extraMetadata"], {"keep": True})
        merged_stats = files["stats.json"]
        self.assertEqual(merged_stats["total"], {"desktop": 22, "video": 2})
        self.assertEqual(merged_stats["series"]["video"]["future"], 9)
        self.assertEqual(merged_stats["releases"], stats["releases"])
        self.assertEqual((metadata, published, stats), before)
        self.assertEqual(catalog.decode(files["data/video/index.json"])[0]["count"], 1)
        self.assertIn("data/video/latest.json", files)
        self.assertIn("data/video/desktop.json", files)
        repeated = catalog.build_catalog(files["metadata/video.json"], combined, [job()], merged_stats)
        self.assertEqual(len(catalog.decode(repeated["data/video.json"])), 2)

    def test_published_only_item_and_metadata_only_item_both_survive(self):
        metadata, published, stats = self.fixture()
        published.append({"id": "video-70", "path": "/wallpaper/video/desktop/历史/仅数据.mp4", "category": "desktop", "filename": "仅数据.mp4", "unknown": "kept"})
        metadata["images"]["wallpaper/video/mobile/历史/仅元数据.mp4"] = {"category": "mobile", "filename": "仅元数据.mp4"}
        result = catalog.decode(catalog.build_catalog(metadata, published, [], stats)["data/video.json"])
        self.assertEqual(len(result), 3)
        self.assertEqual(next(x for x in result if x["id"] == "video-70")["unknown"], "kept")

    def test_metadata_only_r2_entry_keeps_explicit_derived_asset_paths(self):
        metadata = {"images": {"wallpaper/video/mobile/topic/history.mp4": {"id": "video-stable", "category": "mobile", "filename": "history.mp4", "storage": "r2", "thumbnailPath": "/thumbnail/video/mobile/custom/poster.webp", "previewPath": "/preview/video/mobile/custom/full.mp4", "custom": {"keep": True}}}}
        record = catalog.decode(catalog.build_catalog(metadata, [], [], {})["data/video.json"])[0]
        self.assertEqual(record["thumbnailPath"], "/thumbnail/video/mobile/custom/poster.webp")
        self.assertEqual(record["previewPath"], "/preview/video/mobile/custom/full.mp4")
        self.assertEqual(record["custom"], {"keep": True})

    def test_unsafe_frozen_path_and_conflicting_identity_are_rejected(self):
        invalid = job()
        invalid["review"]["topic"] = "../outside"
        with self.assertRaises(ValueError):
            catalog.final_keys(invalid)
        metadata, published, stats = self.fixture()
        published.append({**published[0], "path": "/wallpaper/video/a/other.mp4"})
        with self.assertRaises(ValueError):
            catalog.build_catalog(metadata, published, [], stats)

    def test_latest_order_uses_newest_date_then_ascending_filename(self):
        published = [{"id": "video-z", "path": "/wallpaper/video/desktop/a/z.mp4", "category": "desktop", "filename": "z.mp4", "createdAt": "2026-11-12"},
                     {"id": "video-a", "path": "/wallpaper/video/desktop/a/a.mp4", "category": "desktop", "filename": "a.mp4", "createdAt": "2026-11-12"}]
        files = catalog.build_catalog({"images": {}}, published, [], {})
        self.assertEqual([item["filename"] for item in catalog.decode(files["data/video/latest.json"])], ["a.mp4", "z.mp4"])

    def test_new_job_cannot_replace_a_legacy_video_at_the_same_path(self):
        incoming = job()
        key, _, item = catalog.published_job(incoming)
        historical = {**item, "id": "video-61", "displayTitle": "历史标题"}
        historical.pop("jobId")
        with self.assertRaisesRegex(ValueError, "collid"):
            catalog.build_catalog({"images": {key: {"category": "desktop", "filename": item["filename"]}}}, [historical], [incoming], {})

    def test_retry_of_same_job_cannot_change_frozen_review(self):
        first = catalog.build_catalog({"images": {}}, [], [job()], {})
        changed = job()
        changed["review"]["displayTitle"] = "被改变的冻结标题"
        with self.assertRaisesRegex(ValueError, "Frozen"):
            catalog.build_catalog(first["metadata/video.json"], catalog.decode(first["data/video.json"]), [changed], first["stats.json"])

    def test_metadata_persists_r2_assets_and_complete_publication_membership(self):
        first = job()
        second = job("fedcba98-bbbb", "另一视频.mp4")
        files = catalog.build_catalog({"images": {}}, [], [first, second], {}, publication_id="publication-1")
        for source in [first, second]:
            keys = catalog.final_keys(source)
            metadata = files["metadata/video.json"]["images"][keys["download"]]
            self.assertEqual(metadata["storage"], "r2")
            self.assertEqual(metadata["previewPath"], catalog.public_path(keys["preview"]))
            self.assertEqual(metadata["thumbnailPath"], catalog.public_path(keys["thumbnail"]))
            self.assertEqual(metadata["publicationId"], "publication-1")
            self.assertEqual(metadata["publicationJobCount"], 2)
        published = catalog.decode(files["data/video.json"])
        self.assertTrue(all(item["publicationId"] == "publication-1" and item["publicationJobCount"] == 2 for item in published))
        with self.assertRaisesRegex(ValueError, "Frozen"):
            catalog.build_catalog(files["metadata/video.json"], published, [first, second], files["stats.json"], publication_id="publication-other")


class MediaTests(unittest.TestCase):
    def probe(self, **updates):
        video = {"codec_type": "video", "codec_name": "h264", "pix_fmt": "yuv420p", "width": 3840, "height": 2160, "duration": "20", "color_transfer": "bt709"}
        video.update(updates)
        return {"streams": [video, {"codec_type": "audio", "codec_name": "aac"}], "format": {"duration": "20", "format_name": "mov,mp4,m4a,3gp,3g2,mj2"}}

    def test_compatible_download_copies_and_incompatible_transcodes_crf18(self):
        compatible = media.download_arguments(self.probe())
        self.assertIn("copy", compatible)
        incompatible = media.download_arguments(self.probe(pix_fmt="yuv420p10le", codec_name="hevc"))
        self.assertEqual(incompatible[incompatible.index("-crf") + 1], "18")
        self.assertIn("yuv420p", incompatible)

    def test_hdr_and_dolby_vision_fail_explicitly(self):
        for probe in [self.probe(color_transfer="smpte2084"), self.probe(color_transfer="arib-std-b67"), self.probe(side_data_list=[{"side_data_type": "DOVI configuration record"}])]:
            with self.assertRaisesRegex(ValueError, "HDR"):
                media.download_arguments(probe)

    def test_frame_score_rejects_black_and_prefers_detail(self):
        self.assertGreater(media.frame_score(bytes(range(256)) * 16), media.frame_score(bytes(4096)))

    def test_artifact_manifest_rejects_tampering(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "file.mp4"
            path.write_bytes(b"complete-media")
            descriptor = media.describe(path, "video/mp4", "jobs/a/download.mp4")
            self.assertTrue(media.verify_file(path, descriptor))
            path.write_bytes(b"corrupt-media")
            self.assertFalse(media.verify_file(path, descriptor))

    def test_pillow_webp_fallback_preserves_ratio_and_never_upscales(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as directory:
            source, target = Path(directory) / "frame.jpg", Path(directory) / "frame.webp"
            Image.new("RGB", (960, 540), "red").save(source)
            media.pillow_thumbnail(source, target)
            with Image.open(target) as image:
                self.assertEqual(image.format, "WEBP")
                self.assertEqual(image.size, (480, 270))


class FakeAPI:
    def __init__(self, task=None):
        self.task = task
        self.calls = []
    def post(self, path, body):
        self.calls.append((path, body))
        if path.endswith("/claim"):
            task, self.task = self.task, None
            return {"task": task, "leaseToken": "lease"}
        if path.endswith("/source"):
            return {"url": "unused"}
        if path.endswith("/fail"):
            return {}
        return {}
    def get(self, path):
        self.calls.append((path, None))
        return {"url": "unused"}


class RunnerTests(unittest.TestCase):
    def test_task_deadline_interrupts_blocking_work(self):
        with self.assertRaises(TimeoutError):
            with runner.processing_timeout(0.01):
                time.sleep(0.2)

    def test_partial_remote_checkpoint_does_not_become_a_reusable_manifest(self):
        value = job()["result"]
        api_source = {"checkpoint": {"result": value, "objects": []}}
        with tempfile.TemporaryDirectory() as directory:
            restored = runner.restore_checkpoint(api_source, Path(directory), "01234567-aaaa", "a" * 64, 10**15)
            self.assertFalse(restored)
            self.assertFalse((Path(directory) / "result.json").exists())

    def test_processing_failure_callbacks_then_fails_the_runner(self):
        task = {**job(), "sha256": "d" * 64, "size": 12}
        api = FakeAPI(task)
        with tempfile.TemporaryDirectory() as directory, patch.object(runner, "download_source", side_effect=RuntimeError("source failed")):
            with self.assertRaises(RuntimeError):
                runner.run_process(api, Path(directory), "test-run", max_tasks=1)
        self.assertTrue(any(path.endswith("/fail") for path, _ in api.calls))
        self.assertFalse(any(path.endswith("/complete") for path, _ in api.calls))

    def test_callback_non_2xx_is_not_silently_accepted(self):
        class BadComplete(FakeAPI):
            def post(self, path, body):
                if path.endswith("/complete"):
                    raise RuntimeError("HTTP 503")
                return super().post(path, body)
        api = BadComplete()
        with self.assertRaisesRegex(RuntimeError, "503"):
            runner.complete_process(api, "a", "lease", {})


class PublisherTests(unittest.TestCase):
    def test_publication_objects_include_selected_thumbnail_only_and_digests(self):
        objects = publish.publication_objects({"jobs": [job()]})
        self.assertEqual(len(objects), 3)
        self.assertTrue(all(x["sha256"] for x in objects))
        self.assertEqual(objects[-1]["sourceKey"], "jobs/01234567-aaaa/frame-3.webp")

    def test_optimistic_conflict_rereads_head_and_rebuilds_json(self):
        class Git:
            def __init__(self):
                self.reads = 0
                self.writes = []
            def snapshot(self):
                self.reads += 1
                return str(self.reads), {"metadata/video.json": {"images": {}}, "data/video.json": {"blob": catalog.encode([])}, "stats.json": {"custom": self.reads}}
            def commit(self, head, files, message):
                self.writes.append((head, files))
                if len(self.writes) == 1:
                    raise publish.HeadConflict("changed")
                return "new-sha"
        git = Git()
        sha, files = publish.commit_catalog(git, {"id": "p1", "jobs": [job()]})
        self.assertEqual(sha, "new-sha")
        self.assertEqual(git.reads, 2)
        self.assertEqual(files["stats.json"]["custom"], 2)

    def test_deployment_success_requires_actual_run_success_and_matching_inputs(self):
        run = {"id": 1, "status": "completed", "conclusion": "failure", "display_title": "video:p1:abc"}
        self.assertFalse(publish.deployment_succeeded(run))
        run["conclusion"] = "success"
        self.assertTrue(publish.deployment_succeeded(run))


if __name__ == "__main__":
    unittest.main()
