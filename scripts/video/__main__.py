"""python -m video：本地验收、JSON 离线预览或 Actions 队列消费者。"""
import argparse
import json
import os
import re
import sys
from pathlib import Path

from . import catalog, media, publish, runner
from .api import ServiceAPI


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    local = commands.add_parser("local")
    local.add_argument("source")
    local.add_argument("output")
    local.add_argument("--job-id", default="local-validation")
    build = commands.add_parser("catalog")
    build.add_argument("repository")
    build.add_argument("output")
    build.add_argument("--publication")
    for name in ("process", "publish"):
        command = commands.add_parser(name)
        command.add_argument("--max-tasks", type=int, default=8 if name == "process" else 1)
    commands.choices["process"].add_argument("--cache-dir", default=".video-cache")
    arguments = parser.parse_args()
    if arguments.command == "local":
        result = media.process_media(arguments.source, arguments.output, arguments.job_id)
        print(json.dumps({"metadata": result["metadata"], "defaultFrameId": result["defaultFrameId"]}, ensure_ascii=False))
        return
    if arguments.command == "catalog":
        root, output = Path(arguments.repository).resolve(), Path(arguments.output).resolve()
        if output == root or root in output.parents:
            raise ValueError("Offline catalog output must be outside the source repository")
        existing = {name: json.loads((root / name).read_text()) for name in ("metadata/video.json", "data/video.json", "stats.json")}
        for path in (root / "data/video").glob("*.json"):
            existing[path.relative_to(root).as_posix()] = json.loads(path.read_text())
        publication = json.loads(Path(arguments.publication).read_text()) if arguments.publication else {"jobs": []}
        files = catalog.build_catalog(existing["metadata/video.json"], catalog.decode(existing["data/video.json"]), publication["jobs"], existing["stats.json"], existing_files=existing)
        for name, value in files.items():
            target = output / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(publish.json_bytes(value))
        print(json.dumps({"total": files["data/video.json"]["total"], "jsonFiles": len(files), "output": str(output)}, ensure_ascii=False))
        return
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    if not run_id.isdigit():
        raise ValueError("Queue consumers require a numeric GITHUB_RUN_ID")
    if arguments.max_tasks < 1 or arguments.max_tasks > 20:
        raise ValueError("max-tasks must be between 1 and 20")
    api = ServiceAPI(os.environ.get("VIDEO_API_URL"), os.environ.get("VIDEO_SERVICE_TOKEN"))
    if arguments.command == "process":
        runner.run_process(api, Path(arguments.cache_dir), run_id, arguments.max_tasks)
    else:
        workflow = os.environ.get("VIDEO_GALLERY_WORKFLOW", "")
        if not workflow:
            raise ValueError("VIDEO_GALLERY_WORKFLOW is required; gallery deployment integration must be configured")
        token = os.environ.get("REPO_TOKEN", "")
        github = publish.GitHub(os.environ.get("VIDEO_DATA_REPOSITORY", "IT-NuanxinPro/nuanXinProPic"), token, os.environ.get("VIDEO_DATA_BRANCH", "main"))
        gallery = publish.GitHub(os.environ.get("VIDEO_GALLERY_REPOSITORY", "IT-NuanxinPro/wallpaper-gallery"), token)
        publish.run_publish(api, github, gallery, workflow, os.environ.get("VIDEO_GALLERY_REF", "main"), run_id, arguments.max_tasks)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        message = re.sub(r"https?://\S+", "[remote URL]", str(error))
        print(f"Video pipeline failed: {type(error).__name__}: {message}", file=sys.stderr)
        raise SystemExit(1)
