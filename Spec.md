# Accent.AI — Master Specification for Antigravity IDE
**Full Title:** Audit of Carbon Credits and ENvironmental impacT on land using AI  
**Hackathon:** AI Builder Cup 2026 | Google Cloud JAPAC Hackathon (Theme 6: Sustainability & Social Impact)  
**Target GCP Project ID:** `accent-511817`

## 1. Project Overview
**Accent.AI** is an autonomous satellite and statistical verification engine that audits environmental compliance filings—specifically **Environmental Impact Assessments (EIAs)** and **Voluntary Carbon Credit Project Design Documents (PDDs)**—using clinical-trial methodology. 

It detects three core forms of scientific manipulation:
1. **Temporal Confounding (Seasonal Spin):** Sampling water/effluent during 95th-percentile monsoon floods to dilute pollutants (EIA), or measuring forest canopy right after seasonal rains against a dry-season baseline (Carbon).
2. **Spatial Control-Group Bias:** Placing air/water baseline sensors upwind or upstream of industrial discharge plumes (EIA), or selecting steep, high-risk "control plots" that do not match the project area's topography (Carbon).
3. **Perimeter & Buffer Leakage:** Claiming dense ecological land is "barren scrubland" (EIA), or shifting logging just outside the protected project boundary into the 5 km buffer ring (Carbon).

When manipulation is detected, Accent.AI acts as an active **Ground-Level Dispatcher** via **Google Cloud Pub/Sub**, firing geo-fenced counter-sampling coordinates to field inspectors or issuing registry credit holds and ranger alerts.

---

## 2. Mandatory Hackathon Tech Stack (Google Cloud JAPAC Rules)
All code strictly integrates the official Google Cloud tech stack:
1. **Gemini on Vertex AI (`google-genai` SDK):** Use `from google import genai` and `from google.genai import types` (do NOT use deprecated libraries). Model `gemini-2.5-pro` (with fallback to `gemini-2.5-flash`) and native Pydantic `response_schema` for autonomous PDF classification (EIA vs Carbon), multimodal extraction, and statistical reasoning.
2. **Google Earth Engine (`earthengine-api`) & BigQuery (`google-cloud-bigquery`):** Query historical precipitation percentiles (CHIRPS), prevailing wind vectors (ERA5), elevation/slope (SRTM Digital Elevation Model), and vegetation canopy trends (Sentinel-2 NDVI).
3. **Google Cloud Storage (`google-cloud-storage`):** Stage uploaded regulatory PDFs and generated audit artifacts (`$ACCENT_BUCKET`).
4. **Google Cloud Pub/Sub (`google-cloud-pubsub`):** Publish real-time JSON dispatch payloads to topic `accent-ground-dispatch` when an audit fails compliance thresholds.
5. **Google Cloud Run Deployment:** Containerized via `Dockerfile` exposing `PORT=8080` (`streamlit run app.py --server.port=8080 --server.address=0.0.0.0`).

*Resilience Requirement:* Every GCP service module (`gemini_extractor.py`, `earth_engine_verifier.py`, `pubsub_dispatcher.py`, `storage_service.py`) MUST implement live GCP SDK calls wrapped in clean try/except blocks that gracefully fall back to deterministic telemetry if local credentials or Earth Engine project authentication are not yet mounted. The app must NEVER crash during a judge's live evaluation.

---

## 3. Target Repository Structure
```text
accent-ai/
├── app.py                          # Main Streamlit 3-column forensic dashboard
├── schemas.py                      # Pydantic models for Gemini Structured Outputs & Audit Verdicts
├── services/
│   ├── __init__.py                 # Shared ADC / project detection (accent-511817)
│   ├── gemini_extractor.py         # google-genai Vertex AI PDF extraction & clinical audit logic
│   ├── earth_engine_verifier.py    # Earth Engine & BigQuery spatio-temporal verification
│   ├── pubsub_dispatcher.py        # Cloud Pub/Sub ground-action event publisher
│   └── storage_service.py          # Cloud Storage PDF staging helper
├── demo_data/
│   ├── __init__.py
│   └── golden_cases.py             # Pre-loaded realistic EIA & Carbon Offset benchmark cases
├── requirements.txt                # Pinned Python dependencies
├── Dockerfile                      # Cloud Run production container config (Port 8080)
└── README.md                       # Architecture & Cloud Run deployment guide
```