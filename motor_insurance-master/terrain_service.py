import math
import logging
import time
from typing import Optional, Tuple
from dataclasses import dataclass
import requests

logger = logging.getLogger(__name__)

OPENTOPO_API_KEY = "39d3304c16d17adeabf622e2747225e7"
OPENTOPO_BASE_URL = "https://portal.opentopography.org/API/globaldem"
OPENTOPO_TIMEOUT = 8

_terrain_cache: dict = {}
CACHE_TTL_SECONDS = 86400

ROAD_FRICTION = {
    "tarmac":     0.75,
    "wet_tarmac": 0.45,
    "gravel":     0.55,
    "murram":     0.50,
    "dirt":       0.40,
    "mud":        0.25,
}

KENYAN_TERRAIN_PROFILES = {
    "limuru_road":     (8.5,  "tarmac"),
    "maai_mahiu":      (12.0, "tarmac"),
    "thika_road":      (1.5,  "tarmac"),
    "mombasa_road":    (2.0,  "tarmac"),
    "ngong_road":      (3.0,  "tarmac"),
    "waiyaki_way":     (2.5,  "tarmac"),
    "kiambu_road":     (4.0,  "tarmac"),
    "langata_road":    (2.0,  "tarmac"),
    "jogoo_road":      (1.0,  "tarmac"),
    "eastern_bypass":  (1.5,  "tarmac"),
    "northern_bypass": (2.0,  "tarmac"),
    "nairobi_cbd":     (1.5,  "tarmac"),
    "nakuru":          (3.0,  "tarmac"),
    "eldoret":         (2.5,  "tarmac"),
    "kisumu":          (1.5,  "tarmac"),
    "mombasa":         (1.0,  "tarmac"),
    "default":         (1.0,  "tarmac"),
}

SURFACE_KEYWORDS = {
    "murram":   "murram",
    "dirt":     "dirt",
    "gravel":   "gravel",
    "unpaved":  "gravel",
    "wet":      "wet_tarmac",
    "flooded":  "wet_tarmac",
    "rain":     "wet_tarmac",
    "mud":      "mud",
}


@dataclass
class TerrainResult:
    latitude: float
    longitude: float
    elevation_m: float
    slope_degrees: float
    road_surface: str
    friction_coefficient: float
    source: str
    confidence: float

    @property
    def slope_radians(self) -> float:
        return math.radians(self.slope_degrees)

    @property
    def gravity_component(self) -> float:
        """Component of gravity along slope (m/s²)"""
        return 9.81 * math.sin(self.slope_radians)


def _cache_key(lat: float, lng: float) -> Tuple[float, float]:
    return (round(lat, 3), round(lng, 3))


def _is_cache_valid(timestamp: float) -> bool:
    return (time.time() - timestamp) < CACHE_TTL_SECONDS


# Maps this module's coarse surface categories (weather already folded in
# for the wet/mud cases, e.g. "wet_tarmac") onto the DB `friction_mu_lookup`
# table's separate (road_type, weather) columns -- that table has real
# per-condition rows (asphalt/oily, murram/muddy, etc.) this module's flat
# 6-entry ROAD_FRICTION dict can't express.
_SURFACE_TO_DB_ROAD_WEATHER = {
    "tarmac":     ("asphalt", "dry"),
    "wet_tarmac": ("asphalt", "wet"),
    "gravel":     ("gravel", "dry"),
    "murram":     ("murram", "dry"),
    "dirt":       ("dirt", "dry"),
    "mud":        ("murram", "muddy"),
}


def resolve_friction(surface: str) -> float:
    """Friction coefficient for a surface category -- tries the DB-seeded
    friction_mu_lookup table (weather-aware, richer than this module's own
    hardcoded ROAD_FRICTION) first, falling back to ROAD_FRICTION on any DB
    error so a missing/unreachable DB can't newly break terrain lookups that
    worked before this table was wired in."""
    road_type, weather = _SURFACE_TO_DB_ROAD_WEATHER.get(surface, ("asphalt", "dry"))
    try:
        from registry_adapter import RegistryAdapter
        result = RegistryAdapter().get_friction_mu(road_type, weather)
        if result and result.get("lookup_method") == "exact":
            return result["mu"]
    except Exception as e:
        logger.warning(f"DB friction lookup failed for ({road_type}, {weather}), falling back to hardcoded table: {e}")
    return ROAD_FRICTION.get(surface, 0.75)


def _infer_surface_from_text(location_text: str) -> str:
    loc_lower = location_text.lower()
    for keyword, surface in SURFACE_KEYWORDS.items():
        if keyword in loc_lower:
            return surface
    return "tarmac"


def _fallback_from_text(location_text: str) -> TerrainResult:
    loc_lower = location_text.lower()
    slope_deg, surface = KENYAN_TERRAIN_PROFILES["default"]
    matched_profile = "default"

    for key, (slope, surf) in KENYAN_TERRAIN_PROFILES.items():
        if key.replace("_", " ") in loc_lower or key in loc_lower.replace(" ", "_"):
            slope_deg = slope
            surface = surf
            matched_profile = key
            break

    inferred_surface = _infer_surface_from_text(location_text)
    if inferred_surface != "tarmac":
        surface = inferred_surface

    friction = resolve_friction(surface)
    confidence = 0.6 if matched_profile != "default" else 0.3

    logger.info(
        f"Terrain fallback: '{matched_profile}' profile — "
        f"slope={slope_deg}°, surface={surface}, confidence={confidence}"
    )

    return TerrainResult(
        latitude=0.0, longitude=0.0, elevation_m=0.0,
        slope_degrees=slope_deg, road_surface=surface,
        friction_coefficient=friction,
        source="hardcoded_profile", confidence=confidence
    )


def _fetch_elevation_grid(lat: float, lng: float, radius_deg: float = 0.002) -> Optional[list]:
    params = {
        "demtype": "SRTMGL3",
        "south": lat - radius_deg, "north": lat + radius_deg,
        "west": lng - radius_deg, "east": lng + radius_deg,
        "outputFormat": "AAIGrid",
        "API_Key": OPENTOPO_API_KEY,
    }
    try:
        response = requests.get(OPENTOPO_BASE_URL, params=params, timeout=OPENTOPO_TIMEOUT)
        if response.status_code != 200:
            logger.warning(f"OpenTopography API returned {response.status_code}: {response.text[:100]}")
            return None

        elevations = []
        for line in response.text.strip().split('\n'):
            line = line.strip()
            if not line or line.lower().startswith(('ncols','nrows','xllcorner','yllcorner','cellsize','nodata')):
                continue
            for v in line.split():
                try:
                    elev = float(v)
                    if elev > -9999:
                        elevations.append(elev)
                except ValueError:
                    continue
        return elevations if len(elevations) >= 4 else None

    except requests.Timeout:
        logger.warning(f"OpenTopography API timeout after {OPENTOPO_TIMEOUT}s")
        return None
    except requests.RequestException as e:
        logger.warning(f"OpenTopography API request failed: {str(e)}")
        return None


def _calculate_slope_from_elevations(elevations: list, radius_deg: float = 0.002) -> float:
    if not elevations or len(elevations) < 2:
        return 1.0
    elev_change_m = max(elevations) - min(elevations)
    grid_width_m = radius_deg * 2 * 111000
    if grid_width_m <= 0:
        return 1.0
    return round(min(math.degrees(math.atan(elev_change_m / grid_width_m)), 45.0), 2)


def get_terrain(
    location_text: str = "",
    latitude: float = 0.0,
    longitude: float = 0.0,
) -> TerrainResult:
    """
    Main terrain lookup — 3-tier priority:
    1. Cache (GPS coordinates)
    2. OpenTopography API (GPS coordinates available)
    3. Hardcoded Kenyan profile (location text match) / default
    """
    has_gps = (latitude != 0.0 and longitude != 0.0)

    if has_gps:
        key = _cache_key(latitude, longitude)
        if key in _terrain_cache:
            cached = _terrain_cache[key]
            if _is_cache_valid(cached['timestamp']):
                logger.info(f"Terrain cache hit ({latitude:.3f}, {longitude:.3f}): slope={cached['slope_degrees']}°")
                surface = _infer_surface_from_text(location_text) if location_text else cached['road_surface']
                return TerrainResult(
                    latitude=latitude, longitude=longitude,
                    elevation_m=cached['elevation_m'],
                    slope_degrees=cached['slope_degrees'],
                    road_surface=surface,
                    friction_coefficient=resolve_friction(surface),
                    source="cache", confidence=0.85
                )

    if has_gps:
        logger.info(f"Fetching terrain from OpenTopography for ({latitude:.4f}, {longitude:.4f})")
        elevations = _fetch_elevation_grid(latitude, longitude)

        if elevations:
            slope_deg = _calculate_slope_from_elevations(elevations)
            elevation_m = sum(elevations) / len(elevations)
            surface = _infer_surface_from_text(location_text) if location_text else "tarmac"
            friction = resolve_friction(surface)

            _terrain_cache[_cache_key(latitude, longitude)] = {
                'elevation_m': elevation_m, 'slope_degrees': slope_deg,
                'road_surface': surface, 'timestamp': time.time()
            }

            logger.info(f"OpenTopography terrain: elevation={elevation_m:.0f}m, slope={slope_deg}°, surface={surface}")

            return TerrainResult(
                latitude=latitude, longitude=longitude,
                elevation_m=elevation_m, slope_degrees=slope_deg,
                road_surface=surface, friction_coefficient=friction,
                source="opentopography", confidence=0.90
            )
        else:
            logger.warning(f"OpenTopography failed ({latitude:.4f}, {longitude:.4f}) — falling back")

    result = _fallback_from_text(location_text)
    result.latitude = latitude
    result.longitude = longitude
    return result


def get_terrain_cache_stats() -> dict:
    valid = sum(1 for v in _terrain_cache.values() if _is_cache_valid(v['timestamp']))
    return {
        "total_cached": len(_terrain_cache),
        "valid_entries": valid,
        "expired_entries": len(_terrain_cache) - valid,
    }


def clear_terrain_cache():
    _terrain_cache.clear()
    logger.info("Terrain cache cleared")