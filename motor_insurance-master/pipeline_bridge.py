import re
import math
import hmac
import hashlib
import logging
import secrets
from dataclasses import dataclass, field, asdict
from typing import Optional

logger = logging.getLogger(__name__)

G = 9.81


@dataclass
class PhysicsInput:
    claim_id: str

    v1_make: str = "Unknown"
    v1_model: str = "Unknown"
    v1_body_type: str = "saloon"
    v1_stated_speed_kmh: float = 0.0
    v1_speed_is_inferred: bool = False
    v1_speed_confidence: float = 1.0

    v2_make: str = "Unknown"
    v2_model: str = "Unknown"
    v2_body_type: str = "saloon"
    v2_stated_speed_kmh: float = 0.0
    v2_speed_is_inferred: bool = False
    v2_speed_confidence: float = 1.0

    impact_zone_v1: str = "front_bumper"
    impact_zone_v2: str = "front_bumper"
    approach_angle_deg: float = 180.0
    crush_depth_mm: float = 0.0
    estimated_repair_cost: float = 0.0

    location_text: str = ""
    latitude: float = 0.0
    longitude: float = 0.0

    telemetry_available: bool = False
    accelerometer_peak_g: float = 0.0
    gyroscope_yaw_rate: float = 0.0
    gps_speed_at_impact_kmh: float = 0.0

    v1_impact_vertex_xyz: list = field(default_factory=list)
    v1_force_vector_n: list = field(default_factory=list)
    impact_force_magnitude_n: float = 0.0
    telemetry_delta_v_ms: float = 0.0
    v1_yaw_rate_deg_s: float = 0.0
    v1_pitch_rate_deg_s: float = 0.0
    v1_roll_rate_deg_s: float = 0.0

    pathway: str = "pathway_2"

    hmac_valid: bool = False
    data_quality_score: float = 0.5
    warnings: list = field(default_factory=list)

    def to_dict(self):
        return asdict(self)


def validate_hmac(
    payload_bytes: bytes,
    received_signature: str,
    device_secret: str
) -> bool:
    expected = hmac.new(
        device_secret.encode(),
        payload_bytes,
        hashlib.sha256
    ).hexdigest()

    return secrets.compare_digest(expected, received_signature)


SPEED_KEYWORDS = {
    "parked":           (0, 0),
    "stationary":       (0, 0),
    "crawling":         (5, 15),
    "slowly":           (10, 25),
    "slow":             (10, 25),
    "normal speed":     (40, 70),
    "moderate speed":   (40, 70),
    "fast":             (70, 100),
    "speeding":         (90, 140),
    "high speed":       (90, 140),
    "overtaking":       (80, 120),
    "traffic jam":      (5, 20),
}

DAMAGE_KEYWORDS = {
    "scratch":          (0, 10),
    "scratched":        (0, 10),
    "dent":             (10, 40),
    "dented":           (10, 40),
    "minor damage":     (10, 40),
    "slight damage":    (10, 40),
    "damaged":          (30, 80),
    "severely damaged": (80, 200),
    "crumpled":         (100, 250),
    "crushed":          (150, 350),
    "written off":      (300, 600),
    "total loss":       (300, 600),
    "airbags deployed": (150, 400),
    "airbag":           (150, 400),
}

IMPACT_ZONE_KEYWORDS = {
    "front":            "front_bumper",
    "bonnet":           "front_bumper",
    "hood":             "front_bumper",
    "bumper":           "front_bumper",
    "rear":             "rear_bumper",
    "back":             "rear_bumper",
    "boot":             "rear_bumper",
    "side":             "driver_door",
    "door":             "driver_door",
    "driver":           "driver_door",
    "passenger side":   "passenger_door",
    "left side":        "driver_door",
    "right side":       "passenger_door",
    "t-bone":           "driver_door",
    "t bone":           "driver_door",
}

COLLISION_ANGLE_KEYWORDS = {
    "head on":          180.0,
    "head-on":          180.0,
    "frontal":          180.0,
    "rear end":         0.0,
    "rear-end":         0.0,
    "rear ended":       0.0,
    "shunted":          0.0,
    "t-bone":           90.0,
    "t bone":           90.0,
    "side swipe":       45.0,
    "sideswipe":        45.0,
    "sideswiped":       45.0,
    "overtaking":       15.0,
}


def infer_speed_from_narrative(narrative: str) -> tuple[float, float]:
    narrative_lower = narrative.lower()

    speed_patterns = [
        r'(\d+)\s*km/?h',
        r'(\d+)\s*kph',
        r'at\s+(\d+)\s*(?:km|kph|kilometers)',
        r'speed\s+of\s+(\d+)',
        r'doing\s+(\d+)',
    ]
    for pattern in speed_patterns:
        match = re.search(pattern, narrative_lower)
        if match:
            speed = float(match.group(1))
            if 0 < speed < 200:
                logger.info(f"Explicit speed found: {speed} km/h")
                return speed, 0.85

    for keyword, (min_s, max_s) in SPEED_KEYWORDS.items():
        if keyword in narrative_lower:
            estimated = (min_s + max_s) / 2
            logger.info(f"Speed inferred from keyword '{keyword}': {estimated} km/h")
            return estimated, 0.45

    logger.info("No speed found — using urban default 40 km/h")
    return 40.0, 0.25


def infer_crush_depth_from_narrative(narrative: str) -> tuple[float, float]:
    narrative_lower = narrative.lower()

    for keyword, (min_d, max_d) in DAMAGE_KEYWORDS.items():
        if keyword in narrative_lower:
            depth = (min_d + max_d) / 2
            return depth, 0.35

    return 50.0, 0.2


STATED_OTHER_VEHICLE_POSITION_PATTERN = re.compile(
    r"Position of other vehicle at moment of impact \(claimant-confirmed\):\s*(.+)"
)


def infer_stated_collision_geometry(narrative: str) -> Optional[tuple[str, float]]:
    """Claimant-confirmed collision geometry from the FNOL "where was the
    other vehicle" question (build_structured_intake_block in routes.py),
    when present -- claims filed before that question existed simply won't
    match. Returns (impact_zone_v1, approach_angle_deg) or None when absent
    or the claimant said "not sure" (in which case narrative/CV inference is
    the only signal available, same as before this question existed).

    Treated as a STATED input, same tier as stated speed/crush depth -- not
    ground truth. A colluding claimant could describe a false position, so
    this is meant to be cross-checked against CV-detected damage location
    when photos are available (see service.py), not trusted blindly."""
    match = STATED_OTHER_VEHICLE_POSITION_PATTERN.search(narrative)
    if not match:
        return None
    stated = match.group(1).strip().lower()
    if stated.startswith("behind"):
        return "rear_bumper", 0.0
    if stated.startswith("in front") or "oncoming" in stated:
        return "front_bumper", 180.0
    if "left" in stated:
        return "driver_door", 90.0
    if "right" in stated:
        return "passenger_door", 90.0
    return None


def infer_impact_zone(narrative: str) -> tuple[str, str]:
    narrative_lower = narrative.lower()
    zone_v1 = "front_bumper"
    zone_v2 = "front_bumper"

    for keyword, zone in IMPACT_ZONE_KEYWORDS.items():
        if keyword in narrative_lower:
            zone_v1 = zone
            break

    return zone_v1, zone_v2


def infer_approach_angle(narrative: str) -> tuple[float, float]:
    narrative_lower = narrative.lower()

    for keyword, angle in COLLISION_ANGLE_KEYWORDS.items():
        if keyword in narrative_lower:
            return angle, 0.6

    if any(phrase in narrative_lower for phrase in [
        "right side", "left side", "driver side", "passenger side",
        "side of my vehicle", "side of his vehicle", "door",
        "crashed into the side", "hit the side", "struck the side",
        "junction", "intersection", "crossed", "jumped the light",
        "ran a red", "jumped a red"
    ]):
        return 90.0, 0.55

    if any(phrase in narrative_lower for phrase in [
        "from behind", "rear", "back of my", "shunted", "pushed from behind",
        "rammed from behind", "rear-ended"
    ]):
        return 0.0, 0.55

    if any(phrase in narrative_lower for phrase in [
        "head on", "head-on", "frontal", "front of my", "coming towards"
    ]):
        return 180.0, 0.55

    return 180.0, 0.3


def extract_third_party_from_text(text: str) -> tuple[str, str, str]:
    text_lower = text.lower()

    HEAVY_COMMERCIAL_PATTERNS = [
        ("trailer",      "Semi-Trailer", "heavy_commercial"),
        ("semi-trailer", "Semi-Trailer", "heavy_commercial"),
        ("lorry",        "Lorry",        "heavy_commercial"),
        ("truck",        "Truck",        "heavy_commercial"),
        ("isuzu fvr",    "FVR Truck",    "heavy_commercial"),
        ("isuzu nkr",    "NKR Lorry",    "heavy_commercial"),
        ("canter",       "Canter",       "heavy_commercial"),
        ("actros",       "Actros",       "heavy_commercial"),
        ("tata bus",     "Bus",          "heavy_commercial"),
    ]

    for keyword, model, body_type in HEAVY_COMMERCIAL_PATTERNS:
        if keyword in text_lower:
            logger.info(f"V2 heavy commercial extracted: '{keyword}' → {model}")
            return "Unknown", model, body_type

    if any(w in text_lower for w in ["boda", "motorcycle", "motorbike", "boda boda"]):
        return "Unknown", "Motorcycle", "motorcycle"

    if any(w in text_lower for w in ["matatu", "psvs", "14-seater", "nissan caravan"]):
        return "Unknown", "Matatu", "matatu"
    if "hiace" in text_lower and "matatu" in text_lower:
        return "Toyota", "Hiace", "matatu"

    VEHICLE_PATTERNS = [
        ("toyota", "premio", "saloon"),
        ("toyota", "allion", "saloon"),
        ("toyota", "fielder", "saloon"),
        ("toyota", "probox", "saloon"),
        ("toyota", "vitz", "saloon"),
        ("toyota", "axio", "saloon"),
        ("toyota", "rav4", "suv"),
        ("toyota", "prado", "suv"),
        ("toyota", "hilux", "pickup"),
        ("nissan", "x-trail", "suv"),
        ("nissan", "tiida", "saloon"),
        ("subaru", "forester", "suv"),
        ("subaru", "impreza", "saloon"),
        ("isuzu", "d-max", "pickup"),
        ("honda", "fit", "saloon"),
        ("mazda", "demio", "saloon"),
        ("boda", "", "motorcycle"),
        ("motorcycle", "", "motorcycle"),
        ("motorbike", "", "motorcycle"),
        ("tuk tuk", "", "tuk_tuk"),
    ]

    for make, model, body_type in VEHICLE_PATTERNS:
        if make in text_lower and (not model or model in text_lower):
            return make.title(), model.title(), body_type

    return "Unknown", "Unknown", "saloon"


def extract_vehicle_from_text(text: str) -> tuple[str, str, str]:
    text_lower = text.lower()

    VEHICLE_PATTERNS = [
        ("toyota", "premio", "saloon"),
        ("toyota", "allion", "saloon"),
        ("toyota", "fielder", "saloon"),
        ("toyota", "axio", "saloon"),
        ("toyota", "vitz", "saloon"),
        ("toyota", "probox", "saloon"),
        ("toyota", "rav4", "suv"),
        ("toyota", "harrier", "suv"),
        ("toyota", "prado", "suv"),
        ("toyota", "hilux", "pickup"),
        ("toyota", "hiace", "matatu"),
        ("nissan", "x-trail", "suv"),
        ("nissan", "xtrail", "suv"),
        ("nissan", "tiida", "saloon"),
        ("nissan", "note", "saloon"),
        ("nissan", "caravan", "matatu"),
        ("subaru", "forester", "suv"),
        ("subaru", "impreza", "saloon"),
        ("isuzu", "d-max", "pickup"),
        ("isuzu", "nqr", "matatu"),
        ("bajaj", "boxer", "motorcycle"),
        ("bajaj", "re", "tuk_tuk"),
        ("tvs", "apache", "motorcycle"),
        ("honda", "fit", "saloon"),
        ("mazda", "demio", "saloon"),
        ("matatu", "", "matatu"),
        ("boda", "", "motorcycle"),
        ("boda boda", "", "motorcycle"),
        ("tuk tuk", "", "tuk_tuk"),
        ("isuzu",      "fvr",      "heavy_commercial"),
        ("isuzu",      "nkr",      "heavy_commercial"),
        ("mitsubishi", "canter",   "heavy_commercial"),
        ("tata",       "bus",      "heavy_commercial"),
        ("mercedes",   "actros",   "heavy_commercial"),
        ("trailer",    "",         "heavy_commercial"),
        ("semi-trailer","",        "heavy_commercial"),
        ("lorry",      "",         "heavy_commercial"),
        ("truck",      "",         "heavy_commercial"),
    ]

    for make, model, body_type in VEHICLE_PATTERNS:
        if make in text_lower and (not model or model in text_lower):
            return make.title(), model.title(), body_type

    if any(w in text_lower for w in ["matatu", "bus", "psvs"]):
        return "Unknown", "Matatu", "matatu"
    if any(w in text_lower for w in ["boda", "motorcycle", "motorbike"]):
        return "Unknown", "Motorcycle", "motorcycle"
    if any(w in text_lower for w in ["pickup", "truck"]):
        return "Unknown", "Pickup", "pickup"
    if any(w in text_lower for w in ["suv", "4x4", "landcruiser"]):
        return "Unknown", "SUV", "suv"

    return "Unknown", "Unknown", "saloon"


def determine_pathway(payload: dict) -> str:
    has_telemetry = (
        payload.get("accelerometer_peak_g", 0) > 0 and
        payload.get("gps_speed_at_impact_kmh", 0) > 0
    )
    has_vision_3d = payload.get("vision_vertex_xyz") is not None

    if has_telemetry or has_vision_3d:
        return "pathway_1"
    return "pathway_2"


def data_quality_score(physics_input: PhysicsInput) -> float:
    score = 0.0
    if physics_input.v1_make != "Unknown":              score += 0.15
    if physics_input.v1_model != "Unknown":             score += 0.15
    if physics_input.v2_make != "Unknown":              score += 0.10
    if physics_input.v2_model != "Unknown":             score += 0.10
    if physics_input.v1_stated_speed_kmh > 0:          score += 0.15
    if physics_input.crush_depth_mm > 0:                score += 0.15
    if physics_input.telemetry_available:               score += 0.05
    if physics_input.v1_impact_vertex_xyz:              score += 0.05
    if physics_input.latitude != 0:                     score += 0.05
    if physics_input.approach_angle_deg != 180:         score += 0.05
    return round(min(1.0, score), 2)


class PipelineBridge:

    def __init__(self, device_secrets: Optional[dict] = None):
        self.device_secrets = device_secrets or {}

    def _map_impact_to_vertex(
        self,
        impact_zone: str,
        v1_make: str,
        v1_model: str,
        v1_body_type: str,
    ) -> list[float]:
        try:
            from vehicle_registry import get_vehicle_profile
            profile, _ = get_vehicle_profile(v1_make, v1_model, v1_body_type)
            zones = profile.zones

            zone_map = {
                "front_bumper":      zones.front_bumper,
                "rear_bumper":       zones.rear_bumper,
                "driver_door":       zones.driver_door,
                "passenger_door":    zones.passenger_door,
                "rear_driver":       zones.rear_driver,
                "rear_passenger":    zones.rear_passenger,
                "roof":              zones.roof,
                "front_left_wheel":  zones.front_left_wheel,
                "front_right_wheel": zones.front_right_wheel,
            }

            coords = zone_map.get(impact_zone, zones.front_bumper)
            vertex = [coords["x"], coords["y"], coords["z"]]
            logger.info(
                f"Pathway 1 vertex map: {impact_zone} → "
                f"[{vertex[0]}, {vertex[1]}, {vertex[2]}] on {v1_make} {v1_model}"
            )
            return vertex

        except Exception as e:
            logger.warning(f"Vertex mapping failed for {impact_zone}: {e}")
            return []

    def _compute_force_vector(
        self,
        accelerometer_peak_g: float,
        gyroscope_yaw_rate: float,
        vehicle_mass_kg: float,
        impact_vertex_xyz: list[float],
    ) -> dict:
        a_ms2 = accelerometer_peak_g * G
        force_magnitude_n = vehicle_mass_kg * a_ms2

        impact_duration_s = 0.1
        delta_v_ms = a_ms2 * impact_duration_s

        if abs(gyroscope_yaw_rate) > 120:
            resolved_angle_deg = 90.0
        elif abs(gyroscope_yaw_rate) > 45:
            resolved_angle_deg = 45.0
        elif abs(gyroscope_yaw_rate) > 15:
            resolved_angle_deg = 15.0
        else:
            resolved_angle_deg = 180.0

        angle_rad = math.radians(resolved_angle_deg)

        pitch_component = math.sin(math.radians(abs(gyroscope_yaw_rate) * 0.1))

        fx = force_magnitude_n * math.cos(angle_rad)
        fy = force_magnitude_n * math.sin(angle_rad)
        fz = force_magnitude_n * pitch_component

        force_vector = [round(fx, 1), round(fy, 1), round(fz, 1)]

        logger.info(
            f"Pathway 1 force vector: F={force_magnitude_n:.0f}N | "
            f"[{fx:.0f}, {fy:.0f}, {fz:.0f}]N | "
            f"ΔV={delta_v_ms:.2f}m/s | angle={resolved_angle_deg}°"
        )

        return {
            "force_vector_n": force_vector,
            "magnitude_n": round(force_magnitude_n, 1),
            "delta_v_ms": round(delta_v_ms, 3),
            "resolved_angle_deg": resolved_angle_deg,
        }

    def _derive_crush_depth_from_telemetry(
        self,
        accelerometer_peak_g: float,
        vehicle_mass_kg: float,
        crumple_A: float,
        crumple_B: float,
        impact_width_m: float,
    ) -> float:
        try:
            delta_v_ms = (accelerometer_peak_g * G) * 0.1
            energy_j = 0.5 * vehicle_mass_kg * (delta_v_ms ** 2)

            a_coef = crumple_B * impact_width_m
            b_coef = crumple_A * impact_width_m
            c_coef = -(energy_j / 1000)

            discriminant = b_coef ** 2 - 4 * a_coef * c_coef
            if discriminant < 0:
                return 50.0

            crush_m = (-b_coef + math.sqrt(discriminant)) / (2 * a_coef)
            crush_mm = max(crush_m * 1000, 0.0)
            logger.info(f"Pathway 1 telemetry-derived crush depth: {crush_mm:.1f}mm")
            return round(crush_mm, 1)

        except Exception as e:
            logger.warning(f"Crush depth derivation failed: {e}")
            return 50.0

    def process_telemetry_payload(
        self,
        raw_payload: dict,
        device_id: Optional[str] = None,
        received_signature: Optional[str] = None,
    ) -> PhysicsInput:
        import json
        physics_input = PhysicsInput(claim_id=raw_payload.get("claim_id", ""))
        physics_input.warnings = []

        if device_id and received_signature and device_id in self.device_secrets:
            payload_bytes = json.dumps(raw_payload, sort_keys=True).encode()
            valid = validate_hmac(
                payload_bytes, received_signature, self.device_secrets[device_id]
            )
            physics_input.hmac_valid = valid
            if not valid:
                logger.warning(f"HMAC validation failed for device {device_id}")
                physics_input.warnings.append(
                    "HMAC signature invalid — telemetry may be tampered"
                )
        else:
            physics_input.hmac_valid = False
            physics_input.warnings.append(
                "No HMAC signature — telemetry chain of custody not established"
            )

        physics_input.v1_make = raw_payload.get("v1_make", "Unknown")
        physics_input.v1_model = raw_payload.get("v1_model", "Unknown")
        physics_input.v1_body_type = raw_payload.get("v1_body_type", "saloon")
        physics_input.v1_stated_speed_kmh = float(raw_payload.get("v1_stated_speed_kmh", 0))
        physics_input.v2_make = raw_payload.get("v2_make", "Unknown")
        physics_input.v2_model = raw_payload.get("v2_model", "Unknown")
        physics_input.v2_body_type = raw_payload.get("v2_body_type", "saloon")
        physics_input.v2_stated_speed_kmh = float(raw_payload.get("v2_stated_speed_kmh", 0))
        physics_input.impact_zone_v1 = raw_payload.get("impact_zone_v1", "front_bumper")
        physics_input.impact_zone_v2 = raw_payload.get("impact_zone_v2", "front_bumper")
        physics_input.approach_angle_deg = float(raw_payload.get("approach_angle_deg", 180.0))
        physics_input.crush_depth_mm = float(raw_payload.get("crush_depth_mm", 0))
        physics_input.estimated_repair_cost = float(raw_payload.get("estimated_repair_cost", 0))
        physics_input.location_text = raw_payload.get("location_text", "")
        physics_input.latitude = float(raw_payload.get("latitude", 0))
        physics_input.longitude = float(raw_payload.get("longitude", 0))

        physics_input.telemetry_available = True
        physics_input.accelerometer_peak_g = float(raw_payload.get("accelerometer_peak_g", 0))
        physics_input.gyroscope_yaw_rate = float(raw_payload.get("gyroscope_yaw_rate", 0))
        physics_input.gps_speed_at_impact_kmh = float(raw_payload.get("gps_speed_at_impact_kmh", 0))

        if physics_input.hmac_valid and physics_input.gps_speed_at_impact_kmh > 0:
            physics_input.v1_stated_speed_kmh = physics_input.gps_speed_at_impact_kmh
            physics_input.warnings.append(
                f"Speed overridden with GPS telemetry: {physics_input.gps_speed_at_impact_kmh} km/h"
            )

        physics_input.pathway = determine_pathway(raw_payload)

        if physics_input.pathway == "pathway_1":
            physics_input.v1_impact_vertex_xyz = self._map_impact_to_vertex(
                impact_zone=physics_input.impact_zone_v1,
                v1_make=physics_input.v1_make,
                v1_model=physics_input.v1_model,
                v1_body_type=physics_input.v1_body_type,
            )

            try:
                from vehicle_registry import get_vehicle_profile
                profile, _ = get_vehicle_profile(
                    physics_input.v1_make,
                    physics_input.v1_model,
                    physics_input.v1_body_type,
                )
                vehicle_mass_kg = profile.effective_mass_kg
                impact_width_m = (profile.width_mm / 1000) * 0.4
            except Exception:
                vehicle_mass_kg = 1400.0
                impact_width_m = 0.68
                physics_input.warnings.append(
                    "Vehicle profile not found — using default mass 1400kg for force computation"
                )

            if physics_input.accelerometer_peak_g > 0:
                force_result = self._compute_force_vector(
                    accelerometer_peak_g=physics_input.accelerometer_peak_g,
                    gyroscope_yaw_rate=physics_input.gyroscope_yaw_rate,
                    vehicle_mass_kg=vehicle_mass_kg,
                    impact_vertex_xyz=physics_input.v1_impact_vertex_xyz,
                )
                physics_input.v1_force_vector_n = force_result["force_vector_n"]
                physics_input.impact_force_magnitude_n = force_result["magnitude_n"]
                physics_input.telemetry_delta_v_ms = force_result["delta_v_ms"]
                physics_input.v1_yaw_rate_deg_s = physics_input.gyroscope_yaw_rate

                physics_input.approach_angle_deg = force_result["resolved_angle_deg"]
                physics_input.warnings.append(
                    f"Approach angle resolved from gyroscope: {force_result['resolved_angle_deg']}°"
                )

                if physics_input.crush_depth_mm == 0:
                    try:
                        physics_input.crush_depth_mm = self._derive_crush_depth_from_telemetry(
                            accelerometer_peak_g=physics_input.accelerometer_peak_g,
                            vehicle_mass_kg=vehicle_mass_kg,
                            crumple_A=profile.crumple_A,
                            crumple_B=profile.crumple_B,
                            impact_width_m=impact_width_m,
                        )
                        physics_input.warnings.append(
                            f"Crush depth derived from accelerometer telemetry: "
                            f"{physics_input.crush_depth_mm}mm"
                        )
                    except Exception as e:
                        logger.warning(f"Telemetry crush depth derivation failed: {e}")

            logger.info(
                f"Pathway 1 complete for {physics_input.claim_id} | "
                f"Vertex: {physics_input.v1_impact_vertex_xyz} | "
                f"Force: {physics_input.impact_force_magnitude_n:.0f}N | "
                f"ΔV: {physics_input.telemetry_delta_v_ms:.2f}m/s"
            )

        physics_input.data_quality_score = data_quality_score(physics_input)
        return physics_input

    def process_manual_payload(
        self,
        claim_id: str,
        narrative: str,
        v1_make: str = "",
        v1_model: str = "",
        v1_body_type: str = "",
        v2_make: str = "",
        v2_model: str = "",
        v2_body_type: str = "",
        v1_stated_speed_kmh: float = 0.0,
        v2_stated_speed_kmh: float = 0.0,
        crush_depth_mm: float = 0.0,
        approach_angle_deg: float = 0.0,
        impact_zone_v1: str = "",
        location_text: str = "",
        latitude: float = 0.0,
        longitude: float = 0.0,
        estimated_repair_cost: float = 0.0,
    ) -> PhysicsInput:
        physics_input = PhysicsInput(claim_id=claim_id)
        physics_input.pathway = "pathway_2"
        physics_input.hmac_valid = False
        physics_input.warnings = ["No telemetry — parameters inferred from narrative"]

        if v1_make and v1_model:
            physics_input.v1_make = v1_make
            physics_input.v1_model = v1_model
            physics_input.v1_body_type = v1_body_type or "saloon"
        else:
            make, model, bt = extract_vehicle_from_text(narrative)
            physics_input.v1_make = make
            physics_input.v1_model = model
            physics_input.v1_body_type = bt
            physics_input.warnings.append(f"V1 inferred from narrative: {make} {model}")

        if v2_make and v2_model:
            physics_input.v2_make = v2_make
            physics_input.v2_model = v2_model
            physics_input.v2_body_type = v2_body_type or "saloon"
        else:
            v2_make_inf, v2_model_inf, v2_bt_inf = extract_third_party_from_text(narrative)
            if v2_model_inf != "Unknown":
                physics_input.v2_make = v2_make_inf
                physics_input.v2_model = v2_model_inf
                physics_input.v2_body_type = v2_bt_inf
                physics_input.warnings.append(
                    f"V2 inferred from narrative: {v2_make_inf} {v2_model_inf} ({v2_bt_inf})"
                )
            else:
                physics_input.v2_make = "Unknown"
                physics_input.v2_model = "Unknown"
                physics_input.v2_body_type = "saloon"
                physics_input.warnings.append("V2 details not provided — using generic saloon")

        if v1_stated_speed_kmh > 0:
            physics_input.v1_stated_speed_kmh = v1_stated_speed_kmh
            physics_input.v1_speed_is_inferred = False
            physics_input.v1_speed_confidence = 1.0
        else:
            inferred_speed, speed_conf = infer_speed_from_narrative(narrative)
            physics_input.v1_stated_speed_kmh = inferred_speed
            physics_input.v1_speed_is_inferred = True
            physics_input.v1_speed_confidence = speed_conf
            physics_input.warnings.append(
                f"V1 speed estimated from narrative wording (not explicitly stated by claimant): "
                f"~{inferred_speed} km/h (confidence {speed_conf:.0%})"
            )

        # A fixed-object V2 (wall/pillar/barrier -- see service.py's single-
        # vehicle-vs-fixed-object reconstruction path) is 0 km/h by
        # definition, not "no value given". The `v2_stated_speed_kmh > 0`
        # check below can't distinguish "caller explicitly passed 0.0
        # because it's a barrier" from "caller passed nothing" -- without
        # this branch it fell through to inferring a speed from the
        # narrative text (which isn't even describing V2 at all in this
        # case) and silently animated a barrier moving at highway speed.
        if physics_input.v2_body_type == "fixed_object":
            physics_input.v2_stated_speed_kmh = 0.0
            physics_input.v2_speed_is_inferred = False
            physics_input.v2_speed_confidence = 1.0
        elif v2_stated_speed_kmh > 0:
            physics_input.v2_stated_speed_kmh = v2_stated_speed_kmh
            physics_input.v2_speed_is_inferred = False
            physics_input.v2_speed_confidence = 1.0
        else:
            inferred_speed_v2, speed_conf_v2 = infer_speed_from_narrative(narrative)
            physics_input.v2_stated_speed_kmh = inferred_speed_v2
            physics_input.v2_speed_is_inferred = True
            physics_input.v2_speed_confidence = speed_conf_v2
            physics_input.warnings.append(
                f"V2 speed estimated from narrative wording (not explicitly stated by claimant): "
                f"~{inferred_speed_v2} km/h (confidence {speed_conf_v2:.0%})"
            )

        if crush_depth_mm > 0:
            physics_input.crush_depth_mm = crush_depth_mm
        else:
            inferred_depth, depth_conf = infer_crush_depth_from_narrative(narrative)
            physics_input.crush_depth_mm = inferred_depth
            physics_input.warnings.append(
                f"Crush depth inferred from narrative: {inferred_depth}mm "
                f"(confidence {depth_conf:.0%})"
            )

        # CV-detected damage location from the claim's own photos (passed by
        # service.py, sourced from part_identifier.py's damage_zones) beats
        # a narrative-text guess -- same "measured/observed beats inferred"
        # principle as speed and crush depth above.
        if impact_zone_v1:
            physics_input.impact_zone_v1 = impact_zone_v1
            _, zone_v2 = infer_impact_zone(narrative)
            physics_input.impact_zone_v2 = zone_v2
        else:
            zone_v1, zone_v2 = infer_impact_zone(narrative)
            physics_input.impact_zone_v1 = zone_v1
            physics_input.impact_zone_v2 = zone_v2

        if approach_angle_deg > 0:
            physics_input.approach_angle_deg = approach_angle_deg
        else:
            angle, angle_conf = infer_approach_angle(narrative)
            physics_input.approach_angle_deg = angle
            physics_input.warnings.append(
                f"Approach angle inferred: {angle}° (confidence {angle_conf:.0%})"
            )

        physics_input.location_text = location_text
        physics_input.latitude = latitude
        physics_input.longitude = longitude
        physics_input.estimated_repair_cost = estimated_repair_cost
        physics_input.telemetry_available = False
        physics_input.data_quality_score = data_quality_score(physics_input)

        logger.info(
            f"Manual payload processed for {claim_id} | "
            f"Pathway 2 | Quality: {physics_input.data_quality_score}"
        )
        return physics_input


_bridge_instance: Optional[PipelineBridge] = None

def get_bridge() -> PipelineBridge:
    global _bridge_instance
    if _bridge_instance is None:
        _bridge_instance = PipelineBridge()
    return _bridge_instance