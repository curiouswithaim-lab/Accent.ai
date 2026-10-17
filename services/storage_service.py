"""Google Cloud Storage staging for uploaded filings and generated audit artifacts.

Bucket: $ACCENT_BUCKET (default: "{project}-accent-filings").
Offline fallback writes to ./.accent_artifacts/ and returns a file:// URI.
"""
import datetime as dt
import hashlib
import os
import re
from pathlib import Path
from typing import Dict

from services import force_offline, gcp_project, has_gcp_credentials, logger

LOCAL_DIR = Path(os.getenv("ACCENT_LOCAL_ARTIFACTS", os.getenv("TERRATRIAL_LOCAL_ARTIFACTS", Path(__file__).resolve().parent.parent / ".accent_artifacts")))


def bucket_name() -> str:
    return os.getenv("ACCENT_BUCKET") or os.getenv("TERRATRIAL_BUCKET") or f"{gcp_project() or 'your-gcp-project-id'}-accent-filings"


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name)[:120] or "file"


def _upload(blob_path: str, data: bytes, content_type: str) -> Dict[str, str]:
    if not force_offline() and has_gcp_credentials():
        try:
            from google.cloud import storage

            client = storage.Client(project=gcp_project())
            blob = client.bucket(bucket_name()).blob(blob_path)
            blob.upload_from_string(data, content_type=content_type, timeout=30)
            uri = f"gs://{bucket_name()}/{blob_path}"
            logger.info("Staged %s (%d bytes)", uri, len(data))
            return {"mode": "LIVE", "uri": uri}
        except Exception as exc:
            logger.warning("GCS upload failed, staging locally: %s", exc)
            reason = f"{exc.__class__.__name__}: {str(exc)[:140]}"
    else:
        reason = "No GCP credentials mounted (local/offline mode)"
    try:
        path = LOCAL_DIR / blob_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return {"mode": "LOCAL", "uri": path.as_uri(), "reason": reason}
    except Exception as exc:  # read-only filesystem etc.
        return {"mode": "MEMORY", "uri": f"memory://{blob_path}", "reason": f"{reason}; local write failed: {exc}"}


def stage_pdf(pdf_bytes: bytes, filename: str) -> Dict[str, str]:
    digest = hashlib.sha256(pdf_bytes).hexdigest()[:16]
    day = dt.date.today().isoformat()
    result = _upload(f"filings/{day}/{digest}_{_safe(filename)}", pdf_bytes, "application/pdf")
    result["sha256_16"] = digest
    return result


def store_audit_artifact(report_json: str, audit_id: str) -> Dict[str, str]:
    day = dt.date.today().isoformat()
    return _upload(f"audits/{day}/{_safe(audit_id)}.json", report_json.encode("utf-8"), "application/json")
