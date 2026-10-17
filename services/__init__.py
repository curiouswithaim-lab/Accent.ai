"""Shared GCP environment helpers for Accent.AI service modules.

Accent.AI: Audit of Carbon Credits and ENvironmental impacT on land using AI.
Every service follows the same contract: attempt the live Google Cloud SDK call,
and on *any* failure (missing credentials, missing API enablement, network
errors, quota) fall back to deterministic telemetry so a live demo never crashes.
"""
import functools
import logging
import os
from typing import Optional, Tuple

logger = logging.getLogger("accent_ai")
if not logger.handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

DEFAULT_PROJECT_ID = "accent-511817"
PUBSUB_TOPIC_ID = os.getenv("ACCENT_PUBSUB_TOPIC", os.getenv("TERRATRIAL_PUBSUB_TOPIC", "accent-ground-dispatch"))


def force_offline() -> bool:
    """Set ACCENT_FORCE_OFFLINE=1 (or TERRATRIAL_FORCE_OFFLINE=1) to skip every live GCP call."""
    val = os.getenv("ACCENT_FORCE_OFFLINE", os.getenv("TERRATRIAL_FORCE_OFFLINE", "0")).lower()
    return val in ("1", "true", "yes")


@functools.lru_cache(maxsize=1)
def adc_status() -> Tuple[bool, Optional[str], str]:
    """Return (has_credentials, project_id, detail) using Application Default Credentials."""
    target_project = os.getenv("GOOGLE_CLOUD_PROJECT") or DEFAULT_PROJECT_ID
    if force_offline():
        return False, target_project, "ACCENT_FORCE_OFFLINE=1"
    try:
        import google.auth

        creds, adc_project = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
        project = os.getenv("GOOGLE_CLOUD_PROJECT") or adc_project or DEFAULT_PROJECT_ID
        return True, project, f"ADC ({type(creds).__name__})"
    except Exception as exc:  # DefaultCredentialsError, TransportError, ...
        return False, target_project, f"No ADC: {exc.__class__.__name__}"


def gcp_project() -> Optional[str]:
    return adc_status()[1]


def has_gcp_credentials() -> bool:
    return adc_status()[0]

