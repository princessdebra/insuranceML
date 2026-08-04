
import sqlite3
from pathlib import Path
from typing import Optional
 
DB_PATH = Path("claims_database.db")
 
DEFAULT_MU = 0.45
 
 
class RegistryAdapter:
 
    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
 
    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn
 
    def get_vehicle_spec(
        self,
        model_identity: str,
        body_type: Optional[str] = None
    ) -> dict:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM vehicle_registry WHERE registry_key = ?",
                (model_identity.lower(),)
            ).fetchone()
 
            if row:
                return self._build_spec(conn, row, "exact_match")
 
            row = conn.execute(
                "SELECT * FROM vehicle_registry WHERE registry_key LIKE ?",
                (f"%{model_identity.lower()}%",)
            ).fetchone()
 
            if row:
                return self._build_spec(conn, row, "partial_match")
 
            if body_type:
                row = conn.execute(
                    """
                    SELECT * FROM vehicle_registry
                    WHERE body_type = ? AND registry_key LIKE 'generic%'
                    LIMIT 1
                    """,
                    (body_type.lower(),)
                ).fetchone()
 
                if row:
                    return self._build_spec(conn, row, "body_type_fallback")
 
                row = conn.execute(
                    "SELECT * FROM vehicle_registry WHERE body_type = ? LIMIT 1",
                    (body_type.lower(),)
                ).fetchone()
 
                if row:
                    return self._build_spec(conn, row, "body_type_fallback")
 
            row = conn.execute(
                "SELECT * FROM vehicle_registry WHERE body_type = 'saloon' LIMIT 1"
            ).fetchone()
 
            logger.warning(
                f"No registry match for '{model_identity}' — using generic saloon fallback"
            )
            return self._build_spec(conn, row, "generic_fallback")
 
    def _build_spec(
        self,
        conn: sqlite3.Connection,
        row: sqlite3.Row,
        lookup_method: str
    ) -> dict:
        engine_class = self._resolve_engine_class(conn, row["body_type"])
        effective_mass = row["kerb_weight_kg"] + row["pax_load_kg"]
 
        return {
            "kenyan_operational_mass_kg": effective_mass,
            "class": engine_class,
            "crumple_A": row["crumple_A"],
            "crumple_B": row["crumple_B"],
            "length_mm": row["length_mm"],
            "width_mm": row["width_mm"],
            "height_mm": row["height_mm"],
            "wheelbase_mm": row["wheelbase_mm"],
            "cog_height_ratio": row["cog_height_ratio"],
            "has_abs": bool(row["has_abs"]),
            "airbag_count": row["airbag_count"],
            "drive_type": row["drive_type"],
            "crumple_zone": row["crumple_zone"],
            "registry_key": row["registry_key"],
            "make": row["make"],
            "model": row["model"],
            "body_type": row["body_type"],
            "confidence": row["confidence"],
            "data_source": row["data_source"],
            "lookup_method": lookup_method,
        }
 
    def _resolve_engine_class(self, conn: sqlite3.Connection, body_type: str) -> str:
        row = conn.execute(
            "SELECT engine_class FROM vehicle_class_mapping WHERE body_type = ?",
            (body_type.lower(),)
        ).fetchone()
        if row:
            return row["engine_class"]
        logger.warning(f"No class mapping for body_type '{body_type}' — defaulting to saloon_station_wagon")
        return "saloon_station_wagon"
 
    def get_friction_mu(self, road_type: str, weather: str) -> dict:
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT * FROM friction_mu_lookup
                WHERE road_type = ? AND weather = ?
                """,
                (road_type.lower(), weather.lower())
            ).fetchone()
 
            if row:
                return {
                    "mu": row["mu_value"],
                    "road_type": row["road_type"],
                    "weather": row["weather"],
                    "notes": row["notes"],
                    "lookup_method": "exact",
                }
 
            row = conn.execute(
                """
                SELECT * FROM friction_mu_lookup
                WHERE road_type = ? AND weather = 'wet'
                """,
                (road_type.lower(),)
            ).fetchone()
 
            if row:
                logger.warning(
                    f"No mu for ({road_type}, {weather}) — using ({road_type}, wet) fallback"
                )
                return {
                    "mu": row["mu_value"],
                    "road_type": road_type,
                    "weather": weather,
                    "notes": f"Fallback from ({road_type}, wet)",
                    "lookup_method": "road_type_only",
                }
 
            logger.warning(
                f"No mu for ({road_type}, {weather}) — using default {DEFAULT_MU}"
            )
            return {
                "mu": DEFAULT_MU,
                "road_type": road_type,
                "weather": weather,
                "notes": f"Default fallback — no DB entry for ({road_type}, {weather})",
                "lookup_method": "default",
            }
 
    def resolve_payload(self, raw_payload: dict) -> dict:
        enriched = dict(raw_payload)
 
        env = enriched.get("environment_snapshot", {})
        road_type = env.get("road_type", "asphalt")
        weather = env.get("weather", "dry")
 
        mu_result = self.get_friction_mu(road_type, weather)
        enriched["environment_snapshot"] = {
            **env,
            "resolved_friction_mu": mu_result["mu"],
            "mu_lookup_method": mu_result["lookup_method"],
            "mu_notes": mu_result["notes"],
        }
 
        for vehicle in enriched.get("vehicles", []):
            model_identity = vehicle.get("model_identity", "")
            body_type = vehicle.get("body_type")
            spec = self.get_vehicle_spec(model_identity, body_type)
            vehicle["resolved_spec"] = spec
 
        return enriched
 
    def list_vehicles(self) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT registry_key, make, model, body_type, confidence FROM vehicle_registry"
            ).fetchall()
        return [dict(r) for r in rows]
 
    def list_mu_table(self) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT road_type, weather, mu_value, notes FROM friction_mu_lookup ORDER BY road_type, weather"
            ).fetchall()
        return [dict(r) for r in rows]
 