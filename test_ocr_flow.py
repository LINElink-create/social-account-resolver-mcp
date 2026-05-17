from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from tools import database
from tools.ocr import general_basic_ocr_image_url, run_pending_ocr_tasks
from tools.webpage import collect_and_filter_page_images


def print_json(value: dict[str, Any]) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def test_single_image(image_url: str) -> int:
    result = general_basic_ocr_image_url(image_url)
    print_json(
        {
            "ok": result["ok"],
            "image_url": result["image_url"],
            "text": result["text"],
            "text_length": len(result["text"]),
            "detection_count": len(result["ocr_result"].get("TextDetections", [])),
        }
    )
    return 0


def test_page_flow(
    page_url: str,
    collect_limit: int,
    ocr_limit: int,
    min_width: int,
    min_height: int,
    keep_unknown_size: bool,
) -> int:
    collected = collect_and_filter_page_images(
        page_url,
        limit=collect_limit,
        min_width=min_width,
        min_height=min_height,
        keep_unknown_size=keep_unknown_size,
    )
    print_json(
        {
            "step": "collect_and_filter_page_images",
            "ok": collected["ok"],
            "page_title": collected["page"].get("title"),
            "collected_count": collected["collected_count"],
            "kept_count": collected["kept_count"],
            "rejected_count": collected["rejected_count"],
            "first_images": collected["images"][:3],
        }
    )

    if not collected["images"]:
        print("No images left after filtering; stop before creating OCR tasks.")
        return 1

    saved = database.create_image_tasks(
        page_url=page_url,
        images=collected["images"],
        source_platform="bilibili",
        task_type="ocr",
    )
    print_json(
        {
            "step": "create_image_tasks",
            "ok": saved["ok"],
            "created_count": saved["created_count"],
            "skipped_count": saved["skipped_count"],
            "task_ids": [task["_id"] for task in saved["tasks"][:ocr_limit]],
        }
    )

    ocr_result = run_pending_ocr_tasks(limit=ocr_limit)
    print_json(
        {
            "step": "run_pending_ocr_tasks",
            "ok": ocr_result["ok"],
            "claimed_count": ocr_result["claimed_count"],
            "success_count": ocr_result["success_count"],
            "failed_count": ocr_result["failed_count"],
            "results": [
                {
                    "ok": item.get("ok"),
                    "task_id": (item.get("task") or {}).get("_id") or item.get("task_id"),
                    "status": (item.get("task") or {}).get("status"),
                    "text": (item.get("ocr") or {}).get("text", "")[:500],
                    "error": item.get("error"),
                }
                for item in ocr_result["results"]
            ],
        }
    )
    return 0 if ocr_result["success_count"] else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Test Tencent OCR and image task flow.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    image_parser = subparsers.add_parser("image", help="Run OCR for one image URL.")
    image_parser.add_argument("image_url")

    page_parser = subparsers.add_parser(
        "page", help="Collect page images, create image_tasks, and OCR pending tasks."
    )
    page_parser.add_argument("page_url")
    page_parser.add_argument("--collect-limit", type=int, default=20)
    page_parser.add_argument("--ocr-limit", type=int, default=3)
    page_parser.add_argument("--min-width", type=int, default=180)
    page_parser.add_argument("--min-height", type=int, default=120)
    page_parser.add_argument(
        "--drop-unknown-size",
        action="store_true",
        help="Reject images whose width and height are both unknown.",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    try:
        if args.command == "image":
            return test_single_image(args.image_url)
        if args.command == "page":
            return test_page_flow(
                page_url=args.page_url,
                collect_limit=args.collect_limit,
                ocr_limit=args.ocr_limit,
                min_width=args.min_width,
                min_height=args.min_height,
                keep_unknown_size=not args.drop_unknown_size,
            )
    except Exception as exc:
        print_json({"ok": False, "error": str(exc)})
        return 1

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
