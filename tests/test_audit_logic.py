"""Offline regression tests for the deterministic audit engine. Run: python -m unittest discover tests"""
import os
import sys
import unittest

os.environ["ACCENT_FORCE_OFFLINE"] = "1"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from schemas import ExtractedFilingData, GeoPoint, SamplingClaim  # noqa: E402
from services.earth_engine_verifier import verify_filing  # noqa: E402
from services.gemini_extractor import (  # noqa: E402
    _heuristic_extract,
    build_deterministic_report,
    claim_limit_breach,
    is_surface_effluent_concentration,
)

COMPLIANT = "✅ COMPLIANCE VERIFIED: STATUTORY LIMITS RESPECTED"
DRY = {"rain": {"target_percentile": 10.0}}


def claim(param, value, limit, unit="mg/L", lower=None, conc=True):
    return SamplingClaim(parameter=param, claimed_project_value=value, claimed_control_or_limit_value=limit,
                         statutory_lower_limit=lower, unit=unit, sampling_date="2024-01-15",
                         page_citation="Page 1", is_concentration_pollutant=conc)


def eia(claims, with_air_sensor=False):
    pts = [GeoPoint(label="Effluent outfall", role="PROJECT_SITE", latitude=31.04, longitude=76.70,
                    claimed_elevation_m=300.0)]
    if with_air_sensor:
        pts.append(GeoPoint(label="Ambient air monitor AQ-1", role="SENSOR_STATION", latitude=31.06,
                            longitude=76.68, claimed_elevation_m=310.0))
    return ExtractedFilingData(
        filing_mode="EIA_CLEARANCE", subsector="INDUSTRIAL_EIA", forestry_metric_type="NOT_APPLICABLE",
        project_title="Test Estate", applicant_or_proponent="Test", region_name="Test",
        summary_of_claim="Test", coordinates=pts, sampling_claims=claims, extracted_methodology_notes=[])


def audit(x, profile=DRY):
    ev = verify_filing(x, profile, prefer_live=False)
    return build_deterministic_report(x, ev), ev


class ComplianceLogic(unittest.TestCase):
    def test_within_limit_dry_weather_is_cleared(self):
        r, _ = audit(eia([claim("River effluent BOD", 18.0, 30.0)]))
        self.assertEqual(r.verdict_banner, COMPLIANT)
        self.assertEqual(r.dispatch_action.action_type, "COMPLIANCE_CLEARED")

    def test_self_reported_exceedance_is_not_cleared(self):
        r, _ = audit(eia([claim("River effluent BOD", 45.0, 30.0)]))
        self.assertEqual(r.forensic_checks[0].status, "FAILED")
        self.assertNotEqual(r.verdict_banner, COMPLIANT)
        self.assertNotEqual(r.dispatch_action.action_type, "COMPLIANCE_CLEARED")

    def test_exceedance_in_secondary_claim_is_caught(self):
        r, _ = audit(eia([claim("River effluent BOD", 18.0, 30.0), claim("Effluent Lead (Pb)", 0.3, 0.1)]))
        self.assertEqual(r.forensic_checks[0].status, "FAILED")
        self.assertIn("Lead", r.forensic_checks[0].statistical_discrepancy)

    def test_ph_below_lower_bound_is_breach(self):
        c = claim("Surface water pH", 4.2, 8.5, unit="pH", lower=6.5, conc=False)
        self.assertTrue(claim_limit_breach(c))
        r, _ = audit(eia([c]))
        self.assertNotEqual(r.verdict_banner, COMPLIANT)

    def test_ph_within_range_is_cleared(self):
        r, _ = audit(eia([claim("Surface water pH", 7.4, 8.5, unit="pH", lower=6.5, conc=False)]))
        self.assertEqual(r.verdict_banner, COMPLIANT)

    def test_missing_limit_is_not_cleared(self):
        r, _ = audit(eia([claim("River effluent BOD", 18.0, 0.0)]))
        self.assertEqual(r.forensic_checks[0].status, "WARNING")
        self.assertNotEqual(r.verdict_banner, COMPLIANT)
        self.assertNotEqual(r.verdict_banner, "VERIFIED BASELINE INTEGRITY")
        self.assertNotEqual(r.dispatch_action.action_type, "COMPLIANCE_CLEARED")

    def test_soil_parameters_anywhere_in_name_are_unregulated(self):
        for p in ("Lead (Pb) in soil", "Top soil pH", "Soil pH"):
            self.assertIsNone(claim_limit_breach(claim(p, 45.0, 30.0)), p)

    def test_failed_spatial_check_blocks_clearance(self):
        x = eia([claim("River effluent BOD", 18.0, 30.0)], with_air_sensor=True)
        # Sensor sits NW of the source; wind blowing from the NW makes it upwind.
        r, ev = audit(x, {**DRY, "wind": {"prevailing_from_deg": 315.0, "spread_deg": 20.0}})
        self.assertTrue(ev.wind.sensor_is_upwind)
        self.assertEqual(r.forensic_checks[1].status, "FAILED")
        self.assertNotEqual(r.verdict_banner, COMPLIANT)

    def test_soil_normal_range_is_not_a_statutory_breach(self):
        soil = claim("Soil pH", 9.09, 8.5, unit="pH", lower=6.0, conc=False)
        self.assertIsNone(claim_limit_breach(soil))
        r, _ = audit(eia([claim("River effluent BOD", 18.0, 30.0), soil]))
        self.assertEqual(r.verdict_banner, COMPLIANT)

    def test_phenol_and_sulphide_are_concentrations(self):
        for p in ("Effluent Phenol", "Effluent Sulphide", "Effluent Phosphate"):
            self.assertTrue(is_surface_effluent_concentration(claim(p, 0.5, 1.0)), p)
        self.assertFalse(is_surface_effluent_concentration(claim("Effluent pH", 7.0, 8.5, unit="pH")))


class ProvisionalData(unittest.TestCase):
    def test_synthetic_layers_block_enforcement(self):
        x = eia([claim("River effluent BOD", 45.0, 30.0)])
        ev = verify_filing(x, None, prefer_live=False)
        self.assertTrue(all(p.mode == "DETERMINISTIC_FALLBACK" for p in ev.provenance))
        r = build_deterministic_report(x, ev)
        self.assertTrue(r.provisional_layers)
        self.assertEqual(r.dispatch_action.action_type, "PROVISIONAL_DATA_REVIEW")

    def test_live_layers_allow_enforcement(self):
        x = eia([claim("River effluent BOD", 45.0, 30.0)])
        ev = verify_filing(x, None, prefer_live=False)
        ev = ev.model_copy(update={"provenance": [p.model_copy(update={"mode": "LIVE"}) for p in ev.provenance]})
        r = build_deterministic_report(x, ev)
        self.assertEqual(r.provisional_layers, [])
        self.assertEqual(r.dispatch_action.action_type, "COUNTER_SAMPLING_DISPATCH")


class ReddBaseline(unittest.TestCase):
    def _redd(self, claimed_rate, observed_rate):
        return audit(*self._redd_inputs(claimed_rate, observed_rate))

    def _redd_inputs(self, claimed_rate, observed_rate):
        x = ExtractedFilingData(
            filing_mode="CARBON_OFFSET", subsector="FORESTRY_REDD", forestry_metric_type="AVOIDED_DEFORESTATION_RATE",
            project_title="Test REDD", applicant_or_proponent="Test", region_name="Test", summary_of_claim="Test",
            coordinates=[GeoPoint(label="Centroid", role="PROJECT_SITE", latitude=-17.0, longitude=28.0)],
            sampling_claims=[claim("Baseline Deforestation Rate", 0.1, claimed_rate, unit="%/yr", conc=False)],
            extracted_methodology_notes=[])
        profile = {"ndvi": {"baseline_ndvi": 0.6, "like_season_baseline_ndvi": 0.6, "measurement_ndvi": 0.6},
                   "buffer": {"baseline_loss_rate_pct": observed_rate, "ring_change_pct": [0, 0, 0, 0]}}
        return x, profile

    def test_uses_observed_rate_not_constant(self):
        r, ev = self._redd(claimed_rate=2.5, observed_rate=2.0)
        self.assertEqual(ev.buffer.baseline_loss_rate_pct, 2.0)
        self.assertEqual(r.forensic_checks[0].status, "PASSED")

    def test_inflated_baseline_fails(self):
        r, _ = self._redd(claimed_rate=3.9, observed_rate=0.9)
        self.assertEqual(r.forensic_checks[0].status, "FAILED")

    def test_conservative_baseline_passes_despite_seasonal_ndvi(self):
        x, profile = self._redd_inputs(claimed_rate=1.3, observed_rate=2.35)
        profile["ndvi"] = {"baseline_ndvi": 0.2, "like_season_baseline_ndvi": 0.42, "measurement_ndvi": 0.53}
        r, _ = audit(x, profile)
        self.assertEqual(r.forensic_checks[0].status, "PASSED")
        self.assertIn("conservative", r.forensic_checks[0].statistical_discrepancy)

    def test_undisclosed_elevation_still_compares_control_to_project(self):
        x, profile = self._redd_inputs(claimed_rate=1.0, observed_rate=1.0)
        x = x.model_copy(update={"coordinates": x.coordinates + [
            GeoPoint(label="Reference area", role="CONTROL_SITE", latitude=-18.2, longitude=28.9, claimed_elevation_m=None)]})
        profile["terrain"] = {"PROJECT_SITE": {"elevation_m": 646.0, "slope_deg": 7.9},
                              "CONTROL_SITE": {"elevation_m": 1228.0, "slope_deg": 1.5}}
        r, _ = audit(x, profile)
        self.assertEqual(r.forensic_checks[1].status, "WARNING")
        self.assertIn("Baseline Topography Not Disclosed", r.forensic_checks[1].statistical_discrepancy)


class ReferenceAreaPressure(unittest.TestCase):
    """Kariba-style baseline: rate taken from a reference area that deforests far faster than the project area."""

    def _inputs(self, project_rate, reference_rate):
        x = ExtractedFilingData(
            filing_mode="CARBON_OFFSET", subsector="FORESTRY_REDD", forestry_metric_type="AVOIDED_DEFORESTATION_RATE",
            project_title="Test REDD", applicant_or_proponent="Test", region_name="Test", summary_of_claim="Test",
            coordinates=[GeoPoint(label="District A", role="PROJECT_SITE", latitude=-16.5, longitude=29.9),
                         GeoPoint(label="District B", role="PROJECT_SITE", latitude=-17.0, longitude=28.6),
                         GeoPoint(label="Reference town", role="CONTROL_SITE", latitude=-18.2, longitude=28.9)],
            sampling_claims=[SamplingClaim(parameter="Baseline Deforestation Rate", claimed_project_value=0.0,
                                           claimed_control_or_limit_value=1.3, unit="%/yr", sampling_date="2011-10-13",
                                           reference_period="2009-2011", page_citation="p23",
                                           is_concentration_pollutant=False)],
            extracted_methodology_notes=[])
        profile = {"ndvi": {"baseline_ndvi": 0.6, "like_season_baseline_ndvi": 0.6, "measurement_ndvi": 0.6},
                   "buffer": {"baseline_loss_rate_pct": project_rate, "ring_change_pct": [0, 0, 0, 0]},
                   "baseline": {"project_rate_pct": project_rate, "reference_rate_pct": reference_rate},
                   "terrain": {"PROJECT_SITE": {"elevation_m": 900.0, "slope_deg": 3.0},
                               "CONTROL_SITE": {"elevation_m": 950.0, "slope_deg": 2.5}}}
        return x, profile

    def test_reference_period_from_filing(self):
        _, ev = audit(*self._inputs(0.4, 1.3))
        self.assertEqual(ev.baseline_sampling.period, "2009–2011")
        self.assertEqual(sum(s.role == "PROJECT_SITE" for s in ev.baseline_sampling.sites), 2)

    def test_fast_reference_area_fails_control_check(self):
        r, _ = audit(*self._inputs(0.4, 1.3))
        self.assertEqual(r.forensic_checks[0].status, "WARNING")
        self.assertEqual(r.forensic_checks[1].status, "FAILED")
        self.assertIn("faster than the project area", r.forensic_checks[1].statistical_discrepancy)
        self.assertNotEqual(r.verdict_banner, "VERIFIED BASELINE INTEGRITY")
        self.assertEqual(r.dispatch_action.action_type, "REGISTRY_ESCROW_HOLD_AND_RANGER_ALERT")

    def test_matched_reference_area_passes(self):
        r, _ = audit(*self._inputs(1.2, 1.3))
        self.assertEqual(r.forensic_checks[0].status, "PASSED")
        self.assertEqual(r.forensic_checks[1].status, "PASSED")
        self.assertEqual(r.verdict_banner, "VERIFIED BASELINE INTEGRITY")
        self.assertIn("not registry approval", r.dispatch_action.instruction_directive)

    def test_default_reference_period_is_three_years_when_clamped(self):
        import datetime as dt
        from services.earth_engine_verifier import _reference_period
        self.assertEqual(_reference_period(None, dt.date(2026, 5, 1)), (2022, 2024))
        self.assertEqual(_reference_period(None, dt.date(2011, 10, 13)), (2009, 2011))

    def test_non_percentage_baseline_is_not_compared(self):
        x, profile = self._inputs(0.4, 0.5)
        c = x.sampling_claims[0].model_copy(update={"unit": "ha/yr", "claimed_control_or_limit_value": 12178.0})
        r, _ = audit(x.model_copy(update={"sampling_claims": [c]}), profile)
        self.assertEqual(r.forensic_checks[0].status, "WARNING")

    def test_disclosed_elevation_far_above_project_is_flagged(self):
        x, profile = self._inputs(1.2, 1.3)
        pts = [p.model_copy(update={"claimed_elevation_m": 1500.0}) if p.role == "CONTROL_SITE" else p
               for p in x.coordinates]
        profile["terrain"]["CONTROL_SITE"] = {"elevation_m": 1500.0, "slope_deg": 3.0}
        r, _ = audit(x.model_copy(update={"coordinates": pts}), profile)
        self.assertEqual(r.forensic_checks[1].status, "WARNING")

    def test_canopy_threshold_adapts_to_dry_woodland(self):
        from services.earth_engine_verifier import _canopy_threshold
        self.assertEqual(_canopy_threshold(22.0), 10)
        self.assertEqual(_canopy_threshold(75.0), 30)

    def test_rate_aggregates_by_forest_area(self):
        from services.earth_engine_verifier import _baseline_from_sums
        sites = [GeoPoint(label=n, role=r, latitude=0, longitude=0)
                 for n, r in (("A", "PROJECT_SITE"), ("B", "PROJECT_SITE"), ("Ref", "CONTROL_SITE"))]
        bs = _baseline_from_sums(sites, 2, [(10000, 300), (30000, 0), (20000, 1200)], 2009, 2011, 10, 20.0)
        self.assertEqual(bs.sites[0].loss_rate_pct, 1.0)
        self.assertEqual(bs.project_rate_pct, 0.25)
        self.assertEqual(bs.reference_rate_pct, 2.0)


class OfflineClassifier(unittest.TestCase):
    def _classify(self, text):
        from unittest import mock
        with mock.patch("services.gemini_extractor._pdf_text", return_value=[text]):
            return _heuristic_extract(b"", "filing.pdf", [])

    def test_eia_mentioning_wind_direction_stays_in_scope(self):
        x = self._classify("Environmental Impact Assessment. Ambient air quality. Predominant wind direction NW. "
                           "Stack height 30 m. Effluent discharge. Prepared for the State Pollution Control Board.")
        self.assertTrue(x.is_satellite_verifiable)
        self.assertEqual(x.subsector, "INDUSTRIAL_EIA")
        self.assertNotEqual(x.governing_body, "US EPA")

    def test_wind_farm_pdd_is_out_of_scope(self):
        x = self._classify("Grid-connected wind farm project under ACM0002. Wind power generation. "
                           "Power purchase agreement with the grid. SCADA metering.")
        self.assertFalse(x.is_satellite_verifiable)
        self.assertEqual(x.subsector, "RENEWABLE_ENERGY_GRID")

    def test_redd_with_arrangement_word_stays_redd(self):
        x = self._classify("REDD+ project design document. Avoided deforestation. Verra VCS. "
                           "Benefit-sharing arrangement with communities. Carbon credit issuance.")
        self.assertEqual(x.subsector, "FORESTRY_REDD")


if __name__ == "__main__":
    unittest.main()
