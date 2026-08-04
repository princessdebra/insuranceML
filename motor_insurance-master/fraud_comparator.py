import math
import logging
from dataclasses import dataclass, field, asdict
from typing import Optional

logger = logging.getLogger(__name__)


# ── THRESHOLDS ────────────────────────────────────────────────────────────────

# Speed delta above which we flag — in km/h
SPEED_DELTA_LOW      = 15.0   # Minor discrepancy — warn
SPEED_DELTA_MEDIUM   = 30.0   # Moderate — flag
SPEED_DELTA_HIGH     = 50.0   # Significant — strong fraud signal
SPEED_DELTA_CRITICAL = 80.0   # Extreme — near-certain misrepresentation

# Crush depth delta above which we flag — in mm
CRUSH_DELTA_LOW      = 50.0
CRUSH_DELTA_MEDIUM   = 150.0
CRUSH_DELTA_HIGH     = 400.0
CRUSH_DELTA_CRITICAL = 800.0

# Airbag deployment: minimum speed (km/h) at which frontal airbags typically deploy
AIRBAG_DEPLOY_MIN_KMH = 25.0   # Below this, frontal airbags should NOT deploy

# Score weights within the comparator
W_SPEED       = 0.45
W_CRUSH       = 0.30
W_AIRBAG      = 0.15
W_POST_CRASH  = 0.10


# ── DATA STRUCTURES ───────────────────────────────────────────────────────────

@dataclass
class Inconsistency:
    type: str
    severity: str          # info / low / medium / high / critical
    description: str
    confidence: float      # 0.0 – 1.0
    penalty_applied: float # score points penalised for this finding
    party: str = "member"


@dataclass
class FraudComparisonResult:
    # Overall
    comparator_score: float        # 0–100, higher = more fraudulent
    verdict: str                   # CONSISTENT / SUSPICIOUS / INCONSISTENT / CRITICAL
    verdict_reason: str

    # Component scores (0–100 each)
    speed_score: float
    crush_score: float
    airbag_score: float
    post_crash_score: float

    # Stated vs derived values
    stated_speed_v1_kmh: Optional[float]
    physics_speed_v1_kmh: Optional[float]
    speed_delta_kmh: Optional[float]

    stated_crush_depth_mm: Optional[float]
    physics_crush_depth_mm: Optional[float]
    crush_delta_mm: Optional[float]

    # Inconsistencies
    inconsistencies: list[Inconsistency] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    # Confidence in the comparison itself (degrades when values are inferred)
    comparison_confidence: float = 0.7

    def to_dict(self) -> dict:
        d = asdict(self)
        return d


# ── COMPARATOR ────────────────────────────────────────────────────────────────

class FraudComparator:

    def compare(
        self,
        stated_values: dict,
        simulation_output: dict,
        vehicle_specs: Optional[list[dict]] = None,
    ) -> FraudComparisonResult:
        """
        Main entry point.

        stated_values keys (all optional — comparator degrades gracefully):
            stated_speed_v1_kmh       : float  — V1 speed from narrative
            stated_speed_v2_kmh       : float  — V2 speed from narrative
            stated_crush_depth_mm     : float  — damage depth from narrative/photos
            airbags_deployed          : bool   — claimant stated airbags fired
            claimant_ejected          : bool   — claimant stated they were ejected
            post_crash_v1_movement_m  : float  — stated vehicle slide/roll distance

        simulation_output keys (from ConfigurableMultiVehicleEngine):
            v1_insured_telemetry      : list[dict]  — telemetry frames
            v2_third_party_telemetry  : list[dict]
            (plus metadata fields)
        """
        inconsistencies: list[Inconsistency] = []
        warnings: list[str] = []

        v1_tel = simulation_output.get("v1_insured_telemetry", [])
        v2_tel = simulation_output.get("v2_third_party_telemetry", [])

        physics_v1_pre  = self._get_pre_impact_speed(v1_tel)
        physics_v1_post = self._get_post_impact_speed(v1_tel)

        # ── 1. SPEED COMPARISON ───────────────────────────────────────────

        stated_v1 = stated_values.get("stated_speed_v1_kmh")
        speed_score, speed_confidence = 0.0, 0.5

        if stated_v1 is not None and physics_v1_pre is not None:
            delta = abs(stated_v1 - physics_v1_pre)
            speed_score, inc = self._score_speed_delta(stated_v1, physics_v1_pre, delta)
            if inc:
                inconsistencies.append(inc)
            speed_confidence = 0.75
        else:
            warnings.append(
                "Speed comparison skipped — "
                f"stated={'missing' if stated_v1 is None else stated_v1} | "
                f"physics={'missing' if physics_v1_pre is None else physics_v1_pre}"
            )
            speed_score = 20.0  # mild baseline penalty for missing stated speed

        speed_delta = (
            abs(stated_v1 - physics_v1_pre)
            if stated_v1 is not None and physics_v1_pre is not None
            else None
        )

        # ── 2. CRUSH DEPTH COMPARISON ─────────────────────────────────────

        stated_crush = stated_values.get("stated_crush_depth_mm")
        physics_crush = self._derive_expected_crush(simulation_output, vehicle_specs)
        crush_score = 0.0
        crush_confidence = 0.5

        if stated_crush is not None and physics_crush is not None:
            crush_delta = abs(stated_crush - physics_crush)
            crush_score, inc = self._score_crush_delta(stated_crush, physics_crush, crush_delta)
            if inc:
                inconsistencies.append(inc)
            crush_confidence = 0.65
        else:
            warnings.append(
                "Crush depth comparison skipped — "
                f"stated={'missing' if stated_crush is None else stated_crush}mm | "
                f"physics={'missing' if physics_crush is None else round(physics_crush,1)}mm"
            )
            crush_score = 10.0

        crush_delta_mm = (
            abs(stated_crush - physics_crush)
            if stated_crush is not None and physics_crush is not None
            else None
        )

        # ── 3. AIRBAG CONSISTENCY CHECK ───────────────────────────────────

        airbags_deployed = stated_values.get("airbags_deployed", False)
        airbag_score, airbag_inc = self._check_airbag_consistency(
            airbags_deployed, physics_v1_pre, vehicle_specs
        )
        if airbag_inc:
            inconsistencies.append(airbag_inc)

        # ── 4. POST-CRASH MOVEMENT CHECK ──────────────────────────────────

        stated_slide = stated_values.get("post_crash_v1_movement_m")
        post_score, post_inc = self._check_post_crash_movement(
            stated_slide, physics_v1_post
        )
        if post_inc:
            inconsistencies.append(post_inc)

        # ── WEIGHTED FINAL SCORE ──────────────────────────────────────────

        raw = (
            speed_score  * W_SPEED +
            crush_score  * W_CRUSH +
            airbag_score * W_AIRBAG +
            post_score   * W_POST_CRASH
        )
        final_score = min(round(raw, 1), 100.0)

        # ── VERDICT ───────────────────────────────────────────────────────

        verdict, reason = self._determine_verdict(final_score, inconsistencies)

        # ── COMPARISON CONFIDENCE ─────────────────────────────────────────

        comparison_confidence = round(
            (speed_confidence + crush_confidence) / 2, 2
        )

        return FraudComparisonResult(
            comparator_score=final_score,
            verdict=verdict,
            verdict_reason=reason,
            speed_score=round(speed_score, 1),
            crush_score=round(crush_score, 1),
            airbag_score=round(airbag_score, 1),
            post_crash_score=round(post_score, 1),
            stated_speed_v1_kmh=stated_v1,
            physics_speed_v1_kmh=round(physics_v1_pre, 1) if physics_v1_pre else None,
            speed_delta_kmh=round(speed_delta, 1) if speed_delta else None,
            stated_crush_depth_mm=stated_crush,
            physics_crush_depth_mm=round(physics_crush, 1) if physics_crush else None,
            crush_delta_mm=round(crush_delta_mm, 1) if crush_delta_mm else None,
            inconsistencies=inconsistencies,
            warnings=warnings,
            comparison_confidence=comparison_confidence,
        )

    # ── INTERNAL SCORERS ──────────────────────────────────────────────────────

    def _score_speed_delta(
        self, stated: float, physics: float, delta: float
    ) -> tuple[float, Optional[Inconsistency]]:

        if delta <= SPEED_DELTA_LOW:
            return 5.0, None

        if delta <= SPEED_DELTA_MEDIUM:
            score = 30.0
            severity, conf = "low", 0.60
        elif delta <= SPEED_DELTA_HIGH:
            score = 55.0
            severity, conf = "medium", 0.70
        elif delta <= SPEED_DELTA_CRITICAL:
            score = 75.0
            severity, conf = "high", 0.80
        else:
            score = 95.0
            severity, conf = "critical", 0.90

        inc = Inconsistency(
            type="velocity_inconsistency",
            severity=severity,
            description=(
                f"Stated speed ({stated:.1f} km/h) differs from physics-derived speed "
                f"({physics:.1f} km/h) by {delta:.1f} km/h. "
                f"{'Extreme discrepancy — strong misrepresentation signal.' if delta > SPEED_DELTA_CRITICAL else ''}"
            ),
            confidence=conf,
            penalty_applied=score,
        )
        return score, inc

    def _score_crush_delta(
        self, stated: float, physics: float, delta: float
    ) -> tuple[float, Optional[Inconsistency]]:

        if delta <= CRUSH_DELTA_LOW:
            return 5.0, None

        if delta <= CRUSH_DELTA_MEDIUM:
            score, severity, conf = 25.0, "low", 0.55
        elif delta <= CRUSH_DELTA_HIGH:
            score, severity, conf = 50.0, "medium", 0.65
        elif delta <= CRUSH_DELTA_CRITICAL:
            score, severity, conf = 70.0, "high", 0.75
        else:
            score, severity, conf = 90.0, "critical", 0.85

        direction = "over-stated" if stated > physics else "under-stated"

        inc = Inconsistency(
            type="crush_depth_mismatch",
            severity=severity,
            description=(
                f"Stated damage depth ({stated:.0f}mm) vs physics-expected ({physics:.0f}mm) — "
                f"delta {delta:.0f}mm. Damage appears {direction}."
            ),
            confidence=conf,
            penalty_applied=score,
        )
        return score, inc

    def _check_airbag_consistency(
        self,
        deployed: bool,
        physics_speed_kmh: Optional[float],
        vehicle_specs: Optional[list[dict]],
    ) -> tuple[float, Optional[Inconsistency]]:

        if not deployed or physics_speed_kmh is None:
            return 0.0, None

        # Check if V1 spec has airbags at all
        has_airbags = True
        if vehicle_specs:
            v1_spec = vehicle_specs[0] if vehicle_specs else {}
            has_airbags = v1_spec.get("airbag_count", 2) > 0

        if not has_airbags:
            inc = Inconsistency(
                type="airbag_deployment_impossible",
                severity="high",
                description=(
                    "Claimant stated airbags deployed, but vehicle registry shows "
                    "airbag_count = 0 for this vehicle model."
                ),
                confidence=0.85,
                penalty_applied=70.0,
            )
            return 70.0, inc

        if physics_speed_kmh < AIRBAG_DEPLOY_MIN_KMH:
            inc = Inconsistency(
                type="airbag_deployment_inconsistent",
                severity="medium",
                description=(
                    f"Airbag deployment claimed, but physics-derived impact speed is "
                    f"{physics_speed_kmh:.1f} km/h — below the typical frontal deployment "
                    f"threshold of {AIRBAG_DEPLOY_MIN_KMH} km/h."
                ),
                confidence=0.70,
                penalty_applied=45.0,
            )
            return 45.0, inc

        return 0.0, None

    def _check_post_crash_movement(
        self,
        stated_slide_m: Optional[float],
        physics_post_speed_kmh: Optional[float],
    ) -> tuple[float, Optional[Inconsistency]]:

        if stated_slide_m is None or physics_post_speed_kmh is None:
            return 0.0, None

        # Rough physics estimate: slide distance from post-impact speed
        # using kinetic friction (mu=0.45) and assuming 2 seconds of deceleration
        mu = 0.45
        g = 9.81
        post_mps = physics_post_speed_kmh / 3.6
        estimated_slide = (post_mps ** 2) / (2 * mu * g)

        delta = abs(stated_slide_m - estimated_slide)

        if delta < 5.0:
            return 0.0, None

        score = min(delta * 2.5, 60.0)
        inc = Inconsistency(
            type="post_crash_movement_mismatch",
            severity="medium" if delta < 15 else "high",
            description=(
                f"Stated post-crash slide ({stated_slide_m:.1f}m) vs physics estimate "
                f"({estimated_slide:.1f}m) — delta {delta:.1f}m."
            ),
            confidence=0.55,
            penalty_applied=round(score, 1),
        )
        return score, inc

    # ── HELPERS ───────────────────────────────────────────────────────────────

    def _get_pre_impact_speed(self, telemetry: list[dict]) -> Optional[float]:
        """Last speed before t=0 in telemetry."""
        pre = [f for f in telemetry if f.get("time_sec", 0) < 0]
        if not pre:
            return None
        return pre[-1].get("velocity_kmh")

    def _get_post_impact_speed(self, telemetry: list[dict]) -> Optional[float]:
        """First speed at t > 0 in telemetry."""
        post = [f for f in telemetry if f.get("time_sec", 0) > 0]
        if not post:
            return None
        return post[0].get("velocity_kmh")

    def _derive_expected_crush(
        self,
        simulation_output: dict,
        vehicle_specs: Optional[list[dict]],
    ) -> Optional[float]:
        """
        Back-calculate expected crush depth from physics-derived delta-V and
        vehicle stiffness coefficients (McHenry).
        C = -A/B + sqrt((A/B)^2 + 2*E / (B * width))  [in meters, converted to mm]
        """
        if not vehicle_specs:
            return None

        v1_spec = vehicle_specs[0]
        A = v1_spec.get("crumple_A")
        B = v1_spec.get("crumple_B")
        mass = v1_spec.get("kenyan_operational_mass_kg")
        width = (v1_spec.get("width_mm", 1700) / 1000) * 0.7  # ~70% of width as impact width

        if not all([A, B, mass, width]):
            return None

        v1_tel = simulation_output.get("v1_insured_telemetry", [])
        pre  = self._get_pre_impact_speed(v1_tel)
        post = self._get_post_impact_speed(v1_tel)

        if pre is None or post is None:
            return None

        delta_v_mps = abs(pre - post) / 3.6
        energy = 0.5 * mass * (delta_v_mps ** 2)

        # McHenry inverse: solve C from E = (width/2) * (A*C + B*C^2)
        # B*C^2 + A*C - 2E/width = 0
        a_coef = B
        b_coef = A
        c_coef = -(2 * energy / width)

        discriminant = b_coef ** 2 - 4 * a_coef * c_coef
        if discriminant < 0:
            return None

        crush_m = (-b_coef + math.sqrt(discriminant)) / (2 * a_coef)
        return max(crush_m * 1000, 0.0)  # convert to mm, floor at 0

    def _determine_verdict(
        self, score: float, inconsistencies: list[Inconsistency]
    ) -> tuple[str, str]:

        critical_count = sum(1 for i in inconsistencies if i.severity == "critical")
        high_count     = sum(1 for i in inconsistencies if i.severity == "high")

        if score >= 80 or critical_count >= 1:
            return "CRITICAL", "Multiple critical physics inconsistencies — immediate SIU referral required."
        if score >= 60 or high_count >= 2:
            return "INCONSISTENT", "Significant discrepancies between stated claim and physics reconstruction."
        if score >= 35:
            return "SUSPICIOUS", "Moderate inconsistencies detected — enhanced verification recommended."
        return "CONSISTENT", "Stated values broadly align with physics reconstruction."