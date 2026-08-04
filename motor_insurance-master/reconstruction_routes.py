
import logging
from datetime import datetime
from typing import Optional
 
from fastapi import APIRouter, HTTPException, Form, status
from pydantic import BaseModel
from database import db_manager
 
from pipeline_bridge import get_bridge, PhysicsInput
from physics_engine import get_engine
from terrain_service import get_terrain
from vehicle_registry import list_registry, registry_stats, get_vehicle_profile
 
logger = logging.getLogger(__name__)
 
reconstruction_router = APIRouter(
    prefix="/api/reconstruction",
    tags=["Reconstruction"]
)
 
 
class ReconstructionResponse(BaseModel):
    success: bool
    claim_id: str
    pathway: str
    physics_verdict: str
    physics_fraud_score: float
    simulation_method: str
    data_quality_score: float
    warnings: list
    result: dict
    timestamp: str
 
 
@reconstruction_router.post("/manual")
async def reconstruct_from_narrative(
    claim_id: str = Form(..., description="Existing claim ID e.g. CLM-2026-000022"),
    narrative: str = Form(..., description="Full incident narrative from member"),
    v1_make: Optional[str] = Form(None, description="Insured vehicle make e.g. Toyota"),
    v1_model: Optional[str] = Form(None, description="Insured vehicle model e.g. Premio"),
    v1_body_type: Optional[str] = Form(None, description="saloon / suv / pickup / matatu / motorcycle"),
    v1_stated_speed_kmh: float = Form(0.0, description="Stated speed of insured vehicle (0 = infer from narrative)"),
    v2_make: Optional[str] = Form(None, description="Third party vehicle make"),
    v2_model: Optional[str] = Form(None, description="Third party vehicle model"),
    v2_body_type: Optional[str] = Form(None, description="Third party body type"),
    v2_stated_speed_kmh: float = Form(0.0, description="Stated speed of third party vehicle"),
    crush_depth_mm: float = Form(0.0, description="Observed crush depth in mm (0 = infer from narrative)"),
    approach_angle_deg: float = Form(0.0, description="Collision angle: 180=head-on, 90=T-bone, 0=rear-end (0 = infer)"),
    location_text: str = Form("", description="Incident location e.g. Thika Road near Roysambu"),
    latitude: float = Form(0.0),
    longitude: float = Form(0.0),
    estimated_repair_cost: float = Form(0.0, description="Claimed repair cost in KES"),
):
    """
    **Run physics reconstruction from narrative + form data (no telemetry required)**
 
    Use this for all existing claims. The engine will:
    - Infer missing parameters from narrative text
    - Run McHenry crush energy analysis
    - Cross-reference stated speed vs physics-derived speed
    - Return a fraud score and verdict
 
    **Example:**
    ```
    claim_id: CLM-2026-000022
    narrative: "A matatu swerved into my lane on Thika Road doing about 60km/h..."
    v1_make: Toyota
    v1_model: Premio
    estimated_repair_cost: 285000
    ```
    """
    try:
        bridge = get_bridge()
        engine = get_engine()
 
        physics_input: PhysicsInput = bridge.process_manual_payload(
            claim_id=claim_id,
            narrative=narrative,
            v1_make=v1_make or "",
            v1_model=v1_model or "",
            v1_body_type=v1_body_type or "",
            v2_make=v2_make or "",
            v2_model=v2_model or "",
            v2_body_type=v2_body_type or "",
            v1_stated_speed_kmh=v1_stated_speed_kmh,
            v2_stated_speed_kmh=v2_stated_speed_kmh,
            crush_depth_mm=crush_depth_mm,
            approach_angle_deg=approach_angle_deg,
            location_text=location_text,
            latitude=latitude,
            longitude=longitude,
            estimated_repair_cost=estimated_repair_cost,
        )
 
        result = engine.reconstruct(
            claim_id=claim_id,
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
            estimated_repair_cost=estimated_repair_cost,
            location_text=location_text,
            latitude=latitude,
            longitude=longitude,
            pathway="pathway_2",
        )
 
        return {
            "success": True,
            "claim_id": claim_id,
            "pathway": "pathway_2",
            "physics_verdict": result.physics_verdict,
            "physics_fraud_score": result.physics_fraud_score,
            "simulation_method": result.simulation_method,
            "data_quality_score": physics_input.data_quality_score,
            "warnings": physics_input.warnings,
            "result": result.to_dict(),
            "timestamp": datetime.now().isoformat()
        }
 
    except Exception as e:
        logger.error(f"Reconstruction error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
 
 
@reconstruction_router.post("/telemetry")
async def reconstruct_from_telemetry(
    payload_json: str = Form(..., description="Full telemetry JSON payload from Flutter app"),
    device_id: Optional[str] = Form(None, description="Device ID for HMAC validation"),
    signature: Optional[str] = Form(None, description="HMAC-SHA256 signature from Flutter app"),
):
    """
    **Run physics reconstruction from Flutter telemetry (Pathway 1)**
 
    Accepts the full sensor payload from the Flutter edge app.
    Validates HMAC signature for chain of custody.
    Uses GPS speed + accelerometer data for precise reconstruction.
 
    This endpoint is ready for when the Flutter app is deployed.
    """
    try:
        raw_payload = json.loads(payload_json)
        claim_id = raw_payload.get("claim_id", "")
        if not claim_id:
            raise HTTPException(status_code=400, detail="claim_id required in payload")
 
        bridge = get_bridge()
        engine = get_engine()
 
        physics_input = bridge.process_telemetry_payload(
            raw_payload=raw_payload,
            device_id=device_id,
            received_signature=signature,
        )
 
        result = engine.reconstruct(
            claim_id=claim_id,
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
        )
 
        return {
            "success": True,
            "claim_id": claim_id,
            "pathway": physics_input.pathway,
            "hmac_valid": physics_input.hmac_valid,
            "physics_verdict": result.physics_verdict,
            "physics_fraud_score": result.physics_fraud_score,
            "simulation_method": result.simulation_method,
            "data_quality_score": physics_input.data_quality_score,
            "warnings": physics_input.warnings,
            "result": result.to_dict(),
            "timestamp": datetime.now().isoformat()
        }
 
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")
    except Exception as e:
        logger.error(f"Telemetry reconstruction error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
 
 
@reconstruction_router.get("/verdict/{claim_id}")
async def get_physics_verdict(claim_id: str):
    try:
        import json
        with db_manager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT physics_fraud_score, physics_verdict,
                       physics_result, reconstruction_pathway
                FROM claims WHERE claim_id = ?
            ''', (claim_id,))
            row = cursor.fetchone()
            if not row or not row["physics_verdict"] or row["physics_verdict"] == "NOT_RUN":
                return {"success": False, "message": f"Physics not yet run for {claim_id}"}
            return {
                "success": True,
                "claim_id": claim_id,
                "physics_fraud_score": row["physics_fraud_score"],
                "physics_verdict": row["physics_verdict"],
                "reconstruction_pathway": row["reconstruction_pathway"],
                "result": json.loads(row["physics_result"] or "{}")
            }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
 
 
@reconstruction_router.get("/vehicle-registry")
async def get_vehicle_registry():
    """
    **List all vehicles in the Kenyan physics registry**
 
    Shows make, model, body type, kerb weight, and data confidence.
    All entries marked as DUMMY pending Old Mutual fleet submission.
    """
    return {
        "success": True,
        "stats": registry_stats(),
        "vehicles": list_registry()
    }
 
 
@reconstruction_router.get("/vehicle-lookup")
async def lookup_vehicle(
    make: str,
    model: str,
    body_type: Optional[str] = None
):
    """
    **Look up physics profile for a specific vehicle**
 
    Use this to verify what profile the engine will use for a given claim vehicle.
    """
    profile, method = get_vehicle_profile(make, model, body_type)
    return {
        "success": True,
        "lookup_method": method,
        "profile": profile.to_dict()
    }
 
 
@reconstruction_router.get("/health")
async def reconstruction_health():
    """Health check for reconstruction engine"""
    try:
        engine = get_engine()
        return {
            "status": "operational",
            "pybullet_available": engine.use_pybullet,
            "simulation_mode": "pybullet" if engine.use_pybullet else "analytical_newton_euler",
            "vehicle_registry_size": len(list_registry()),
            "timestamp": datetime.now().isoformat()
        }
    except Exception as e:
        return {
            "status": "error",
            "error": str(e)
        }
 