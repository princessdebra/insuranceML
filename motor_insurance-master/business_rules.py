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
        if policy:
            policy = dict(policy)
            try:
                with self.db.get_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute(
                        "SELECT vehicle_make, vehicle_model, vehicle_year, class_of_use "
                        "FROM motor_policy_details WHERE policy_id = ?",
                        (policy_id,),
                    )
                    row = cursor.fetchone()
                    if row:
                        policy.update(dict(row))
            except Exception as e:
                logger.warning(f"Could not enrich policy {policy_id} with motor details: {e}")
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
        self._rule_driver_eligibility(result, incident_details)
        self._rule_shared_identity(result, member_id, claim_id, member)
        self._rule_repeat_repair_shop(result, incident_details)
        self._rule_cost_escalation(result, incident_details)
        self._rule_unreported_theft(result, narrative_text, estimated_cost, policy, incident_details)
        self._rule_usage_class_mismatch(result, narrative_text, policy)
        self._rule_off_hours_filing(result, created_dt)

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
        if 0 <= hours_since_start < 24:
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
        if lapse_days > 7:
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
                cursor.execute(
                    """
                    SELECT COUNT(*) as cnt FROM claims
                    WHERE member_id = ? AND claim_id != ?
                    AND created_at >= datetime('now', '-365 days')
                    """,
                    (member_id, claim_id),
                )
                count = cursor.fetchone()["cnt"] + 1  # +1 to include this claim
        except Exception as e:
            logger.warning(f"repeat_claimant_12mo lookup failed: {e}")
            result.skipped.append(rule_id)
            return
        if count >= 3:
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
        if days_to_report > 30:
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
        if 0.65 <= ratio <= 0.69:
            result.findings.append(RuleFinding(
                rule_id, "medium",
                f"The repair estimate is {ratio:.0%} of the sum insured -- close to the point where it "
                "would be cheaper to write the car off. This gets a specialist look before repairs are approved.",
                65,
            ))
        elif ratio >= 0.70:
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
        if photo_count < 2:
            missing.append(f"only {photo_count} photo(s) uploaded (recommended minimum: 2)")
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
        if deviation > 0.30:
            direction = "above" if sum_insured > market_value else "below"
            result.findings.append(RuleFinding(
                rule_id, "medium",
                f"The insured value (KES {sum_insured:,.0f}) is {deviation:.0%} {direction} the typical "
                f"market value for this make and model (~KES {market_value:,.0f}) -- "
                "over-insurance is a known way to profit from a total loss.",
                50,
            ))

    def _rule_driver_eligibility(self, result, incident_details):
        rule_id = "driver_eligibility"
        age = incident_details.get("driver_age")
        licence_class = incident_details.get("driver_licence_class")
        min_age = incident_details.get("policy_min_driver_age", 18)
        permitted_classes = incident_details.get("policy_permitted_licence_classes")
        if age is None and not licence_class:
            result.skipped.append(rule_id)
            return
        if age is not None and age < min_age:
            result.findings.append(RuleFinding(
                rule_id, "high",
                f"The driver at the time was {age} years old, below this policy's minimum driver age of {min_age}.",
                85,
            ))
        if licence_class and permitted_classes and licence_class not in permitted_classes:
            result.findings.append(RuleFinding(
                rule_id, "high",
                f"The driver's licence class ({licence_class}) doesn't match what this policy permits "
                f"({', '.join(permitted_classes)}).",
                80,
            ))

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

    def _rule_repeat_repair_shop(self, result, incident_details):
        rule_id = "repeat_repair_shop"
        shop_id = incident_details.get("repair_shop_id")
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

    def _rule_cost_escalation(self, result, incident_details):
        rule_id = "cost_escalation"
        initial = incident_details.get("initial_estimate")
        final = incident_details.get("final_estimate")
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
