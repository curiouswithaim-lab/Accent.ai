"""Google Cloud Pub/Sub ground-action dispatcher for Accent.AI.

Publishes the GroundDispatchAction JSON to projects/{project}/topics/accent-ground-dispatch.
Downstream subscribers (field-inspector mobile app, registry webhook, ranger SMS bridge)
consume the event. Offline, a deterministic simulated acknowledgement ID is returned.
"""
import datetime as dt
import hashlib
import json
import os
from typing import Any, Dict, Optional

from schemas import FullAuditReport, GroundDispatchAction
from services import PUBSUB_TOPIC_ID, force_offline, gcp_project, has_gcp_credentials, logger


def topic_path(project_id: Optional[str] = None) -> str:
    return f"projects/{project_id or gcp_project() or 'accent-511817'}/topics/{PUBSUB_TOPIC_ID}"


def build_payload(action: GroundDispatchAction, report: Optional[FullAuditReport] = None,
                  audit_id: Optional[str] = None) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "schema": "accent_ai.ground_dispatch.v1",
        "audit_id": audit_id,
        "issued_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "dispatch": json.loads(action.model_dump_json()),
    }
    if report is not None:
        payload.update({
            "project_title": report.extracted_data.project_title,
            "filing_mode": report.extracted_data.filing_mode,
            "integrity_score": report.integrity_score,
            "verdict": report.verdict_banner,
            "failed_checks": [c.check_name for c in report.forensic_checks if c.status == "FAILED"],
            "geofence": {
                "type": "Point",
                "coordinates": [action.recommended_coordinates_lon, action.recommended_coordinates_lat],
                "radius_m": 250,
            },
        })
    return payload


def publish_dispatch(action: GroundDispatchAction, report: Optional[FullAuditReport] = None,
                     audit_id: Optional[str] = None) -> Dict[str, Any]:
    """Publish to Pub/Sub. Returns a receipt dict. Never raises."""
    payload = build_payload(action, report, audit_id)
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    project = gcp_project()
    topic = topic_path(project)
    attributes = {
        "action_type": action.action_type,
        "priority": action.priority,
        "filing_mode": payload.get("filing_mode", "UNKNOWN"),
        "audit_id": audit_id or "",
    }
    if not force_offline() and has_gcp_credentials() and project:
        try:
            from google.cloud import pubsub_v1

            publisher = pubsub_v1.PublisherClient()
            future = publisher.publish(topic, data=data, **attributes)
            message_id = future.result(timeout=float(os.getenv("PUBSUB_TIMEOUT_S", "15")))
            logger.info("Published dispatch %s to %s", message_id, topic)
            return {"mode": "LIVE", "message_id": message_id, "topic": topic, "bytes": len(data),
                    "attributes": attributes, "payload": payload}
        except Exception as exc:
            logger.warning("Pub/Sub publish failed, simulating ack: %s", exc)
            reason = f"{exc.__class__.__name__}: {str(exc)[:160]}"
    else:
        reason = "No GCP credentials mounted (local/offline mode)"
    sim_id = "sim-" + str(int(hashlib.sha256(data).hexdigest()[:13], 16))
    return {"mode": "SIMULATED", "message_id": sim_id, "topic": topic, "bytes": len(data),
            "attributes": attributes, "payload": payload, "reason": reason}
