"""Accent.AI Pydantic schemas.

Accent.AI: Audit of Carbon Credits and ENvironmental impacT on land using AI.
Two families of models live here:
  1. Gemini Structured-Output contracts (ExtractedFilingData, FullAuditReport, ...)
     passed directly as `response_schema` to the google-genai SDK.
  2. Satellite / weather evidence bundles produced by the Earth Engine verifier
     and consumed by the audit engine + Streamlit dashboard.
"""
from typing import List, Literal, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# 1. Gemini structured-output contracts
# ---------------------------------------------------------------------------
class GeoPoint(BaseModel):
    label: str = Field(description="e.g., 'Project Site Center', 'Control Plot A', 'Baseline Air Sensor 1', 'Downwind Village'")
    role: Literal["PROJECT_SITE", "CONTROL_SITE", "SENSOR_STATION", "BUFFER_ZONE"]
    latitude: float
    longitude: float
    claimed_elevation_m: Optional[float] = Field(default=0.0, description="Claimed elevation in meters if stated, else 0.0 or None")

class SamplingClaim(BaseModel):
    parameter: str = Field(description="e.g., 'River Heavy Metal (Cr-VI)', 'Groundwater pH', 'Ambient PM2.5', 'Above-Ground Biomass / NDVI'")
    claimed_project_value: float
    claimed_control_or_limit_value: float
    statutory_lower_limit: Optional[float] = Field(
        default=None,
        description="Lower bound when the permissible limit is a range, e.g. 6.5 for pH 6.5–8.5 (claimed_control_or_limit_value then holds the upper bound 8.5). Null when the limit is a single maximum.",
    )
    unit: str = Field(default="mg/L", description="e.g., 'mg/L', 'ug/m3', 'pH', 'tCO2e/ha', 'NDVI'")
    sampling_date: str = Field(description="YYYY-MM-DD format")
    reference_period: Optional[str] = Field(
        default=None,
        description="For historical rates (e.g. a baseline deforestation rate), the years it was measured over as 'YYYY-YYYY', e.g. '2009-2011'. Null if not stated.",
    )
    page_citation: str = Field(description="Exact page or table reference in the PDF, e.g., 'Page 42, Table 3.4'")
    is_concentration_pollutant: bool = Field(default=True, description="True for chemical concentrations (mg/L, ppm, ug/m3) where dilution or mass flow applies; False for pH, temperature, turbidity, water level, or non-concentration metrics")
    governing_body: str = Field(default="Statutory Authority", description="e.g., 'US EPA', 'CPCB', 'State Pollution Control Board', 'Defra', 'UNFCCC'")
    governing_statute: str = Field(default="Applicable Environmental Standard", description="e.g., 'Clean Water Act', 'Environment Protection Act', 'VCS Standard'")

class ExtractedFilingData(BaseModel):
    filing_mode: Literal["EIA_CLEARANCE", "CARBON_OFFSET"]
    subsector: Literal["FORESTRY_REDD", "FORESTRY_ARR", "INDUSTRIAL_EIA", "RENEWABLE_ENERGY_GRID", "OTHER_OUT_OF_SCOPE"] = Field(
        default="FORESTRY_REDD",
        description="Subsector classification: FORESTRY_REDD (Avoided Deforestation), FORESTRY_ARR (Afforestation / Canopy Gain), INDUSTRIAL_EIA, RENEWABLE_ENERGY_GRID, or OTHER_OUT_OF_SCOPE",
    )
    forestry_metric_type: Optional[Literal["AVOIDED_DEFORESTATION_RATE", "CANOPY_GAIN", "NOT_APPLICABLE"]] = Field(
        default=None,
        description="For forestry filings: 'AVOIDED_DEFORESTATION_RATE' for REDD+ avoided deforestation / baseline loss rate; 'CANOPY_GAIN' for ARR/afforestation canopy gain; 'NOT_APPLICABLE' for EIA.",
    )
    is_satellite_verifiable: bool = Field(
        default=True,
        description="True for forestry and surface industrial sites; False for wind, solar, SCADA-reliant projects",
    )
    unsupported_reason: Optional[str] = Field(
        default=None,
        description="e.g., 'Grid-connected displacement requires electrical SCADA telemetry and grid-dispatch metering receipts, which cannot be verified via optical earth observation satellites.'",
    )
    project_title: str
    applicant_or_proponent: str
    region_name: str
    governing_body: str = Field(default="Statutory Authority", description="e.g., 'US EPA', 'CPCB', 'State Pollution Control Board', 'UNFCCC', 'Verra'")
    governing_statute: str = Field(default="Applicable Environmental Standard", description="e.g., 'Clean Water Act', 'Environment Protection Act', 'VCS Standard'")
    summary_of_claim: str
    coordinates: List[GeoPoint]
    sampling_claims: List[SamplingClaim]
    extracted_methodology_notes: List[str] = Field(
        description="Quotes or notes from the PDF regarding control site selection, weather during sampling, or land classification"
    )

class ForensicCheckResult(BaseModel):
    check_name: str = Field(description="1. Temporal Confounding Check, 2. Spatial Control-Group Check, or 3. Perimeter & Buffer Truth Check")
    status: Literal["FAILED", "WARNING", "PASSED"]
    pdf_claim_summary: str
    satellite_truth_summary: str
    statistical_discrepancy: str = Field(description="Quantified delta, e.g., 'Sampled at 97th percentile monsoon rainfall (+340% dilution)'")

class GroundDispatchAction(BaseModel):
    pubsub_topic: str
    action_type: str = Field(description="COUNTER_SAMPLING_DISPATCH, REGISTRY_ESCROW_HOLD_AND_RANGER_ALERT, COMPLIANCE_CLEARED, or OUT_OF_SCOPE_NOTICE")
    priority: str = Field(default="ROUTINE", description="CRITICAL, HIGH, ROUTINE, or INFORMATIONAL")
    target_recipient: str
    recommended_coordinates_lat: float
    recommended_coordinates_lon: float
    recommended_time_window: str
    instruction_directive: str

class FullAuditReport(BaseModel):
    extracted_data: ExtractedFilingData
    integrity_score: Optional[int] = Field(default=None, description="0 to 100 baseline integrity score, or None if out of scope")
    verdict_banner: str = Field(description="e.g., '✅ COMPLIANCE VERIFIED: STATUTORY LIMITS RESPECTED', 'ℹ️ METHODOLOGY OUT OF OBSERVATIONAL SATELLITE SCOPE', 'HIGH SPIN / BASELINE RIGGING DETECTED', or 'VERIFIED BASELINE INTEGRITY'")
    forensic_checks: List[ForensicCheckResult]
    dispatch_action: GroundDispatchAction
    provisional_layers: List[str] = Field(
        default_factory=list,
        description="Evidence layers that used synthetic fallback telemetry instead of live satellite data",
    )


class AuditReview(BaseModel):
    """Gemini audit-review output: rewritten prose for the three deterministic checks."""
    forensic_checks: List[ForensicCheckResult]
    instruction_directive: str


# ---------------------------------------------------------------------------
# 2. Satellite & weather evidence (Earth Engine / BigQuery verifier output)
# ---------------------------------------------------------------------------
class LayerProvenance(BaseModel):
    layer: str
    dataset: str
    mode: Literal["LIVE", "BENCHMARK_REPLAY", "DETERMINISTIC_FALLBACK"]
    detail: str = ""


class RainfallEvidence(BaseModel):
    dates: List[str]
    precip_mm: List[float]
    sampling_date: str
    sampling_day_mm: float
    sampling_percentile: float
    p50_wet_day_mm: float
    p95_mm: float
    antecedent_7d_mm: float
    estimated_dilution_factor: float
    dry_window_suggestion: str
    bigquery_corroboration: Optional[str] = None
    monthly_dates: List[str] = []
    monthly_mm: List[float] = []


class NdviEvidence(BaseModel):
    dates: List[str]
    ndvi: List[float]
    baseline_date: str
    baseline_ndvi: float
    measurement_date: str
    measurement_ndvi: float
    like_season_baseline_ndvi: float
    claimed_gain_pct: float
    true_gain_pct: float
    seasonal_artifact_pct: float
    project_site_current_ndvi: float


class WindEvidence(BaseModel):
    direction_bins_deg: List[float]
    frequency_pct: List[float]
    prevailing_from_deg: float
    mean_speed_ms: float
    sampling_date: str
    source_label: str
    sensor_label: str
    sensor_bearing_from_source_deg: float
    sensor_distance_km: float
    angular_offset_deg: float
    sensor_is_upwind: bool
    downwind_receptor_lat: float
    downwind_receptor_lon: float


class TerrainPoint(BaseModel):
    label: str
    role: str
    latitude: float
    longitude: float
    srtm_elevation_m: float
    slope_deg: float
    claimed_elevation_m: Optional[float] = None
    distance_to_road_km: Optional[float] = None


class BufferRing(BaseModel):
    label: str
    inner_km: float
    outer_km: float
    pre_period_loss_ha: float
    post_period_loss_ha: float
    change_pct: float
    mean_ndvi: float


class BufferEvidence(BaseModel):
    project_radius_km: float
    pre_period: str
    post_period: str
    rings: List[BufferRing]
    inside_change_pct: float
    peak_ring_label: str
    peak_change_pct: float
    hotspot_lat: float
    hotspot_lon: float
    hotspot_distance_outside_km: float
    baseline_loss_rate_pct: float = Field(
        default=0.0, description="Observed pre-project forest loss across project + buffer rings, %/yr")


class SiteLossSample(BaseModel):
    label: str
    role: str
    latitude: float
    longitude: float
    radius_km: float
    forest_ha: float
    loss_ha: float
    loss_rate_pct: float


class BaselineSampling(BaseModel):
    """Hansen forest-loss rate over the filing's own reference period, at every project and reference site."""
    period: str
    canopy_threshold_pct: int
    mean_tree_cover_pct: float
    sites: List[SiteLossSample]
    project_rate_pct: float
    reference_rate_pct: Optional[float] = None


class SatelliteEvidence(BaseModel):
    filing_mode: Literal["EIA_CLEARANCE", "CARBON_OFFSET"]
    provenance: List[LayerProvenance]
    rainfall: RainfallEvidence
    ndvi: NdviEvidence
    wind: Optional[WindEvidence] = None
    terrain: List[TerrainPoint]
    buffer: BufferEvidence
    baseline_sampling: Optional[BaselineSampling] = None
    recommended_dispatch_lat: float
    recommended_dispatch_lon: float
    recommended_dispatch_rationale: str