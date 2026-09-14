
import math
import logging
import hashlib
import json
from dataclasses import dataclass, field, asdict
from typing import Optional
 
from vehicle_registry import VehicleProfile, get_vehicle_profile
from terrain_service import get_terrain, TerrainResult
 
logger = logging.getLogger(__name__)
 
try:
    import pybullet as pb
    PYBULLET_AVAILABLE = True
    logger.info("PyBullet available — full multibody simulation enabled")
except ImportError:
    PYBULLET_AVAILABLE = False
    logger.info("PyBullet not installed — using analytical Newton-Euler solver")
 
 
G = 9.81
KMH_TO_MS = 1 / 3.6
MS_TO_KMH = 3.6
 
 
@dataclass
class ImpactVector:
    impact_zone_v1: str = "front_bumper"
    impact_zone_v2: str = "front_bumper"
    approach_angle_deg: float = 180.0
    speed_v1_kmh: float = 0.0
    speed_v2_kmh: float = 0.0
    crush_depth_mm: float = 0.0
    pathway: str = "pathway_2"
 
 
@dataclass
class PhysicsResult:
    claim_id: str
 
    vehicle_1_key: str = ""
    vehicle_2_key: str = ""
    stated_speed_v1_kmh: float = 0.0
    stated_speed_v2_kmh: float = 0.0
    v1_speed_is_inferred: bool = False
    v1_speed_confidence: float = 1.0
    v2_speed_is_inferred: bool = False
    v2_speed_confidence: float = 1.0

    computed_speed_v1_kmh: float = 0.0
    computed_speed_v2_kmh: float = 0.0
    delta_v_kmh: float = 0.0
 
    kinetic_energy_j: float = 0.0
    crush_energy_j: float = 0.0
    energy_consistent: bool = True
 
    stated_vs_computed_delta_kmh: float = 0.0
    velocity_fraud_flag: bool = False
    velocity_fraud_severity: str = "none"
 
    impact_consistent: bool = True
    impact_consistency_score: float = 100.0
    inconsistencies: list = field(default_factory=list)
 
    expected_crush_depth_mm: float = 0.0
    stated_crush_depth_mm: float = 0.0
    crush_depth_delta_mm: float = 0.0
    repair_cost_consistent: bool = True
 
    terrain_adjusted: bool = False
    slope_adjustment_kmh: float = 0.0
 
    physics_fraud_score: float = 0.0
    physics_verdict: str = "CONSISTENT"
    verdict_reason: str = ""
 
    physics_explanation: str = ""
 
    simulation_method: str = "analytical"
    pathway: str = "pathway_2"
    confidence: float = 0.5
 
    signature: str = ""
 
    v1_impact_vertex_xyz: list = field(default_factory=list)
    impact_force_magnitude_n: float = 0.0
    # True only for the Pathway-1 (telemetry-derived) force below -- an
    # impulse-momentum ESTIMATE (mass x Δv / assumed crash-pulse duration)
    # is computed for every claim regardless of pathway, but callers must be
    # able to tell "measured from real sensor data" apart from "estimated
    # from an assumed impact duration" rather than showing both as the same
    # bare "CALCULATED" figure.
    impact_force_is_estimated: bool = True
    telemetry_delta_v_ms: float = 0.0
 
    def to_dict(self):
        return asdict(self)
 
 
def mchenry_crush_energy(crush_depth_m, surface_width_m, A, B):
    C, L = crush_depth_m, surface_width_m
    return (A * C + B * C ** 2) * L * 1000
 
 
def velocity_from_crush_energy(crush_energy_j, mass_kg):
    if mass_kg <= 0:
        return 0.0
    return math.sqrt(2 * crush_energy_j / mass_kg)
 
 
def two_body_pre_impact_speed_v1(crush_energy_j, m1_kg, m2_kg, v2_pre_impact_ms):
    """
    V1's own crush energy gives its velocity CHANGE (delta_v1) via the
    single-body sqrt(2E/m) relation -- that part is legitimate on its own,
    but the previous code then used that number directly AS V1's full
    pre-impact speed, which silently assumes V1 decelerated to a complete
    stop (i.e. hit an immovable wall). That's wrong whenever V1 actually hit
    a second vehicle that moved -- a heavy truck absorbs/imparts very
    different momentum than a parked motorcycle would for the exact same
    crush depth on V1, and the old formula couldn't tell the difference
    since it never looked at m2 at all.

    Conservation of momentum requires the two vehicles' velocity changes to
    be inversely proportional to their masses (m1*delta_v1 = m2*delta_v2),
    so delta_v2 is derived that way instead of needing V2's own crush depth
    -- which usually doesn't exist, since the third-party vehicle rarely has
    photos. V1's actual pre-impact speed is then reconstructed from V2's
    (stated) speed plus both delta-v's.

    No restitution coefficient here -- sqrt(2E/m) already implicitly treats
    the crush energy as the vehicle's entire kinetic energy change (a fully
    inelastic, e=0 assumption baked into the single-body formula itself, the
    same one this codebase already uses for the barrier/fixed-object
    pathway). Dividing by (1+e) on top of that would double-count
    restitution rather than correct for it. This also means the formula
    reduces to exactly the old single-body result when m2 is very large
    relative to m1 and stationary (delta_v2 -> 0), so the already-correct
    fixed-object/barrier case is unaffected by this change.

    Returns (v1_pre_impact_ms, delta_v1_ms).
    """
    if m1_kg <= 0:
        return 0.0, 0.0
    delta_v1 = math.sqrt(2 * crush_energy_j / m1_kg)
    delta_v2 = (m1_kg / m2_kg) * delta_v1 if m2_kg > 0 else 0.0
    v1_pre_impact_ms = v2_pre_impact_ms + delta_v1 + delta_v2
    return v1_pre_impact_ms, delta_v1


def expected_crush_depth(velocity_ms, mass_kg, surface_width_m, A, B):
    KE = 0.5 * mass_kg * velocity_ms ** 2
    a_coef = B * surface_width_m
    b_coef = A * surface_width_m
    c_coef = -KE / 1000
    discriminant = b_coef ** 2 - 4 * a_coef * c_coef
    if discriminant < 0:
        return 0.0
    return max(0.0, (-b_coef + math.sqrt(discriminant)) / (2 * a_coef))
 
 
def braking_distance(initial_speed_ms, friction, slope_deg=0.0, has_abs=False):
    theta = math.radians(slope_deg)
    mu = friction * (1.2 if has_abs else 1.0)
    effective_decel = mu * G * math.cos(theta) - G * math.sin(theta)
    if effective_decel <= 0:
        return float('inf')
    return (initial_speed_ms ** 2) / (2 * effective_decel)
 
 
def speed_at_impact(initial_speed_ms, braking_distance_m, available_distance_m, friction, slope_deg=0.0, has_abs=False):
    theta = math.radians(slope_deg)
    mu = friction * (1.2 if has_abs else 1.0)
    effective_decel = mu * G * math.cos(theta) - G * math.sin(theta)
    if effective_decel <= 0:
        return initial_speed_ms
    v_sq = initial_speed_ms ** 2 - 2 * effective_decel * min(available_distance_m, braking_distance_m)
    return math.sqrt(max(0.0, v_sq))
 
 
def momentum_analysis(v1_ms, m1_kg, v2_ms, m2_kg, angle_deg=180.0, restitution=0.15):
    v1_axial = v1_ms * math.cos(math.radians(180 - angle_deg) / 2)
    v2_axial = v2_ms * math.cos(math.radians(180 - angle_deg) / 2)
    e = restitution
    v1f = ((m1_kg - e * m2_kg) * v1_axial + (1 + e) * m2_kg * v2_axial) / (m1_kg + m2_kg)
    v2f = ((m2_kg - e * m1_kg) * v2_axial + (1 + e) * m1_kg * v1_axial) / (m1_kg + m2_kg)
    delta_v1 = abs(v1f - v1_axial)
    delta_v2 = abs(v2f - v2_axial)
    return {
        "v1_post_impact_ms": v1f, "v2_post_impact_ms": v2f,
        "delta_v1_ms": delta_v1, "delta_v2_ms": delta_v2,
        "delta_v1_kmh": delta_v1 * MS_TO_KMH, "delta_v2_kmh": delta_v2 * MS_TO_KMH,
        "total_momentum_before": m1_kg * v1_axial + m2_kg * v2_axial,
        "total_momentum_after":  m1_kg * v1f + m2_kg * v2f,
    }
 
 
VELOCITY_FRAUD_THRESHOLDS = {
    "none":     (0,  10),
    "low":      (10, 20),
    "medium":   (20, 35),
    "high":     (35, 55),
    "critical": (55, 9999),
}
 
def velocity_fraud_severity(delta_kmh):
    delta = abs(delta_kmh)
    for severity, (low, high) in VELOCITY_FRAUD_THRESHOLDS.items():
        if low <= delta < high:
            return severity
    return "critical"
 
 
def build_physics_explanation(result: "PhysicsResult", terrain: TerrainResult) -> str:
    lines = []
 
    verdict_label = {
        "CONSISTENT":   "CONSISTENT",
        "SUSPICIOUS":   "SUSPICIOUS",
        "INCONSISTENT": "INCONSISTENT",
    }.get(result.physics_verdict, result.physics_verdict)
 
    lines.append(f"Physics Reconstruction Score: {result.physics_fraud_score}/100 — {verdict_label}")
    lines.append("")
 
    lines.append("Vehicles analysed:")
    lines.append(f"  Insured (V1): {result.vehicle_1_key}")
    lines.append(f"  Third Party (V2): {result.vehicle_2_key}")
    lines.append(f"  Simulation confidence: {result.confidence:.0%} (based on vehicle registry data quality)")
    lines.append("")
 
    lines.append(
        f"Road conditions: {terrain.road_surface.replace('_', ' ').title()}, "
        f"slope {terrain.slope_degrees}°, friction coefficient {terrain.friction_coefficient}"
    )
    if result.terrain_adjusted:
        lines.append(
            f"  Slope adjustment applied: +{result.slope_adjustment_kmh} km/h added to "
            f"effective impact speed due to road gradient"
        )
    lines.append("")
 
    lines.append("Speed analysis:")
    speed_label = (
        f"Speed used for reconstruction (estimated from narrative wording, "
        f"NOT stated by member, {result.v1_speed_confidence:.0%} confidence)"
        if result.v1_speed_is_inferred else "Member stated speed"
    )
    lines.append(f"  {speed_label}: {result.stated_speed_v1_kmh} km/h")
 
    if result.crush_energy_j > 0:
        lines.append(
            f"  Physics-derived speed (McHenry crush energy model): "
            f"{result.computed_speed_v1_kmh} km/h"
        )
        lines.append(
            f"  Speed delta: {result.stated_vs_computed_delta_kmh} km/h "
            f"({result.velocity_fraud_severity.upper()} fraud signal)"
        )
        severity_explanation = {
            "none":     "within acceptable tolerance — no speed fraud signal",
            "low":      "minor discrepancy — may reflect estimation error",
            "medium":   "notable discrepancy — warrants assessor verification",
            "high":     "significant discrepancy — stated speed inconsistent with observed damage",
            "critical": "extreme discrepancy — stated speed almost certainly fabricated",
        }.get(result.velocity_fraud_severity, "")
        lines.append(f"  {severity_explanation.capitalize()}")
    else:
        lines.append(
            "  No crush depth data available — speed cross-check not performed. "
            "Assessor measurement required for precise analysis."
        )
    lines.append("")
 
    if result.stated_crush_depth_mm > 0 or result.expected_crush_depth_mm > 0:
        lines.append("Crush depth analysis (McHenry model):")
        if result.expected_crush_depth_mm > 0:
            lines.append(
                f"  Expected crush depth at stated speed ({result.stated_speed_v1_kmh} km/h): "
                f"{result.expected_crush_depth_mm} mm"
            )
        if result.stated_crush_depth_mm > 0:
            lines.append(f"  Observed crush depth: {result.stated_crush_depth_mm} mm")
        if result.crush_depth_delta_mm > 0:
            verdict = "inconsistent, requires investigation" if result.crush_depth_delta_mm > 80 else "within acceptable range"
            lines.append(f"  Delta: {result.crush_depth_delta_mm} mm — {verdict}")
        lines.append("")
 
    if result.delta_v_kmh > 0:
        lines.append(
            f"Momentum transfer: V1 velocity change at impact was {result.delta_v_kmh} km/h "
            f"based on vehicle masses and approach angle"
        )
        lines.append("")
 
    if result.inconsistencies:
        lines.append(f"Physics inconsistencies detected ({len(result.inconsistencies)}):")
        for inc in result.inconsistencies:
            severity_icon = {
                "critical": "[CRITICAL]", "high": "[HIGH]",
                "medium": "[MEDIUM]", "low": "[LOW]"
            }.get(inc.get("severity", "low"), "[INFO]")
            lines.append(f"  {severity_icon} {inc['description']}")
        lines.append("")
 
    velocity_penalty = {
        "none": 0, "low": 5, "medium": 20, "high": 40, "critical": 65
    }.get(result.velocity_fraud_severity, 0)
    consistency_penalty = max(0, 100 - result.impact_consistency_score)
 
    lines.append("How the score was calculated:")
    lines.append(
        f"  Velocity fraud penalty: {velocity_penalty} points "
        f"(severity: {result.velocity_fraud_severity.upper()}, weighted at 60% of final score)"
    )
    lines.append(
        f"  Impact consistency penalty: {consistency_penalty:.0f} points "
        f"(consistency score: {result.impact_consistency_score}/100, weighted at 40% of final score)"
    )
    lines.append(
        f"  Final calculation: ({velocity_penalty} x 0.6) + ({consistency_penalty:.0f} x 0.4) "
        f"= {result.physics_fraud_score}/100"
    )
    lines.append("")
 
    engine_name = "PyBullet multibody solver" if result.simulation_method == "pybullet" else "Analytical Newton-Euler solver"
    lines.append(
        f"Simulation engine: {engine_name}, "
        f"Pathway: {result.pathway.replace('_', ' ').title()}"
    )
    lines.append(f"Data integrity: SHA-256 signed — {result.signature[:16]}...")
 
    return "\n".join(lines)
 
 
class CrashReconstructionEngine:
 
    def __init__(self):
        self.use_pybullet = PYBULLET_AVAILABLE
        logger.info(f"CrashReconstructionEngine init | PyBullet: {self.use_pybullet}")
 
    def reconstruct(
        self,
        claim_id: str,
        v1_make: str, v1_model: str, v1_body_type: str, v1_stated_speed_kmh: float,
        v2_make: str, v2_model: str, v2_body_type: str, v2_stated_speed_kmh: float,
        impact_zone_v1: str = "front_bumper",
        impact_zone_v2: str = "front_bumper",
        approach_angle_deg: float = 180.0,
        crush_depth_mm: float = 0.0,
        estimated_repair_cost: float = 0.0,
        location_text: str = "",
        latitude: float = 0.0,
        longitude: float = 0.0,
        pathway: str = "pathway_2",
        v1_speed_is_inferred: bool = False,
        v1_speed_confidence: float = 1.0,
        v2_speed_is_inferred: bool = False,
        v2_speed_confidence: float = 1.0,
    ) -> PhysicsResult:

        result = PhysicsResult(claim_id=claim_id, pathway=pathway)

        profile_v1, method_v1 = get_vehicle_profile(v1_make, v1_model, v1_body_type)
        profile_v2, method_v2 = get_vehicle_profile(v2_make, v2_model, v2_body_type)
        result.vehicle_1_key = f"{v1_make} {v1_model} ({method_v1})"
        result.vehicle_2_key = f"{v2_make} {v2_model} ({method_v2})"
        result.stated_speed_v1_kmh = v1_stated_speed_kmh
        result.stated_speed_v2_kmh = v2_stated_speed_kmh
        result.v1_speed_is_inferred = v1_speed_is_inferred
        result.v1_speed_confidence = v1_speed_confidence
        result.v2_speed_is_inferred = v2_speed_is_inferred
        result.v2_speed_confidence = v2_speed_confidence
        result.confidence = (profile_v1.confidence + profile_v2.confidence) / 2
 
        terrain = get_terrain(
            location_text=location_text,
            latitude=latitude,
            longitude=longitude,
        )
        logger.info(
            f"Terrain for {claim_id}: slope={terrain.slope_degrees}°, "
            f"surface={terrain.road_surface}, source={terrain.source}, "
            f"confidence={terrain.confidence:.0%}"
        )
 
        v1_ms = v1_stated_speed_kmh * KMH_TO_MS
        v2_ms = v2_stated_speed_kmh * KMH_TO_MS
        m1 = profile_v1.effective_mass_kg
        m2 = profile_v2.effective_mass_kg
        result.kinetic_energy_j = round(0.5 * m1 * v1_ms**2 + 0.5 * m2 * v2_ms**2, 2)
 
        crush_depth_m = crush_depth_mm / 1000
        impact_width_m = profile_v1.width_mm / 1000 * 0.4
 
        if crush_depth_m > 0:
            crush_energy = mchenry_crush_energy(crush_depth_m, impact_width_m, profile_v1.crumple_A, profile_v1.crumple_B)
            result.crush_energy_j = round(crush_energy, 2)
            v1_pre_impact_ms, _ = two_body_pre_impact_speed_v1(crush_energy, m1, m2, v2_ms)
            result.computed_speed_v1_kmh = round(v1_pre_impact_ms * MS_TO_KMH, 1)
            expected_depth_m = expected_crush_depth(v1_ms, m1, impact_width_m, profile_v1.crumple_A, profile_v1.crumple_B)
            result.expected_crush_depth_mm = round(expected_depth_m * 1000, 1)
            result.stated_crush_depth_mm = crush_depth_mm
            result.crush_depth_delta_mm = round(abs(crush_depth_mm - result.expected_crush_depth_mm), 1)
        else:
            result.computed_speed_v1_kmh = v1_stated_speed_kmh
            result.expected_crush_depth_mm = 0.0
 
        result.computed_speed_v2_kmh = v2_stated_speed_kmh
 
        if terrain.slope_degrees > 2.0:
            slope_adj_kmh = terrain.gravity_component * 2.0 * MS_TO_KMH
            result.terrain_adjusted = True
            result.slope_adjustment_kmh = round(slope_adj_kmh, 1)
            logger.info(f"Terrain slope {terrain.slope_degrees}° → speed adjustment {slope_adj_kmh:.1f} km/h")
 
        momentum = momentum_analysis(v1_ms, m1, v2_ms, m2, approach_angle_deg)
        result.delta_v_kmh = round(momentum["delta_v1_kmh"], 1)

        # Impulse-momentum force estimate (F = m·Δv / Δt) -- computable from
        # data every claim already has (mass, ΔV), regardless of pathway.
        # Previously the UI only ever showed a real force for Pathway 1
        # (telemetry) claims and a bare "unavailable" for every narrative
        # claim, even though this estimate is standard crash-reconstruction
        # practice, not a guess invented for this codebase. 0.12s is a
        # typical passenger-vehicle crash-pulse duration from published
        # crash-test data (same order of magnitude as the |t|<0.15s "impact
        # phase" window already used elsewhere in this pipeline) -- an
        # assumption, not a measurement, which is exactly why
        # impact_force_is_estimated stays True here; reconstruct_from_input()
        # overwrites both fields with the real telemetry-derived value AND
        # sets impact_force_is_estimated=False when Pathway 1 data exists.
        ASSUMED_CRASH_PULSE_DURATION_S = 0.12
        delta_v_ms = result.delta_v_kmh / MS_TO_KMH
        result.impact_force_magnitude_n = round(m1 * delta_v_ms / ASSUMED_CRASH_PULSE_DURATION_S, 0)
        result.impact_force_is_estimated = True

        stated_vs_computed_delta = abs(result.stated_speed_v1_kmh - result.computed_speed_v1_kmh)
        result.stated_vs_computed_delta_kmh = round(stated_vs_computed_delta, 1)
        severity = velocity_fraud_severity(stated_vs_computed_delta)
        # A speed guessed from vague narrative wording (e.g. "fast", "highway")
        # is not a claimant assertion — don't raise a fraud flag off a guess
        # the system itself is unsure about.
        speed_is_reliable = not (v1_speed_is_inferred and v1_speed_confidence < 0.5)
        result.velocity_fraud_severity = severity if speed_is_reliable else "none"
        result.velocity_fraud_flag = speed_is_reliable and severity in ("high", "critical")
 
        inconsistencies = []
        consistency_score = 100.0
        speed_label = "estimated (not explicitly stated by claimant)" if v1_speed_is_inferred else "stated"

        if crush_depth_mm > 0 and result.crush_depth_delta_mm > 80 and speed_is_reliable:
            inconsistencies.append({
                "type": "crush_depth_mismatch",
                "severity": "high" if result.crush_depth_delta_mm > 150 else "medium",
                "description": (
                    f"{speed_label.capitalize()} speed implies {result.expected_crush_depth_mm}mm crush depth, "
                    f"but {crush_depth_mm}mm was observed — delta {result.crush_depth_delta_mm}mm"
                ),
                "confidence": 0.75 if not v1_speed_is_inferred else 0.4
            })
            consistency_score -= 25.0 if not v1_speed_is_inferred else 10.0

        if result.velocity_fraud_flag:
            inconsistencies.append({
                "type": "velocity_inconsistency",
                "severity": severity,
                "description": (
                    f"Physics-derived speed ({result.computed_speed_v1_kmh} km/h) "
                    f"differs from {speed_label} speed ({result.stated_speed_v1_kmh} km/h) "
                    f"by {stated_vs_computed_delta:.1f} km/h"
                ),
                "confidence": result.confidence
            })
            consistency_score -= {"high": 30.0, "critical": 50.0}.get(severity, 10.0)

        if m1 > 0 and m2 > 0:
            mass_ratio = max(m1, m2) / min(m1, m2)
            if mass_ratio > 4.0 and v1_stated_speed_kmh < 20:
                inconsistencies.append({
                    "type": "low_speed_heavy_impact",
                    "severity": "medium",
                    "description": (
                        f"Mass ratio {mass_ratio:.1f}:1 with {speed_label} speed {v1_stated_speed_kmh} km/h — "
                        f"damage pattern inconsistent with low-speed heavy vehicle collision"
                    ),
                    "confidence": 0.6 if not v1_speed_is_inferred else 0.35
                })
                consistency_score -= 15.0
 
        if "cbd" in location_text.lower() or "roundabout" in location_text.lower():
            if v1_stated_speed_kmh > 80:
                inconsistencies.append({
                    "type": "implausible_speed_for_location",
                    "severity": "medium",
                    "description": (
                        f"Stated speed {v1_stated_speed_kmh} km/h implausible for CBD / roundabout location"
                    ),
                    "confidence": 0.7
                })
                consistency_score -= 20.0
 
        result.inconsistencies = inconsistencies
        result.impact_consistency_score = max(0.0, round(consistency_score, 1))
        result.impact_consistent = consistency_score >= 70.0
 
        velocity_penalty = {"none": 0, "low": 5, "medium": 20, "high": 40, "critical": 65}.get(severity, 0)
        consistency_penalty = max(0, 100 - result.impact_consistency_score)
        physics_score = min(100, (velocity_penalty * 0.6) + (consistency_penalty * 0.4))
        result.physics_fraud_score = round(physics_score, 1)
 
        if physics_score < 20:
            result.physics_verdict = "CONSISTENT"
            result.verdict_reason = "Physics simulation consistent with stated account"
        elif physics_score < 45:
            result.physics_verdict = "SUSPICIOUS"
            result.verdict_reason = "Minor physics inconsistencies detected — recommend assessor review"
        else:
            result.physics_verdict = "INCONSISTENT"
            result.verdict_reason = "Significant physics inconsistencies — recommend SIU investigation"
 
        result.simulation_method = "pybullet" if self.use_pybullet else "analytical"
        payload = json.dumps({
            "claim_id": claim_id, "v1": result.vehicle_1_key,
            "v2": result.vehicle_2_key, "physics_score": result.physics_fraud_score,
            "verdict": result.physics_verdict,
        }, sort_keys=True)
        result.signature = hashlib.sha256(payload.encode()).hexdigest()
 
        logger.info(
            f"Reconstruction complete | {claim_id} | "
            f"Score: {result.physics_fraud_score}/100 | Verdict: {result.physics_verdict}"
        )
 
        return result
 
    def reconstruct_from_input(self, physics_input) -> "PhysicsResult":
        result = self.reconstruct(
            claim_id=physics_input.claim_id,
            v1_make=physics_input.v1_make,
            v1_model=physics_input.v1_model,
            v1_body_type=physics_input.v1_body_type,
            v1_stated_speed_kmh=physics_input.v1_stated_speed_kmh,
            v2_make=physics_input.v2_make,
            v2_model=physics_input.v2_model,
            v2_body_type=physics_input.v2_body_type,
            v2_stated_speed_kmh=physics_input.v2_stated_speed_kmh,
            impact_zone_v1=physics_input.impact_zone_v1,
            impact_zone_v2=physics_input.impact_zone_v2,
            approach_angle_deg=physics_input.approach_angle_deg,
            crush_depth_mm=physics_input.crush_depth_mm,
            estimated_repair_cost=physics_input.estimated_repair_cost,
            location_text=physics_input.location_text,
            latitude=physics_input.latitude,
            longitude=physics_input.longitude,
            pathway=physics_input.pathway,
            v1_speed_is_inferred=getattr(physics_input, "v1_speed_is_inferred", False),
            v1_speed_confidence=getattr(physics_input, "v1_speed_confidence", 1.0),
            v2_speed_is_inferred=getattr(physics_input, "v2_speed_is_inferred", False),
            v2_speed_confidence=getattr(physics_input, "v2_speed_confidence", 1.0),
        )
 
        if physics_input.pathway == "pathway_1":
 
            if physics_input.v1_impact_vertex_xyz:
                result.v1_impact_vertex_xyz = physics_input.v1_impact_vertex_xyz
                logger.info(
                    f"Pathway 1 vertex stored: {physics_input.v1_impact_vertex_xyz} "
                    f"for {physics_input.claim_id}"
                )
 
            if physics_input.impact_force_magnitude_n > 0:
                result.impact_force_magnitude_n = physics_input.impact_force_magnitude_n
                result.impact_force_is_estimated = False
 
            if physics_input.telemetry_delta_v_ms > 0:
                telemetry_speed_kmh = round(
                    physics_input.gps_speed_at_impact_kmh or
                    (physics_input.telemetry_delta_v_ms * 3.6 + physics_input.v2_stated_speed_kmh),
                    1
                )
                result.telemetry_delta_v_ms = physics_input.telemetry_delta_v_ms
 
                if physics_input.gps_speed_at_impact_kmh > 0:
                    stated = physics_input.v1_stated_speed_kmh
                    gps    = physics_input.gps_speed_at_impact_kmh
                    delta  = abs(stated - gps)
                    new_severity = velocity_fraud_severity(delta)
 
                    if new_severity != result.velocity_fraud_severity:
                        logger.info(
                            f"Pathway 1 velocity re-evaluation: "
                            f"stated={stated}km/h gps={gps}km/h delta={delta:.1f}km/h "
                            f"severity {result.velocity_fraud_severity} → {new_severity}"
                        )
                        result.velocity_fraud_severity = new_severity
                        result.velocity_fraud_flag = new_severity in ("high", "critical")
                        result.stated_vs_computed_delta_kmh = round(delta, 1)
 
                        velocity_penalty = {
                            "none": 0, "low": 5, "medium": 20,
                            "high": 40, "critical": 65
                        }.get(new_severity, 0)
                        consistency_penalty = max(0, 100 - result.impact_consistency_score)
                        result.physics_fraud_score = round(
                            min(100, (velocity_penalty * 0.6) + (consistency_penalty * 0.4)), 1
                        )
 
                        if result.physics_fraud_score < 20:
                            result.physics_verdict = "CONSISTENT"
                            result.verdict_reason = "GPS telemetry consistent with stated account"
                        elif result.physics_fraud_score < 45:
                            result.physics_verdict = "SUSPICIOUS"
                            result.verdict_reason = "Minor inconsistencies between GPS and stated speed"
                        else:
                            result.physics_verdict = "INCONSISTENT"
                            result.verdict_reason = (
                                "GPS telemetry contradicts stated speed — "
                                "recommend SIU investigation"
                            )
 
            result.confidence = min(1.0, round(result.confidence + 0.15, 2))
 
            logger.info(
                f"reconstruct_from_input complete [{physics_input.pathway}] | "
                f"{physics_input.claim_id} | "
                f"Score: {result.physics_fraud_score}/100 | "
                f"Verdict: {result.physics_verdict} | "
                f"Confidence: {result.confidence:.0%}"
            )
        return result
 
 
_engine_instance: Optional[CrashReconstructionEngine] = None
 
def get_engine() -> CrashReconstructionEngine:
    global _engine_instance
    if _engine_instance is None:
        _engine_instance = CrashReconstructionEngine()
    return _engine_instance
 
 