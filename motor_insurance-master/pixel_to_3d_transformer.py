
import io
import base64
from dataclasses import dataclass, field, asdict
from typing import Optional
 
 
DEFAULT_FOCAL_LENGTH_MM  = 4.2
DEFAULT_SENSOR_WIDTH_MM  = 6.17
DEFAULT_SENSOR_HEIGHT_MM = 4.55
DEFAULT_DISTANCE_M       = 2.5
 
 
DEPTH_LABEL_MAP = {
    "none":         (0,   5),
    "minimal":      (5,   20),
    "shallow":      (10,  40),
    "moderate":     (40,  100),
    "deep":         (100, 250),
    "severe":       (250, 500),
    "catastrophic": (500, 900),
}
 
 
@dataclass
class CameraParams:
    focal_length_mm: float = DEFAULT_FOCAL_LENGTH_MM
    sensor_width_mm: float = DEFAULT_SENSOR_WIDTH_MM
    sensor_height_mm: float = DEFAULT_SENSOR_HEIGHT_MM
    image_width_px: int = 3024
    image_height_px: int = 4032
    estimated_distance_m: float = DEFAULT_DISTANCE_M
    source: str = "default"
 
 
@dataclass
class TransformResult:
    vision_vertex_xyz: list = field(default_factory=list)
 
    crush_depth_mm: float = 0.0
    crush_width_mm: float = 0.0
    crush_height_mm: float = 0.0
 
    impact_zone: str = "front_bumper"
    impact_zone_confidence: float = 0.0
 
    damage_bbox_normalised: dict = field(default_factory=dict)
    damage_px_width: float = 0.0
    damage_px_height: float = 0.0
 
    camera_params: CameraParams = field(default_factory=CameraParams)
    fov_horizontal_deg: float = 0.0
    mm_per_pixel: float = 0.0
 
    confidence: float = 0.0
 
    gemini_raw: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)
 
    def to_dict(self) -> dict:
        return asdict(self)
 
 
def extract_camera_params(image_bytes: bytes) -> CameraParams:
    try:
        from PIL import Image, ExifTags
 
        img = Image.open(io.BytesIO(image_bytes))
        width_px, height_px = img.size
 
        params = CameraParams(
            image_width_px=width_px,
            image_height_px=height_px,
            source="default",
        )
 
        exif_data = img._getexif()
        if not exif_data:
            logger.info("No EXIF data — using smartphone defaults")
            return params
 
        exif = {ExifTags.TAGS.get(k, k): v for k, v in exif_data.items()}
 
        focal = exif.get("FocalLength")
        if focal:
            if isinstance(focal, tuple):
                focal = focal[0] / focal[1]
            params.focal_length_mm = float(focal)
            params.source = "exif"
 
        focal_35mm = exif.get("FocalLengthIn35mmFilm")
        if focal_35mm and focal_35mm > 0 and params.focal_length_mm > 0:
            crop_factor = focal_35mm / params.focal_length_mm
            params.sensor_width_mm  = round(36.0 / crop_factor, 2)
            params.sensor_height_mm = round(24.0 / crop_factor, 2)
            params.source = "exif_full"
 
        logger.info(
            f"Camera params: focal={params.focal_length_mm}mm "
            f"sensor={params.sensor_width_mm}×{params.sensor_height_mm}mm "
            f"image={params.image_width_px}×{params.image_height_px}px "
            f"source={params.source}"
        )
        return params
 
    except Exception as e:
        logger.warning(f"EXIF extraction failed: {e} — using smartphone defaults")
        return CameraParams()
 
 
def compute_fov(focal_length_mm: float, sensor_width_mm: float) -> float:
    return math.degrees(2 * math.atan(sensor_width_mm / (2 * focal_length_mm)))
 
 
def pixels_to_mm(
    pixel_count: float,
    image_width_px: int,
    fov_deg: float,
    distance_m: float,
) -> float:
    fov_rad = math.radians(fov_deg)
    real_m = pixel_count * (2 * distance_m * math.tan(fov_rad / 2)) / image_width_px
    return round(real_m * 1000, 1)
 
 
GEMINI_DAMAGE_PROMPT = """
You are a forensic vehicle damage analyst. Examine this vehicle photo and return
a JSON object describing the damage. No text outside the JSON.
 
{{
  "damage_detected": true/false,
  "impact_zone": "front_bumper / rear_bumper / driver_door / passenger_door / roof / bonnet / rear_driver / rear_passenger",
  "impact_zone_confidence": 0-100,
  "damage_bbox": {{
    "x1": 0.0-1.0,
    "y1": 0.0-1.0,
    "x2": 0.0-1.0,
    "y2": 0.0-1.0
  }},
  "crush_depth_label": "none / minimal / shallow / moderate / deep / severe / catastrophic",
  "crush_depth_confidence": 0-100,
  "damage_description": "one sentence factual description",
  "estimated_distance_m": 1.0-8.0
}}
 
Bounding box: normalised (0,0)=top-left (1,1)=bottom-right.
Crush depth label definitions:
  none=0-5mm, minimal=5-20mm, shallow=10-40mm, moderate=40-100mm,
  deep=100-250mm, severe=250-500mm, catastrophic=500mm+
estimated_distance_m: your estimate of how far the camera was from the damage.
"""
 
 
def _call_gemini_vision(image_bytes: bytes, api_key: str) -> dict:
    import google.generativeai as genai
 
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel("gemini-1.5-flash")
 
    image_part = {
        "mime_type": "image/jpeg",
        "data": base64.b64encode(image_bytes).decode(),
    }
 
    response = model.generate_content([GEMINI_DAMAGE_PROMPT, image_part])
    text = response.text.strip()
 
    json_start = text.find("{")
    json_end   = text.rfind("}") + 1
    if json_start < 0 or json_end <= json_start:
        raise ValueError(f"No JSON found in Gemini response: {text[:200]}")
 
    return json.loads(text[json_start:json_end])
 
 
def _zone_to_vertex(
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
            "bonnet":            zones.front_bumper,
            "front_left_wheel":  zones.front_left_wheel,
            "front_right_wheel": zones.front_right_wheel,
        }
 
        coords = zone_map.get(impact_zone, zones.front_bumper)
        return [coords["x"], coords["y"], coords["z"]]
 
    except Exception as e:
        logger.warning(f"Zone→vertex mapping failed for {impact_zone}: {e}")
        return [0.0, 0.0, 0.35]
 
 
class PixelTo3DTransformer:
 
    def __init__(self, gemini_api_key: str):
        self.api_key = gemini_api_key
 
    def transform(
        self,
        image_bytes: bytes,
        v1_make: str = "Unknown",
        v1_model: str = "Unknown",
        v1_body_type: str = "saloon",
        camera_distance_m: Optional[float] = None,
    ) -> TransformResult:
        result = TransformResult()
 
        camera = extract_camera_params(image_bytes)
        if camera_distance_m:
            camera.estimated_distance_m = camera_distance_m
        result.camera_params = camera
 
        fov_deg = compute_fov(camera.focal_length_mm, camera.sensor_width_mm)
        result.fov_horizontal_deg = round(fov_deg, 2)
 
        mm_per_px = pixels_to_mm(1, camera.image_width_px, fov_deg, camera.estimated_distance_m)
        result.mm_per_pixel = mm_per_px
 
        try:
            gemini_result = _call_gemini_vision(image_bytes, self.api_key)
            result.gemini_raw = gemini_result
            logger.info(
                f"Gemini damage detection: zone={gemini_result.get('impact_zone')} "
                f"depth={gemini_result.get('crush_depth_label')} "
                f"conf={gemini_result.get('crush_depth_confidence')}%"
            )
        except Exception as e:
            logger.warning(f"Gemini Vision failed: {e} — using fallback estimates")
            result.warnings.append(f"Gemini Vision unavailable: {str(e)}")
            result.confidence = 0.15
            result.impact_zone = "front_bumper"
            result.crush_depth_mm = 50.0
            result.crush_width_mm = 600.0
            result.vision_vertex_xyz = _zone_to_vertex("front_bumper", v1_make, v1_model, v1_body_type)
            return result
 
        if not gemini_result.get("damage_detected", False):
            result.warnings.append("Gemini detected no visible damage in photo")
            result.confidence = 0.1
            return result
 
        gemini_distance = gemini_result.get("estimated_distance_m")
        if gemini_distance and camera.source == "default":
            camera.estimated_distance_m = float(gemini_distance)
            mm_per_px = pixels_to_mm(1, camera.image_width_px, fov_deg, camera.estimated_distance_m)
            result.mm_per_pixel = mm_per_px
 
        bbox = gemini_result.get("damage_bbox", {})
        if bbox:
            x1 = float(bbox.get("x1", 0.3))
            y1 = float(bbox.get("y1", 0.3))
            x2 = float(bbox.get("x2", 0.7))
            y2 = float(bbox.get("y2", 0.7))
 
            bbox_px_width  = (x2 - x1) * camera.image_width_px
            bbox_px_height = (y2 - y1) * camera.image_height_px
 
            result.damage_bbox_normalised = {"x1": x1, "y1": y1, "x2": x2, "y2": y2}
            result.damage_px_width  = round(bbox_px_width, 1)
            result.damage_px_height = round(bbox_px_height, 1)
 
            result.crush_width_mm  = pixels_to_mm(
                bbox_px_width, camera.image_width_px, fov_deg, camera.estimated_distance_m
            )
            result.crush_height_mm = pixels_to_mm(
                bbox_px_height, camera.image_width_px, fov_deg, camera.estimated_distance_m
            )
 
            logger.info(
                f"Damage region: {bbox_px_width:.0f}×{bbox_px_height:.0f}px → "
                f"{result.crush_width_mm:.0f}×{result.crush_height_mm:.0f}mm real-world"
            )
 
        depth_label = gemini_result.get("crush_depth_label", "moderate")
        depth_range = DEPTH_LABEL_MAP.get(depth_label, (40, 100))
        result.crush_depth_mm = round((depth_range[0] + depth_range[1]) / 2, 1)
 
        impact_zone = gemini_result.get("impact_zone", "front_bumper")
        result.impact_zone = impact_zone
        result.impact_zone_confidence = float(gemini_result.get("impact_zone_confidence", 50)) / 100
 
        result.vision_vertex_xyz = _zone_to_vertex(
            impact_zone, v1_make, v1_model, v1_body_type
        )
        logger.info(
            f"Impact zone '{impact_zone}' → vertex {result.vision_vertex_xyz} "
            f"on {v1_make} {v1_model}"
        )
 
        depth_conf = float(gemini_result.get("crush_depth_confidence", 50)) / 100
        zone_conf  = result.impact_zone_confidence
        exif_bonus = 0.1 if camera.source in ("exif", "exif_full") else 0.0
        bbox_bonus = 0.1 if bbox else 0.0
 
        result.confidence = round(
            min(1.0, (depth_conf * 0.4 + zone_conf * 0.4 + exif_bonus + bbox_bonus)),
            2
        )
 
        logger.info(
            f"Transform complete: depth={result.crush_depth_mm}mm "
            f"width={result.crush_width_mm}mm "
            f"vertex={result.vision_vertex_xyz} "
            f"confidence={result.confidence:.0%}"
        )
        return result
 
    def transform_to_bridge_payload(
        self,
        image_bytes: bytes,
        v1_make: str = "Unknown",
        v1_model: str = "Unknown",
        v1_body_type: str = "saloon",
        camera_distance_m: Optional[float] = None,
    ) -> dict:
        result = self.transform(image_bytes, v1_make, v1_model, v1_body_type, camera_distance_m)
 
        return {
            "vision_vertex_xyz":    result.vision_vertex_xyz,
            "crush_depth_mm":       result.crush_depth_mm,
            "crush_width_mm":       result.crush_width_mm,
            "impact_zone_v1":       result.impact_zone,
            "vision_confidence":    result.confidence,
            "vision_transform_raw": result.to_dict(),
        }