# Accent.AI — Specification
**Full Title:** Audit of Carbon Credits and ENvironmental impacT on land using AI  
**Target GCP Project ID:** `<your-gcp-project-id>`

## 1. Project Overview
**Accent.AI** is an autonomous satellite and statistical verification engine that audits environmental compliance filings—specifically **Environmental Impact Assessments (EIAs)** and **Voluntary Carbon Credit Project Design Documents (PDDs)**—using independent earth observation.

It detects core forms of baseline manipulation:
1. **Temporal Confounding (Seasonal Spin):** Sampling water/effluent during 95th-percentile monsoon floods to dilute pollutants (EIA), or measuring forest canopy right after seasonal rains against a dry-season baseline (Carbon).
2. **Spatial Control-Group Bias:** Placing air/water baseline sensors upwind or upstream of industrial discharge plumes (EIA), or selecting steep, high-risk "control plots" that do not match the project area's topography (Carbon).
3. **Perimeter & Buffer Leakage:** Claiming dense ecological land is "barren scrubland" (EIA), or shifting logging just outside the protected project boundary into the buffer ring (Carbon).

When manipulation is detected, Accent.AI acts as an active **Ground-Level Dispatcher** via **Google Cloud Pub/Sub**, firing geo-fenced counter-sampling coordinates to field inspectors or issuing registry credit holds and ranger alerts.

---

## 2. Technical Stack
All code integrates the Google Cloud stack:
1. **Gemini on Vertex AI (`google-genai` SDK):** Model `gemini-2.5-pro` (with fallback to `gemini-2.5-flash`) and native Pydantic structured schemas for autonomous PDF classification (EIA vs Carbon), multimodal extraction, and statistical reasoning.
2. **Google Earth Engine (`earthengine-api`) & BigQuery (`google-cloud-bigquery`):** Query historical precipitation percentiles (CHIRPS), prevailing wind vectors (ERA5), elevation/slope (SRTM Digital Elevation Model), vegetation canopy trends (Sentinel-2 / Landsat 5/7 NDVI), and surface water flow.
3. **Google Cloud Storage (`google-cloud-storage`):** Stage uploaded regulatory PDFs and generated audit artifacts (`$ACCENT_BUCKET`).
4. **Google Cloud Pub/Sub (`google-cloud-pubsub`):** Publish real-time JSON dispatch payloads to topic `accent-ground-dispatch` when an audit fails compliance thresholds.
5. **Google Cloud Run Deployment:** Containerized via `Dockerfile` exposing `PORT=8080`.

---

## 3. Repository Structure
```text
accent-ai/
├── app.py                          # Main Streamlit audit workspace and interactive dashboard
├── schemas.py                      # Pydantic models for Gemini Structured Outputs & Audit Verdicts
├── services/
│   ├── __init__.py                 # Shared ADC / project detection
│   ├── gemini_extractor.py         # google-genai Vertex AI PDF extraction & audit logic
│   ├── earth_engine_verifier.py    # Earth Engine & BigQuery spatio-temporal verification
│   ├── pubsub_dispatcher.py        # Cloud Pub/Sub ground-action event publisher
│   └── storage_service.py          # Cloud Storage PDF staging helper
├── requirements.txt                # Pinned Python dependencies
├── Dockerfile                      # Cloud Run production container config (Port 8080)
└── README.md                       # Architecture & Cloud Run deployment guide
```