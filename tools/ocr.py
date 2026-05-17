from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from tencentcloud.common import credential
from tencentcloud.common.exception.tencent_cloud_sdk_exception import (
    TencentCloudSDKException,
)
from tencentcloud.common.profile.client_profile import ClientProfile
from tencentcloud.common.profile.http_profile import HttpProfile
from tencentcloud.ocr.v20181119 import models, ocr_client

from . import database

PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")

DEFAULT_BATCH_LIMIT = int(os.getenv("OCR_BATCH_LIMIT", "5"))
OCR_ENDPOINT = "ocr.tencentcloudapi.com"


class OCRConfigurationError(RuntimeError):
    """Raised when OCR credentials are missing."""


def _client() -> ocr_client.OcrClient:
    secret_id = os.getenv("TENCENTCLOUD_SECRET_ID", "").strip()
    secret_key = os.getenv("TENCENTCLOUD_SECRET_KEY", "").strip()
    region = os.getenv("TENCENTCLOUD_REGION", "").strip()
    if not secret_id or not secret_key:
        raise OCRConfigurationError(
            "TENCENTCLOUD_SECRET_ID and TENCENTCLOUD_SECRET_KEY are required"
        )

    cred = credential.Credential(secret_id, secret_key)
    http_profile = HttpProfile()
    http_profile.endpoint = OCR_ENDPOINT
    client_profile = ClientProfile()
    client_profile.httpProfile = http_profile
    return ocr_client.OcrClient(cred, region, client_profile)


def _extract_text(ocr_payload: dict[str, Any]) -> str:
    detections = ocr_payload.get("TextDetections") or []
    lines: list[str] = []
    for item in detections:
        text = item.get("DetectedText")
        if text:
            lines.append(str(text).strip())
    return "\n".join(line for line in lines if line)


def general_basic_ocr_image_url(image_url: str) -> dict[str, Any]:
    client = _client()
    request = models.GeneralBasicOCRRequest()
    request.from_json_string(json.dumps({"ImageUrl": image_url}))
    response = client.GeneralBasicOCR(request)
    payload = json.loads(response.to_json_string())
    return {
        "ok": True,
        "image_url": image_url,
        "provider": "tencentcloud",
        "api": "GeneralBasicOCR",
        "ocr_result": payload,
        "text": _extract_text(payload),
    }


def run_ocr_for_image_task(task_id: str) -> dict[str, Any]:
    task = database.get_image_task(task_id)
    if not task:
        return {"ok": False, "task_id": task_id, "error": "image task not found"}

    image_url = task.get("image_url")
    if not image_url:
        saved = database.mark_image_task_failed(task_id, "image_url is missing")
        return {
            "ok": False,
            "task_id": task_id,
            "error": "image_url is missing",
            "task": saved,
        }

    try:
        result = general_basic_ocr_image_url(str(image_url))
        saved = database.mark_image_task_ocr_success(
            task_id, result["ocr_result"], result["text"]
        )
        return {"ok": True, "task": saved, "ocr": result}
    except (TencentCloudSDKException, OCRConfigurationError, Exception) as exc:
        saved = database.mark_image_task_failed(task_id, str(exc))
        return {"ok": False, "task_id": task_id, "error": str(exc), "task": saved}


def run_pending_ocr_tasks(limit: int = DEFAULT_BATCH_LIMIT) -> dict[str, Any]:
    tasks = database.claim_pending_image_tasks(limit)
    results: list[dict[str, Any]] = []
    for task in tasks:
        results.append(run_ocr_for_image_task(task["_id"]))
    return {
        "ok": True,
        "claimed_count": len(tasks),
        "success_count": sum(1 for item in results if item.get("ok")),
        "failed_count": sum(1 for item in results if not item.get("ok")),
        "results": results,
    }
