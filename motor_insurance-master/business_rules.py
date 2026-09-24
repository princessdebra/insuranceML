"""
Business Rules Engine (Appendix C, §C.1) plus additional fraud-pattern rules.

Each rule is deterministic and produces a plain-English `description` --
the same {"type", "severity", "description", "confidence"} shape already
used by photo/narrative anomalies in service.py -- so results can flow
straight into the risk-score blend, the AI advisory, and the AI-ROL audit
trail without a new schema.

Rules that need data the current intake flow doesn't collect yet (driver
age/licence, repair-shop identity, cost-revision history, market-value
reference data) accept that data as optional fields on `incident_details`
and simply skip themselves -- silently, not as a failure -- when it's
absent. That keeps every rule safe to enable today even though only a
subset will actually have data to fire against until the intake forms
grow those fields.
"""

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

SEVERITY_WEIGHT = {"low": 15, "medium": 35, "high": 60, "critical": 85}

# Illustrative only -- not a real valuation source. A production version
# would call an actual vehicle-valuation API/table.
_ILLUSTRATIVE_MARKET_VALUE_KES = {
    ("toyota", "premio"): 2_200_000,
    ("toyota", "hilux"): 3_200_000,
    ("toyota", "land cruiser"): 9_500_000,
    ("toyota", "axio"): 1_600_000,
    ("nissan", "x-trail"): 2_600_000,
    ("subaru", "forester"): 2_800_000,
    ("honda", "cr-v"): 2_900_000,
    ("mazda", "cx-5"): 3_300_000,
    ("mitsubishi", "pajero"): 3_600_000,
}

_COMMERCIAL_USE_KEYWORDS = (
    "uber", "bolt", "little cab", "taxi", "matatu", "delivery", "courier",
    "boda", "hire", "for hire", "ride-hailing", "ride hailing", "business errand",
)

_THEFT_KEYWORDS = ("stolen", "theft", "carjack", "hijack")

# Admin-configurable thresholds (Settings page, Admin Portal) -- every entry
# here is a numeric knob a rule below reads via self.config[key] instead of
# a hardcoded literal, so an admin can retune sensitivity without a code
# deploy. `label`/`group`/`help` drive the settings UI; `default` is what
# applies until database.py's system_config table has an override for that
# key. Keep this list and the rules that read from it in sync -- a key
# removed here but still referenced in a rule will KeyError.
CONFIG_SCHEMA = [
    {"key": "early_claim_hours", "label": "Early-claim window", "group": "Timing", "default": 24, "unit": "hours",
     "help": "Flag claims filed within this many hours of the policy's start date."},
    {"key": "lapse_days", "label": "Lapse-then-renewal threshold", "group": "Timing", "default": 7, "unit": "days",
     "help": "Flag when a same-type prior policy lapsed more than this many days before the current one started."},
    {"key": "late_reporting_days", "label": "Late-reporting threshold", "group": "Timing", "default": 30, "unit": "days",
     "help": "Flag claims reported more than this many days after the incident."},
    {"key": "repeat_claimant_window_days", "label": "Repeat-claimant window", "group": "Timing", "default": 365, "unit": "days",
     "help": "Look-back window for counting a policyholder's other claims."},
    {"key": "repeat_claimant_count", "label": "Repeat-claimant count", "group": "Timing", "default": 3, "unit": "claims",
     "help": "Flag once a policyholder reaches this many claims within the look-back window."},
    {"key": "cover_upgrade_window_days", "label": "Cover-upgrade window", "group": "Timing", "default": 7, "unit": "days",
     "help": "Flag when cover was upgraded within this many days before the incident."},
    {"key": "cover_upgrade_high_severity_days", "label": "Cover-upgrade high-severity cutoff", "group": "Timing", "default": 2, "unit": "days",
     "help": "Within this many days of the upgrade, the finding is raised to high severity instead of medium."},
    {"key": "cost_ratio_medium_min", "label": "Cost/sum-insured -- medium band starts at", "group": "Cost", "default": 0.65, "unit": "ratio",
     "help": "Repair estimate as a fraction of sum insured where the medium-severity band begins."},
    {"key": "cost_ratio_high_min", "label": "Cost/sum-insured -- high band starts at", "group": "Cost", "default": 0.70, "unit": "ratio",
     "help": "Repair estimate as a fraction of sum insured where this is treated as a likely total loss."},
    {"key": "sum_insured_deviation_pct", "label": "Sum-insured deviation threshold", "group": "Cost", "default": 0.30, "unit": "ratio",
     "help": "Flag when the insured value differs from the illustrative market value by more than this fraction."},
    {"key": "min_evidence_photo_count", "label": "Minimum recommended photos", "group": "Evidence", "default": 3, "unit": "photos",
     "help": "Flag claims with fewer than this many uploaded photos."},
    {"key": "photo_metadata_distance_km", "label": "Photo GPS variance distance", "group": "Evidence", "default": 2, "unit": "km",
     "help": "Flag a photo captured more than this far from the claim's incident location."},
    {"key": "photo_metadata_hours", "label": "Photo timestamp variance", "group": "Evidence", "default": 6, "unit": "hours",
     "help": "Flag a photo captured more than this many hours from the incident time."},
    {"key": "recent_change_days", "label": "Recent cover-change window", "group": "Domestic", "default": 7, "unit": "days",
     "help": "Flag when a Domestic policy's Contents/Buildings limit was increased within this many days before the loss."},
    {"key": "quantity_variance_tolerance_pct", "label": "Cargo quantity/weight variance tolerance", "group": "Marine", "default": 0.02, "unit": "ratio",
     "help": "Flag when delivered quantity differs from documented (invoice/packing list) quantity by more than this fraction."},
]
DEFAULT_CONFIG = {c["key"]: c["default"] for c in CONFIG_SCHEMA}


@dataclass
class RuleFinding:
    rule_id: str
    severity: str  # low / medium / high / critical
    description: str
    confidence: int  # 0-100

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.rule_id,
            "severity": self.severity,
            "description": self.description,
            "confidence": self.confidence,
        }


@dataclass
class BusinessRulesResult:
    findings: List[RuleFinding] = field(default_factory=list)
    risk_score: int = 0
    skipped: List[str] = field(default_factory=list)  # rule_ids that had no data to evaluate

    def to_dict(self) -> Dict[str, Any]:
        return {
            "findings": [f.to_dict() for f in self.findings],
            "risk_score": self.risk_score,
            "rules_fired": [f.rule_id for f in self.findings],
            "rules_skipped_no_data": self.skipped,
        }

    @property
    def observation_text(self) -> str:
        if not self.findings:
            return "None"
        return "\n".join(f"- [{f.severity.upper()}] {f.description}" for f in self.findings)


def _parse_date(value: Any) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    # last resort: first 10 chars as YYYY-MM-DD
    try:
        return datetime.strptime(text[:10], "%Y-%m-%d")
    except ValueError:
        return None


class BusinessRulesEngine:
    """Evaluates every rule and returns a single aggregated result."""

    def __init__(self, db_manager):
        self.db = db_manager
        try:
            overrides = self.db.get_system_config_overrides()
        except Exception as e:
            logger.warning(f"Could not load business-rule config overrides, using defaults: {e}")
            overrides = {}
        self.config = {**DEFAULT_CONFIG, **overrides}

    def evaluate(
        self,
        claim_id: str,
        member_id: str,
        policy_id: str,
        claim_type: str,
        narrative_text: str,
        photo_count: int,
        incident_details: Optional[Dict[str, Any]] = None,
    ) -> BusinessRulesResult:
        incident_details = incident_details or {}
        result = BusinessRulesResult()

        claim = self.db.get_claim(claim_id) or {}
        policies = self.db.get_member_policies(member_id) or []
        policy = next((p for p in policies if p.get("policy_id") == policy_id), None)
        policy_sections: List[Dict[str, Any]] = []
        if policy:
            policy = dict(policy)
            # Which *_policy_details table to enrich from depends on
            # claim_type -- same dispatch agents.py's _get_member_policy
            # already uses, kept in sync so both modules resolve the same
            # policy shape for a given claim type.
            detail_table_and_columns = {
                "motor": ("motor_policy_details", "vehicle_make, vehicle_model, vehicle_year, class_of_use, cover_upgrade_date"),
                "marine_cargo": ("marine_policy_details", "cargo_description, origin, destination, conveyance_type, icc_clause, packing_warranty, cover_type, clause, shipment_id, declaration_reference"),
                "marine_hull": ("marine_hull_policy_details", "vessel_id, registration_number, description, vessel_use, navigation_area_description, navigation_zone, survey_valid_to, operator_certificate_valid_to, insured_value, deductible"),
                "goods_in_transit": ("goods_in_transit_policy_details", "transit_id, approved_vehicle_reg, approved_driver_name, approved_transporter_name, commodity, conveyance_limit, overnight_parking_warranty, route_description"),
                "domestic": ("domestic_policy_details", "premises_address, buildings_sum_insured, contents_sum_insured, valuables_limit, security_warranty, fuel_storage_wording, contents_limit_changed_at, contents_limit_previous"),
            }
            table_columns = detail_table_and_columns.get(claim_type)
            try:
                with self.db.get_connection() as conn:
                    cursor = conn.cursor()
                    if table_columns:
                        table, columns = table_columns
                        cursor.execute(f"SELECT {columns} FROM {table} WHERE policy_id = ?", (policy_id,))
                        row = cursor.fetchone()
                        if row:
                            policy.update(dict(row))
                    cursor.execute(
                        "SELECT section_name, limit_amount, excess, selected, effective_date "
                        "FROM policy_sections WHERE policy_id = ?",
                        (policy_id,),
                    )
                    policy_sections = [dict(r) for r in cursor.fetchall()]
            except Exception as e:
                logger.warning(f"Could not enrich policy {policy_id} for claim_type {claim_type}: {e}")
        member = self.db.get_member_info(member_id) or {}

        accident_dt = _parse_date(claim.get("accident_time")) or _parse_date(claim.get("created_at"))
        created_dt = _parse_date(claim.get("created_at"))
        estimated_cost = float(claim.get("estimated_cost") or incident_details.get("estimated_cost") or 0)

        self._rule_early_claim_after_inception(result, policy, accident_dt)
        self._rule_lapse_then_renewal(result, member_id, policy)
        self._rule_repeat_claimant_12mo(result, member_id, claim_id)
        self._rule_late_reporting(result, accident_dt, created_dt)
        self._rule_cost_to_sum_insured_ratio(result, estimated_cost, policy)
        self._rule_minimum_evidence(result, photo_count, incident_details)
        self._rule_sum_insured_deviation(result, policy)
        self._rule_driver_eligibility(result, policy_id, incident_details, accident_dt)
        self._rule_shared_identity(result, member_id, claim_id, member)
        self._rule_repeat_repair_shop(result, claim, incident_details)
        self._rule_cost_escalation(result, claim, incident_details)
        self._rule_unreported_theft(result, narrative_text, estimated_cost, policy, incident_details)
        self._rule_usage_class_mismatch(result, narrative_text, policy)
        self._rule_off_hours_filing(result, created_dt)
        self._rule_cost_divergence_across_parties(result, claim)
        self._rule_cost_reasonableness(result, claim)
        self._rule_policy_incident_date_window(result, policy, accident_dt)
        self._rule_cover_upgrade_before_incident(result, policy, accident_dt)
        self._rule_photo_metadata_variance(result, claim, accident_dt)
        self._rule_severity_narrative_mismatch(result, claim_id, narrative_text)
        self._rule_location_narrative_correlation(result, claim)

        # Domestic / Marine Hull / Marine Cargo / Goods in Transit --
        # BR-POL-001 (policy validity) is already covered for every claim
        # type by _rule_policy_incident_date_window above (it only reads
        # policy.start_date/end_date and accident_dt, nothing motor-
        # specific). BR-MET-008 (evidence metadata variance) is likewise
        # already covered by _rule_photo_metadata_variance above.
        self._rule_cover_section_applicability(result, claim_type, narrative_text, policy_sections)
        self._rule_insured_subject_match(result, claim_type, policy, incident_details)
        self._rule_geographic_scope(result, claim_type, policy, incident_details)
        self._rule_security_warranty_referral(result, claim_type, policy, incident_details, narrative_text)
        self._rule_recent_cover_change(result, claim_type, policy, accident_dt)
        self._rule_transit_custody_window(result, claim_type, policy, accident_dt)
        self._rule_quantity_reconciliation(result, claim_type, claim_id)
        self._rule_approved_transport_party(result, claim_type, policy, incident_details)

        result.risk_score = min(100, sum(SEVERITY_WEIGHT.get(f.severity, 0) for f in result.findings))
        return result

    # ------------------------------------------------------------------
    # Appendix C gap closures
    # ------------------------------------------------------------------

    def _rule_early_claim_after_inception(self, result, policy, accident_dt):
        rule_id = "early_claim_after_inception"
        if not policy or not accident_dt:
            result.skipped.append(rule_id)
            return
        start_dt = _parse_date(policy.get("start_date"))
        if not start_dt:
            result.skipped.append(rule_id)
            return
        hours_since_start = (accident_dt - start_dt).total_seconds() / 3600
        if 0 <= hours_since_start < self.config["early_claim_hours"]:
            result.findings.append(RuleFinding(
                rule_id, "high",
                "This claim was filed less than a day after the policy started -- "
                "worth double-checking it wasn't taken out because the loss had already happened.",
                80,
            ))

    def _rule_lapse_then_renewal(self, result, member_id, policy):
        rule_id = "lapse_then_renewal"
        if not policy:
            result.skipped.append(rule_id)
            return
        start_dt = _parse_date(policy.get("start_date"))
        if not start_dt:
            result.skipped.append(rule_id)
            return
        policies = self.db.get_member_policies(member_id) or []
        same_type_prior = [
            p for p in policies
            if p.get("policy_type") == policy.get("policy_type")
            and p.get("policy_id") != policy.get("policy_id")
            and _parse_date(p.get("end_date"))
            and _parse_date(p.get("end_date")) < start_dt
        ]
        if not same_type_prior:
            result.skipped.append(rule_id)
            return
        most_recent_prior = max(same_type_prior, key=lambda p: _parse_date(p.get("end_date")))
        prior_end = _parse_date(most_recent_prior.get("end_date"))
        lapse_days = (start_dt - prior_end).days
        if lapse_days > self.config["lapse_days"]:
            result.findings.append(RuleFinding(
                rule_id, "high",
                f"This policy lapsed for {lapse_days} days before being renewed -- "
                "a lapse followed shortly by a claim is a pattern that's shown up in past fraud cases.",
                70,
            ))

    def _rule_repeat_claimant_12mo(self, result, member_id, claim_id):
        rule_id = "repeat_claimant_12mo"
        try:
            with self.db.get_connection() as conn:
                cursor = conn.cursor()
                window_days = self.config["repeat_claimant_window_days"]
                cursor.execute(
                    f"""
                    SELECT COUNT(*) as cnt FROM claims
                    WHERE member_id = ? AND claim_id != ?
                    AND created_at >= datetime('now', '-{int(window_days)} days')
                    """,
                    (member_id, claim_id),
                )
                count = cursor.fetchone()["cnt"] + 1  # +1 to include this claim
        except Exception as e:
            logger.warning(f"repeat_claimant_12mo lookup failed: {e}")
            result.skipped.append(rule_id)
            return
        if count >= self.config["repeat_claimant_count"]:
            result.findings.append(RuleFinding(
                rule_id, "medium",
                f"This is this policyholder's {count}{'rd' if count == 3 else 'th'} claim in the past year -- "
                "not automatically a problem, but worth a closer look at the pattern.",
                60,
            ))

    def _rule_late_reporting(self, result, accident_dt, created_dt):
        rule_id = "late_reporting"
        if not accident_dt or not created_dt:
            result.skipped.append(rule_id)
            return
        days_to_report = (created_dt - accident_dt).days
        if days_to_report > self.config["late_reporting_days"]:
            result.findings.append(RuleFinding(
                rule_id, "low",
                f"This claim was reported {days_to_report} days after the incident happened -- "
                "a delay that can make evidence harder to verify.",
                50,
            ))

    def _rule_cost_to_sum_insured_ratio(self, result, estimated_cost, policy):
        rule_id = "cost_to_sum_insured_ratio"
        if not policy or not policy.get("sum_insured") or estimated_cost <= 0:
            result.skipped.append(rule_id)
            return
        ratio = estimated_cost / float(policy["sum_insured"])
        medium_min, high_min = self.config["cost_ratio_medium_min"], self.config["cost_ratio_high_min"]
        if medium_min <= ratio < high_min:
            result.findings.append(RuleFinding(
                rule_id, "medium",
                f"The repair estimate is {ratio:.0%} of the sum insured -- close to the point where it "
                "would be cheaper to write the car off. This gets a specialist look before repairs are approved.",
                65,
            ))
        elif ratio >= high_min:
            result.findings.append(RuleFinding(
                rule_id, "high",
                f"The repair estimate is {ratio:.0%} of the sum insured -- likely a total-loss case, "
                "not a standard repair.",
                75,
            ))

    def _rule_minimum_evidence(self, result, photo_count, incident_details):
        rule_id = "minimum_evidence"
        # Advisory only -- does not block submission. A hard gate here would
        # conflict with the analyst-on-a-call flow, where photos are
        # deliberately optional (see ClaimChatbot.tsx analystMode).
        missing = []
        min_photos = self.config["min_evidence_photo_count"]
        if photo_count < min_photos:
            missing.append(f"only {photo_count} photo(s) uploaded (recommended minimum: {min_photos})")
        if incident_details.get("police_reported") and not incident_details.get("ob_number"):
            missing.append("police involvement was reported but no OB/abstract number was captured")
        if missing:
            result.findings.append(RuleFinding(
                rule_id, "low",
                "This claim is missing supporting evidence: " + "; ".join(missing) + ".",
                90,
            ))

    # ------------------------------------------------------------------
    # New rules
    # ------------------------------------------------------------------

    def _rule_sum_insured_deviation(self, result, policy):
        rule_id = "sum_insured_deviation"
        if not policy:
            result.skipped.append(rule_id)
            return
        make = (policy.get("vehicle_make") or "").strip().lower()
        model = (policy.get("vehicle_model") or "").strip().lower()
        sum_insured = policy.get("sum_insured")
        market_value = _ILLUSTRATIVE_MARKET_VALUE_KES.get((make, model))
        if not market_value or not sum_insured:
            result.skipped.append(rule_id)
            return
        deviation = abs(float(sum_insured) - market_value) / market_value
        if deviation > self.config["sum_insured_deviation_pct"]:
            direction = "above" if sum_insured > market_value else "below"
            result.findings.append(RuleFinding(
                rule_id, "medium",
                f"The insured value (KES {sum_insured:,.0f}) is {deviation:.0%} {direction} the typical "
                f"market value for this make and model (~KES {market_value:,.0f}) -- "
                "over-insurance is a known way to profit from a total loss.",
                50,
            ))

    # Kenyan driving-licence classes that meet a motor comprehensive
    # policy's usual "class B or above" bar -- B (car), and the higher
    # commercial classes that subsume it. A (motorcycle-only) and F
    # (provisional/learner) don't qualify to drive the insured vehicle
    # unsupervised.
    _ACCEPTABLE_LICENCE_CLASSES = {"B", "BE", "C", "CE", "D", "DE"}

    def _rule_driver_eligibility(self, result, policy_id, incident_details, accident_dt=None):
        rule_id = "driver_eligibility"

        # incident_details can override/name the specific driver from the
        # narrative (e.g. "driver_name": "Kevin Otieno"); otherwise every
        # authorised driver on the policy is checked, since the intake flow
        # doesn't always confirm which named driver was actually behind
        # the wheel.
        driver_name = incident_details.get("driver_name")
        drivers = self.db.get_policy_drivers(policy_id) if policy_id else []
        if not drivers:
            result.skipped.append(rule_id)
            return
        if driver_name:
            drivers = [d for d in drivers if d.get("driver_name", "").lower() == driver_name.lower()] or drivers

        flagged = False
        for d in drivers:
            age = d.get("age")
            min_age = d.get("min_permitted_age", 18)
            if age is not None and age < min_age:
                result.findings.append(RuleFinding(
                    rule_id, "high",
                    f"{d.get('driver_name', 'A driver')} on this policy is {age} years old, below the "
                    f"minimum permitted driver age of {min_age}.",
                    85,
                ))
                flagged = True
            licence_class = (d.get("licence_class") or "").upper()
            if licence_class == "F":
                result.findings.append(RuleFinding(
                    rule_id, "medium",
                    f"{d.get('driver_name', 'A driver')} on this policy holds a provisional/learner "
                    f"licence (class F), which shouldn't be driving unsupervised.",
                    65,
                ))
                flagged = True
            elif licence_class and licence_class not in self._ACCEPTABLE_LICENCE_CLASSES:
                result.findings.append(RuleFinding(
                    rule_id, "medium",
                    f"{d.get('driver_name', 'A driver')} holds licence class {licence_class}, which "
                    f"doesn't meet the class B-or-above requirement for driving this vehicle.",
                    60,
                ))
                flagged = True

            licence_expiry = _parse_date(d.get("licence_expiry"))
            if licence_expiry and accident_dt and licence_expiry < accident_dt:
                result.findings.append(RuleFinding(
                    rule_id, "high",
                    f"{d.get('driver_name', 'A driver')}'s licence expired on "
                    f"{licence_expiry.strftime('%Y-%m-%d')}, before the incident date.",
                    80,
                ))
                flagged = True
        if not flagged:
            result.skipped.append(rule_id)

    def _rule_policy_incident_date_window(self, result, policy, accident_dt):
        """
        BR-POL-001 (policy must be active on the incident date) and
        BR-DAT-003 (incident date must fall within the policy's effective/
        expiry window) collapse into one check -- they're the same
        underlying fact. Distinct from early_claim_after_inception above,
        which checks proximity to the START date as a fraud signal; this
        checks the incident actually falls inside the policy's coverage
        window at all, which is a coverage-validity question, not a fraud
        signal.
        """
        rule_id = "policy_incident_date_window"
        if not policy or not accident_dt:
            result.skipped.append(rule_id)
            return
        start = _parse_date(policy.get("start_date"))
        end = _parse_date(policy.get("end_date"))
        if not start or not end:
            result.skipped.append(rule_id)
            return
        if accident_dt < start or accident_dt > end:
            result.findings.append(RuleFinding(
                rule_id, "high",
                f"The incident date ({accident_dt.strftime('%Y-%m-%d')}) falls outside this policy's "
                f"coverage window ({start.strftime('%Y-%m-%d')} to {end.strftime('%Y-%m-%d')}) -- "
                "coverage on the date of loss needs verifying before this claim can proceed.",
                90,
            ))
        else:
            result.skipped.append(rule_id)

    def _rule_cover_upgrade_before_incident(self, result, policy, accident_dt):
        rule_id = "cover_upgrade_before_incident"
        if not policy or not accident_dt:
            result.skipped.append(rule_id)
            return
        upgrade_date = _parse_date(policy.get("cover_upgrade_date"))
        if not upgrade_date:
            result.skipped.append(rule_id)
            return
        days_between = (accident_dt.date() - upgrade_date.date()).days
        if 0 <= days_between <= self.config["cover_upgrade_window_days"]:
            severity = "high" if days_between <= self.config["cover_upgrade_high_severity_days"] else "medium"
            result.findings.append(RuleFinding(
                rule_id, severity,
                f"Cover was upgraded on {upgrade_date.strftime('%Y-%m-%d')}, just {days_between} day(s) "
                f"before this incident -- worth validating the upgrade was genuine before relying on the "
                f"new cover level.",
                70 if severity == "high" else 55,
            ))
        else:
            result.skipped.append(rule_id)

    @staticmethod
    def _haversine_km(lat1, lon1, lat2, lon2):
        from math import radians, sin, cos, sqrt, atan2
        r = 6371.0
        p1, p2 = radians(lat1), radians(lat2)
        dphi = radians(lat2 - lat1)
        dlambda = radians(lon2 - lon1)
        a = sin(dphi / 2) ** 2 + cos(p1) * cos(p2) * sin(dlambda / 2) ** 2
        return 2 * r * atan2(sqrt(a), sqrt(1 - a))

    def _rule_photo_metadata_variance(self, result, claim, accident_dt):
        rule_id = "photo_metadata_variance"
        incident_lat, incident_lon = claim.get("incident_lat"), claim.get("incident_lon")
        claim_id = claim.get("claim_id")
        if not claim_id or incident_lat is None or incident_lon is None:
            result.skipped.append(rule_id)
            return
        try:
            with self.db.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT filename, capture_lat, capture_lon, capture_time FROM claim_photo_files "
                    "WHERE claim_id = ? AND capture_lat IS NOT NULL AND capture_lon IS NOT NULL",
                    (claim_id,),
                )
                photos = [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            logger.warning(f"Could not check photo metadata variance for {claim_id}: {e}")
            result.skipped.append(rule_id)
            return

        if not photos:
            result.skipped.append(rule_id)
            return

        flagged = False
        max_distance_km = self.config["photo_metadata_distance_km"]
        max_hours = self.config["photo_metadata_hours"]
        for p in photos:
            distance_km = self._haversine_km(incident_lat, incident_lon, p["capture_lat"], p["capture_lon"])
            capture_time = _parse_date(p.get("capture_time"))
            hours_diff = abs((capture_time - accident_dt).total_seconds() / 3600) if capture_time and accident_dt else None

            if distance_km > max_distance_km or (hours_diff is not None and hours_diff > max_hours):
                reasons = []
                if distance_km > max_distance_km:
                    reasons.append(f"captured {distance_km:.1f}km from the reported incident location")
                if hours_diff is not None and hours_diff > max_hours:
                    reasons.append(f"captured {hours_diff:.1f} hours from the reported incident time")
                result.findings.append(RuleFinding(
                    rule_id, "high",
                    f"Photo '{p['filename']}' was {' and '.join(reasons)} -- worth confirming why "
                    f"(e.g. vehicle moved to a safe location) rather than assuming anything is wrong.",
                    75,
                ))
                flagged = True
        if not flagged:
            result.skipped.append(rule_id)

    _MINIMIZING_NARRATIVE_PHRASES = (
        "minor", "small scratch", "fender bender", "not serious", "slight",
        "little damage", "barely", "just a scratch", "nothing major",
    )

    def _rule_severity_narrative_mismatch(self, result, claim_id, narrative_text):
        """
        BR-SEV-009: the narrative downplays the incident ("just a minor
        bump") while the vision model's own severity read on the uploaded
        photos says otherwise. A mismatch here doesn't establish who's
        right -- narrative language is subjective and CV severity is
        advisory -- it just flags the disagreement for a handler to look
        at, same spirit as every other cross-validation rule here.
        """
        rule_id = "severity_narrative_mismatch"
        text = (narrative_text or "").lower()
        if not text or not claim_id:
            result.skipped.append(rule_id)
            return
        minimizing = next((p for p in self._MINIMIZING_NARRATIVE_PHRASES if p in text), None)
        if not minimizing:
            result.skipped.append(rule_id)
            return

        try:
            claim = self.db.get_claim(claim_id) or {}
            analysis_result = claim.get("analysis_result")
            if isinstance(analysis_result, str):
                analysis_result = json.loads(analysis_result) if analysis_result else {}
            photo_results = (analysis_result or {}).get("photo_analysis", {}).get("results", [])
        except Exception as e:
            logger.warning(f"Could not check severity/narrative mismatch for {claim_id}: {e}")
            result.skipped.append(rule_id)
            return

        severe_photos = [p for p in photo_results if (p.get("cv_severity") or "").lower() in ("severe", "high")]
        if severe_photos:
            result.findings.append(RuleFinding(
                rule_id, "medium",
                f"The narrative describes this as \"{minimizing}\", but the uploaded photos show "
                f"{(severe_photos[0].get('cv_severity') or 'severe').lower()} damage -- worth clarifying "
                f"with the claimant before relying on either description alone.",
                60,
            ))
        else:
            result.skipped.append(rule_id)

    # Words too generic to count as a location match on their own (a photo
    # description and a narrative both saying "the road" or "outside"
    # shouldn't count as corroborating each other).
    _LOCATION_STOPWORDS = {
        "the", "a", "an", "near", "at", "on", "in", "outside", "along", "by",
        "road", "street", "avenue", "area", "junction", "roundabout", "town",
        "city", "estate", "location", "place", "car", "cars", "vehicle",
        "vehicles", "parking", "lot", "photo", "image", "scene", "shot",
    }

    @classmethod
    def _location_keywords(cls, text: str) -> set:
        words = re.findall(r"[a-zA-Z']{3,}", (text or "").lower())
        return {w for w in words if w not in cls._LOCATION_STOPWORDS}

    def _rule_location_narrative_correlation(self, result, claim):
        """
        Compares the claim's stated incident location (free text the member
        or analyst typed in) against location clues the vision model read
        out of any submitted context/location photos (part_identifier.py's
        describe_location_context -- run on photos classify_photo_purpose()
        ruled out as damage close-ups, e.g. a parking-lot shot submitted
        just to show where the member was). This is deliberately a plain
        keyword-overlap check rather than a second LLM call: every other
        rule in this engine is a fast, deterministic pass over data already
        fetched, and evaluate() is called synchronously from within the
        photo/narrative analysis pipeline -- adding an LLM round-trip here
        would make this one rule an outlier in both cost and failure mode.
        A keyword miss doesn't prove anything (paraphrasing, a landmark
        named differently, or a photo with just no readable clues are all
        normal and innocent) -- like every other advisory rule here, it
        just flags a mismatch worth a human glancing at, not a conclusion.
        """
        rule_id = "location_narrative_correlation"
        claim_id = claim.get("claim_id")
        stated_location = claim.get("location") or ""
        stated_keywords = self._location_keywords(stated_location)
        if not claim_id or not stated_keywords:
            result.skipped.append(rule_id)
            return

        try:
            claim_full = self.db.get_claim(claim_id) or claim
            analysis_result = claim_full.get("analysis_result")
            if isinstance(analysis_result, str):
                analysis_result = json.loads(analysis_result) if analysis_result else {}
            photo_results = (analysis_result or {}).get("photo_analysis", {}).get("results", [])
        except Exception as e:
            logger.warning(f"Could not check location/narrative correlation for {claim_id}: {e}")
            result.skipped.append(rule_id)
            return

        context_descriptions = [
            p.get("location_context") for p in photo_results if p.get("location_context")
        ]
        if not context_descriptions:
            result.skipped.append(rule_id)
            return

        best_overlap = 0
        best_desc = context_descriptions[0]
        for desc in context_descriptions:
            overlap = len(stated_keywords & self._location_keywords(desc))
            if overlap > best_overlap:
                best_overlap = overlap
                best_desc = desc

        if best_overlap == 0:
            result.findings.append(RuleFinding(
                rule_id, "medium",
                f"The claim's stated location (\"{stated_location.strip()}\") doesn't share any obvious "
                f"landmarks or keywords with what's visible in the submitted location photo "
                f"(\"{best_desc}\") -- worth a quick check that they're consistent, though a photo with "
                f"no readable signage or a differently-worded landmark name is a normal, innocent reason "
                f"for this too.",
                45,
            ))
        else:
            result.skipped.append(rule_id)

    def _rule_shared_identity(self, result, member_id, claim_id, member):
        rule_id = "shared_identity"
        phone = (member or {}).get("phone")
        if not phone:
            result.skipped.append(rule_id)
            return
        try:
            with self.db.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT DISTINCT c.member_id FROM claims c
                    JOIN members m ON m.member_id = c.member_id
                    WHERE m.phone = ? AND c.member_id != ? AND c.claim_id != ?
                    AND c.created_at >= datetime('now', '-365 days')
                    """,
                    (phone, member_id, claim_id),
                )
                others = [row["member_id"] for row in cursor.fetchall()]
        except Exception as e:
            logger.warning(f"shared_identity lookup failed: {e}")
            result.skipped.append(rule_id)
            return
        if others:
            result.findings.append(RuleFinding(
                rule_id, "high",
                f"The phone number on this claim is also linked to {len(others)} other "
                f"policyholder(s) with recent claims -- worth checking if these are genuinely separate people.",
                55,
            ))

    def _rule_repeat_repair_shop(self, result, claim, incident_details):
        rule_id = "repeat_repair_shop"
        shop_id = incident_details.get("repair_shop_id") or claim.get("repair_shop_id")
        if not shop_id:
            result.skipped.append(rule_id)
            return
        try:
            with self.db.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT name, fraud_flag_count FROM repair_shops WHERE shop_id = ?", (shop_id,))
                row = cursor.fetchone()
        except Exception as e:
            logger.warning(f"repeat_repair_shop lookup failed: {e}")
            result.skipped.append(rule_id)
            return
        if row and (row["fraud_flag_count"] or 0) >= 3:
            result.findings.append(RuleFinding(
                rule_id, "medium",
                f"This repair shop ({row['name']}) has appeared on {row['fraud_flag_count']} other flagged "
                "claims -- the pattern is about the shop, not just this one claim.",
                55,
            ))

    def _rule_cost_escalation(self, result, claim, incident_details):
        rule_id = "cost_escalation"
        initial = incident_details.get("initial_estimate") or claim.get("initial_estimated_cost")
        final = incident_details.get("final_estimate") or claim.get("estimated_cost")
        if not initial or not final or initial <= 0:
            result.skipped.append(rule_id)
            return
        ratio = final / initial
        if ratio > 1.5:
            result.findings.append(RuleFinding(
                rule_id, "medium",
                f"The repair estimate grew from KES {initial:,.0f} to KES {final:,.0f} "
                f"({ratio:.1f}x) -- cost creep like this is worth a second look.",
                55,
            ))

    def _rule_unreported_theft(self, result, narrative_text, estimated_cost, policy, incident_details):
        rule_id = "unreported_theft"
        text = (narrative_text or "").lower()
        if not any(k in text for k in _THEFT_KEYWORDS):
            result.skipped.append(rule_id)
            return
        sum_insured = (policy or {}).get("sum_insured")
        if not sum_insured or estimated_cost < 0.7 * float(sum_insured):
            result.skipped.append(rule_id)
            return
        if incident_details.get("police_reported") and incident_details.get("ob_number"):
            return  # properly reported -- no finding
        result.findings.append(RuleFinding(
            rule_id, "high",
            "This is a full vehicle theft claim with no police OB number on file -- a genuine theft "
            "in Kenya is almost always reported to police, so a missing report is unusual.",
            70,
        ))

    def _rule_usage_class_mismatch(self, result, narrative_text, policy):
        rule_id = "usage_class_mismatch"
        policy_class = (policy or {}).get("class_of_use", "private")
        if not policy_class or policy_class.lower() != "private":
            result.skipped.append(rule_id)
            return
        text = (narrative_text or "").lower()
        hit = next((kw for kw in _COMMERCIAL_USE_KEYWORDS if re.search(rf"\b{re.escape(kw)}\b", text)), None)
        if hit:
            result.findings.append(RuleFinding(
                rule_id, "high",
                f"The narrative mentions '{hit}', which suggests commercial or ride-hailing use, but this "
                "policy is registered for private use only -- private cover doesn't extend to commercial use.",
                55,
            ))

    def _rule_cost_divergence_across_parties(self, result, claim):
        rule_id = "cost_divergence_across_parties"
        member_cost = claim.get("initial_estimated_cost")
        assessor_cost = claim.get("assessor_estimated_cost")
        final_cost = claim.get("estimated_cost")

        figures = {
            "claimant": member_cost,
            "assessor": assessor_cost,
            "repair shop": final_cost,
        }
        present = {label: v for label, v in figures.items() if v and v > 0}
        if len(present) < 2:
            result.skipped.append(rule_id)
            return

        lowest_label = min(present, key=present.get)
        highest_label = max(present, key=present.get)
        lowest, highest = present[lowest_label], present[highest_label]
        if lowest <= 0:
            result.skipped.append(rule_id)
            return

        divergence = (highest - lowest) / lowest
        if divergence >= 0.5:
            severity = "high" if divergence >= 1.0 else "medium"
            result.findings.append(RuleFinding(
                rule_id, severity,
                f"The cost estimates on this claim diverge sharply between parties: "
                f"{lowest_label} said KES {lowest:,.0f}, {highest_label} said KES {highest:,.0f} "
                f"({divergence:.0%} apart). Worth reconciling before payout.",
                70 if severity == "high" else 55,
            ))
        else:
            result.skipped.append(rule_id)

    def _rule_cost_reasonableness(self, result, claim):
        """
        AI's own independent repair-cost estimate (part_identifier.
        estimate_damage_cost, computed from detected damage zones during
        photo analysis -- see service.py's analyze_multiparty_claim) versus
        whatever a human (member/assessor) entered. Distinct from
        cost_divergence_across_parties above, which only compares humans
        against each other -- this is the one independent check not sourced
        from a party with a stake in the payout.
        """
        rule_id = "cost_reasonableness"
        ai_cost = claim.get("ai_estimated_cost")
        quoted_cost = claim.get("estimated_cost")
        if not ai_cost or ai_cost <= 0 or not quoted_cost or quoted_cost <= 0:
            result.skipped.append(rule_id)
            return

        divergence = abs(quoted_cost - ai_cost) / ai_cost
        if divergence >= 0.3:
            severity = "high" if divergence >= 0.5 else "medium"
            direction = "higher than" if quoted_cost > ai_cost else "lower than"
            result.findings.append(RuleFinding(
                rule_id, severity,
                f"The quoted repair cost (KES {quoted_cost:,.0f}) is {divergence:.0%} {direction} "
                f"the AI's independent damage-based estimate (KES {ai_cost:,.0f}). Worth a closer look "
                f"before payout.",
                65 if severity == "high" else 50,
            ))
        else:
            result.skipped.append(rule_id)

    def _rule_off_hours_filing(self, result, created_dt):
        rule_id = "off_hours_filing"
        if not created_dt:
            result.skipped.append(rule_id)
            return
        if 0 <= created_dt.hour < 4:
            result.findings.append(RuleFinding(
                rule_id, "low",
                "This claim was filed in the middle of the night -- not suspicious on its own, "
                "but combined with other flags it adds a small amount of weight.",
                30,
            ))

    # ------------------------------------------------------------------
    # Domestic Package / Marine Hull / Marine Cargo / Goods in Transit
    #
    # BR-POL-001 (policy validity) and BR-MET-008 (evidence metadata
    # variance) are already covered for every claim type by
    # _rule_policy_incident_date_window and _rule_photo_metadata_variance
    # above -- neither references anything motor-specific, so no duplicate
    # rule is added here for those two IDs.
    # ------------------------------------------------------------------

    # Best-effort keyword→section mapping so a claim's implied cover
    # requirement can be checked against what was actually selected --
    # same "keyword overlap, not a second LLM call" philosophy as
    # _rule_usage_class_mismatch/_rule_location_narrative_correlation above.
    _DOMESTIC_CLAIM_SECTION_KEYWORDS = {
        "Buildings": ("fire", "roof", "structure", "wall", "storm", "flood", "escape of water", "burst pipe"),
        "Contents": ("television", "furniture", "appliance", "sofa", "cooker", "belongings", "contents"),
        "All Risks": ("laptop", "phone", "camera", "jewellery", "portable", "specified item"),
        "Burglary": ("burglary", "break-in", "broke in", "stolen", "theft", "burgled"),
        "Liability": ("visitor", "injured at", "guest", "liability", "injury on my property"),
        "Domestic employees": ("housekeeper", "gardener", "domestic employee", "nanny", "house help"),
    }

    def _rule_cover_section_applicability(self, result, claim_type, narrative_text, policy_sections):
        """BR-COV-002: the section a claim implies (e.g. a burglary claim
        needs Contents/All Risks/Burglary) must actually be selected on the
        policy -- a package policy's sections are opt-in, one selected
        section doesn't imply another (developer guide §"Cover or policy
        section")."""
        rule_id = "BR-COV-002"
        if claim_type != "domestic" or not policy_sections:
            result.skipped.append(rule_id)
            return
        text = (narrative_text or "").lower()
        implied_sections = [
            section for section, keywords in self._DOMESTIC_CLAIM_SECTION_KEYWORDS.items()
            if any(kw in text for kw in keywords)
        ]
        if not implied_sections:
            result.skipped.append(rule_id)
            return
        selected_names = {s["section_name"] for s in policy_sections if s.get("selected")}
        missing = [s for s in implied_sections if s not in selected_names]
        if missing:
            result.findings.append(RuleFinding(
                rule_id, "high",
                f"The narrative describes a loss that would normally fall under {', '.join(missing)}, "
                f"but the policy doesn't show that section as selected -- worth confirming cover before "
                f"proceeding.",
                70,
            ))
        else:
            result.skipped.append(rule_id)

    def _rule_insured_subject_match(self, result, claim_type, policy, incident_details):
        """BR-SUB-003: the claimed property/vessel/shipment/item must match
        the record actually insured -- a claim about the wrong vessel or an
        unscheduled item shouldn't pass silently."""
        rule_id = "BR-SUB-003"
        if not policy:
            result.skipped.append(rule_id)
            return

        if claim_type == "marine_hull":
            claimed_vessel = incident_details.get("vessel_id")
            policy_vessel = policy.get("vessel_id")
            if not claimed_vessel or not policy_vessel:
                result.skipped.append(rule_id)
                return
            if claimed_vessel != policy_vessel:
                result.findings.append(RuleFinding(
                    rule_id, "high",
                    f"The claim references vessel {claimed_vessel}, but this policy insures {policy_vessel} -- "
                    f"confirm the correct policy/vessel before proceeding.",
                    80,
                ))
            else:
                result.skipped.append(rule_id)
        elif claim_type in ("marine_cargo", "goods_in_transit"):
            claimed_ref = incident_details.get("shipment_id") or incident_details.get("transit_id")
            policy_ref = policy.get("shipment_id") or policy.get("transit_id")
            if not claimed_ref or not policy_ref:
                result.skipped.append(rule_id)
                return
            if claimed_ref != policy_ref:
                result.findings.append(RuleFinding(
                    rule_id, "high",
                    f"The claim references {claimed_ref}, but this policy/declaration is for {policy_ref} -- "
                    f"confirm the correct shipment before proceeding.",
                    80,
                ))
            else:
                result.skipped.append(rule_id)
        elif claim_type == "domestic":
            claimed_serial = incident_details.get("item_serial_number")
            if not claimed_serial:
                result.skipped.append(rule_id)
                return
            try:
                with self.db.get_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute(
                        "SELECT 1 FROM domestic_specified_items WHERE policy_id = ? AND serial_number = ?",
                        (policy.get("policy_id"), claimed_serial),
                    )
                    matched = cursor.fetchone() is not None
            except Exception as e:
                logger.warning(f"insured_subject_match lookup failed: {e}")
                result.skipped.append(rule_id)
                return
            if not matched:
                result.findings.append(RuleFinding(
                    rule_id, "medium",
                    f"The claimed item (serial {claimed_serial}) doesn't match any item scheduled on this "
                    f"policy -- confirm it's genuinely a specified item before treating it as covered under "
                    f"All Risks.",
                    65,
                ))
            else:
                result.skipped.append(rule_id)
        else:
            result.skipped.append(rule_id)

    def _rule_geographic_scope(self, result, claim_type, policy, incident_details):
        """BR-LOC-004: the loss location must fall inside what the policy
        actually covers -- Domestic's risk address, Hull's navigation area,
        or Goods in Transit's approved route."""
        rule_id = "BR-LOC-004"
        if not policy:
            result.skipped.append(rule_id)
            return

        if claim_type == "domestic":
            stated_address = incident_details.get("incident_address") or ""
            policy_address = policy.get("premises_address") or ""
            if not stated_address or not policy_address:
                result.skipped.append(rule_id)
                return
            if not (self._location_keywords(stated_address) & self._location_keywords(policy_address)):
                result.findings.append(RuleFinding(
                    rule_id, "high",
                    f"The reported loss location (\"{stated_address}\") doesn't obviously match the insured "
                    f"risk address (\"{policy_address}\") -- confirm this loss happened at the insured "
                    f"premises.",
                    70,
                ))
            else:
                result.skipped.append(rule_id)
        elif claim_type == "marine_hull":
            stated_zone = incident_details.get("navigation_zone")
            policy_zone = policy.get("navigation_zone")
            if not stated_zone or not policy_zone:
                result.skipped.append(rule_id)
                return
            if stated_zone != policy_zone:
                result.findings.append(RuleFinding(
                    rule_id, "high",
                    f"The incident is reported in \"{stated_zone}\", outside the vessel's permitted "
                    f"navigation area (\"{policy_zone}\") -- refer for a coverage check.",
                    75,
                ))
            else:
                result.skipped.append(rule_id)
        else:
            result.skipped.append(rule_id)

    def _rule_security_warranty_referral(self, result, claim_type, policy, incident_details, narrative_text=""):
        """BR-SEC-006: a required security/survey protection whose
        compliance can't be confirmed is a referral, not an automatic
        decline -- 'unusual does not mean fraudulent' (developer guide)."""
        rule_id = "BR-SEC-006"
        if not policy:
            result.skipped.append(rule_id)
            return

        if claim_type == "domestic":
            warranty = policy.get("security_warranty")
            text = (narrative_text or "").lower()
            theft_related = any(k in text for k in _THEFT_KEYWORDS) or incident_details.get("loss_type") == "theft"
            if not warranty or not theft_related:
                result.skipped.append(rule_id)
                return
            alarm_status = incident_details.get("alarm_armed")
            if alarm_status is None or str(alarm_status).lower() in ("unknown", "cannot determine", "unresolved"):
                result.findings.append(RuleFinding(
                    rule_id, "medium",
                    f"This policy requires \"{warranty}\", but whether it was complied with at the time of "
                    f"loss cannot currently be confirmed -- request the outstanding evidence (e.g. alarm "
                    f"monitoring log) before relying on this cover.",
                    60,
                ))
            else:
                result.skipped.append(rule_id)
        elif claim_type == "marine_hull":
            survey_valid_to = _parse_date(policy.get("survey_valid_to"))
            accident_dt = _parse_date(incident_details.get("incident_datetime"))
            if not survey_valid_to or not accident_dt:
                result.skipped.append(rule_id)
                return
            if survey_valid_to < accident_dt:
                result.findings.append(RuleFinding(
                    rule_id, "high",
                    f"The vessel's survey expired on {survey_valid_to.strftime('%Y-%m-%d')}, before this "
                    f"incident -- flag the date and refer; this does not by itself decide seaworthiness.",
                    75,
                ))
            else:
                result.skipped.append(rule_id)
        else:
            result.skipped.append(rule_id)

    def _rule_recent_cover_change(self, result, claim_type, policy, accident_dt):
        """BR-CHG-007: a material cover/value change shortly before the
        loss (e.g. Contents limit raised days before a fire) is a
        validation referral -- distinct from the motor-only
        cover_upgrade_before_incident above, which reads a different
        column (cover_upgrade_date) not populated for Domestic policies."""
        rule_id = "BR-CHG-007"
        if claim_type != "domestic" or not policy or not accident_dt:
            result.skipped.append(rule_id)
            return
        changed_at = _parse_date(policy.get("contents_limit_changed_at"))
        if not changed_at:
            result.skipped.append(rule_id)
            return
        days_between = (accident_dt.date() - changed_at.date()).days
        window = self.config["recent_change_days"]
        if 0 <= days_between <= window:
            result.findings.append(RuleFinding(
                rule_id, "medium",
                f"The Contents limit was increased on {changed_at.strftime('%Y-%m-%d')}, just "
                f"{days_between} day(s) before this loss -- refer for cover-change validation before "
                f"relying on the increased limit.",
                65,
            ))
        else:
            result.skipped.append(rule_id)

    def _rule_transit_custody_window(self, result, claim_type, policy, accident_dt):
        """BR-TRN-009: a Cargo loss must fall within the insured transit's
        attachment/termination window. incident_details carries the
        transit dates (not yet a dedicated schema field -- same "accept it
        as an optional field until intake grows one" pattern as
        driver_eligibility's driver_name above)."""
        rule_id = "BR-TRN-009"
        if claim_type != "marine_cargo" or not accident_dt:
            result.skipped.append(rule_id)
            return
        # incident_details isn't in scope here by signature -- read off the
        # policy dict instead, where callers can stash it the same way
        # cover_upgrade_date is read; if absent, skip rather than guess.
        attachment = _parse_date(policy.get("transit_attachment_date")) if policy else None
        termination = _parse_date(policy.get("transit_termination_date")) if policy else None
        if not attachment or not termination:
            result.skipped.append(rule_id)
            return
        if accident_dt < attachment or accident_dt > termination:
            result.findings.append(RuleFinding(
                rule_id, "high",
                f"The reported loss date ({accident_dt.strftime('%Y-%m-%d')}) falls outside the insured "
                f"transit window ({attachment.strftime('%Y-%m-%d')} to {termination.strftime('%Y-%m-%d')}) -- "
                f"refer to establish when and where the damage actually occurred.",
                70,
            ))
        else:
            result.skipped.append(rule_id)

    def _rule_quantity_reconciliation(self, result, claim_type, claim_id):
        """BR-QTY-010: compares quantities OCR'd from different documents on
        the same claim (invoice/packing list vs delivery note/tally) --
        the rule M01's cargo-shortage demo hinges on. Reads whatever
        numeric quantity field each document's parsed_fields happens to
        carry (document_ocr.py's schemas for these document types are a
        later phase; this rule is written to work once they exist, and
        harmlessly skips until then)."""
        rule_id = "BR-QTY-010"
        if claim_type not in ("marine_cargo", "goods_in_transit") or not claim_id:
            result.skipped.append(rule_id)
            return
        try:
            documents = self.db.get_documents_by_claim(claim_id) or []
        except Exception as e:
            logger.warning(f"quantity_reconciliation lookup failed: {e}")
            result.skipped.append(rule_id)
            return

        quantities = {}
        for doc in documents:
            fields = doc.get("parsed_fields")
            if isinstance(fields, str):
                try:
                    fields = json.loads(fields) if fields else {}
                except json.JSONDecodeError:
                    fields = {}
            fields = fields or {}
            qty = fields.get("quantity") or fields.get("cartons") or fields.get("units_delivered")
            if qty is not None:
                try:
                    quantities[doc.get("document_type", "document")] = float(qty)
                except (TypeError, ValueError):
                    continue

        if len(quantities) < 2:
            result.skipped.append(rule_id)
            return

        documented = max(quantities.values())
        delivered = min(quantities.values())
        if documented <= 0:
            result.skipped.append(rule_id)
            return
        variance = (documented - delivered) / documented
        if variance > self.config["quantity_variance_tolerance_pct"]:
            result.findings.append(RuleFinding(
                rule_id, "high",
                f"Documented quantity is {documented:.0f} but only {delivered:.0f} is accounted for on "
                f"delivery -- a {variance:.0%} shortfall. Refer to locate where custody or count changed; "
                f"this is not itself a finding of theft.",
                75,
            ))
        else:
            result.skipped.append(rule_id)

    def _rule_approved_transport_party(self, result, claim_type, policy, incident_details):
        """BR-GIT-011: the vehicle/driver/transporter actually involved must
        be one the policy approved."""
        rule_id = "BR-GIT-011"
        if claim_type != "goods_in_transit" or not policy:
            result.skipped.append(rule_id)
            return
        mismatches = []
        for field, label in (
            ("vehicle_reg", "approved_vehicle_reg"),
            ("driver_name", "approved_driver_name"),
            ("transporter_name", "approved_transporter_name"),
        ):
            claimed = incident_details.get(field)
            approved = policy.get(label)
            if claimed and approved and claimed.strip().lower() != approved.strip().lower():
                mismatches.append(f"{field.replace('_', ' ')} '{claimed}' (approved: '{approved}')")
        if mismatches:
            result.findings.append(RuleFinding(
                rule_id, "high",
                "This claim involves " + "; ".join(mismatches) +
                " -- not matching the policy's approved party. Refer for a transit-party check before proceeding.",
                70,
            ))
        else:
            result.skipped.append(rule_id)
