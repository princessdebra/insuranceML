
from fastapi import APIRouter, HTTPException, Depends, File, UploadFile, Form, status, Query, BackgroundTasks
from fastapi.responses import JSONResponse, FileResponse
from typing import List, Dict, Any, Optional
from datetime import datetime
import asyncio
import logging
import json
import hashlib

from schemas import (
    ClaimSubmissionSchema,
    AnalysisResultSchema,
    SystemStatsSchema,
    ErrorResponseSchema,
    HealthCheckSchema,
    ClaimsListResponseSchema,
    PhotoAnomalySchema,
    NarrativeAnalysisSchema,
    DatabaseStatusSchema,
    ProcessingLogSchema,
    MaintenanceResponseSchema,
    DetailedMetricsSchema
)
from service import ClaimOrchestrator, TEXT_REASONING_MODEL
from ollama_client import generate_json
from utils import ValidationUtils, generate_unique_id
from database import db_manager
import ai_rol
import devserver_watchdog
import os
 
logger = logging.getLogger(__name__)
 
claims_router = APIRouter(prefix="/api/claims", tags=["Claims"])
analysis_router = APIRouter(prefix="/api/analysis", tags=["Analysis"])
system_router = APIRouter(prefix="/api/system", tags=["System"])
 
def get_claim_orchestrator() -> ClaimOrchestrator:
    return ClaimOrchestrator()


# ─── Background analysis workers ─────────────────────────────────────────
# The full AI chain (photo analysis, narrative analysis, cross-party check,
# physics reconstruction, video render) is the slow part of every submission
# — routinely several minutes under shared-GPU load. Nothing the client
# actually sees in the acknowledgment response depends on it (fraud data is
# deliberately hidden from submitters), so it runs here, after the response
# has already gone out, instead of blocking the request.

def build_structured_intake_block(
    third_party_involved: Optional[str] = None,
    third_party_details: Optional[str] = None,
    third_party_fled: Optional[str] = None,
    police_reported: Optional[str] = None,
    police_ob_number: Optional[str] = None,
    witnesses_present: Optional[str] = None,
    witness_details: Optional[str] = None,
    injuries_reported: Optional[str] = None,
    injury_details: Optional[str] = None,
) -> str:
    """
    Turn the intake wizard's explicit structured answers (third party
    involvement, police report, witnesses, injuries) into a clearly-labeled
    block prepended to the claimant's narrative.

    These facts used to only exist if the claimant happened to mention them
    in free text -- which meant the Kenya-specific fraud-pattern checks in
    Narrative Intelligence (fled scene + plate recorded, police OB same day,
    witness present in a chaotic hit-and-run, etc.) were only ever guessing
    from prose. Deliberately asking for them and feeding them back in as an
    explicit, labeled block means every downstream capability (narrative
    extraction, physics third-party vehicle inference, cross-validation) sees
    them as claimant-confirmed facts rather than hoping they're in the story.
    """
    lines = []

    if third_party_involved == "Yes":
        lines.append(f"Third-party vehicle/party involved: {third_party_details or 'details not provided'}")
        lines.append(f"Third party fled the scene before details were exchanged: {third_party_fled or 'not specified'}")
    else:
        lines.append("Single-vehicle incident — no third party involved")

    if police_reported == "Yes":
        lines.append(f"Reported to police: Yes (OB/Abstract number: {police_ob_number or 'not provided'})")
    else:
        lines.append("Reported to police: No")

    if witnesses_present == "Yes":
        lines.append(f"Witnesses present: Yes ({witness_details or 'details not provided'})")
    else:
        lines.append("Witnesses present: No")

    if injuries_reported == "Yes":
        lines.append(f"Injuries reported: Yes ({injury_details or 'details not provided'})")
    else:
        lines.append("Injuries reported: No")

    return "STRUCTURED INTAKE FACTS (claimant-confirmed at submission, not free text):\n" + "\n".join(f"- {l}" for l in lines)


async def _process_member_claim_background(
    claim_id: str,
    clean_narrative: str,
    photo_data: list,
    estimated_cost: float,
    location: str,
):
    try:
        orchestrator = get_claim_orchestrator()
        analysis_result = await orchestrator.analyze_multiparty_claim(
            claim_id=claim_id,
            member_narrative=clean_narrative,
            member_photos=photo_data,
            estimated_cost=estimated_cost,
            location=location
        )

        with db_manager.get_connection() as conn:
            conn.execute(
                "UPDATE claims SET simulation_video_path = ? WHERE claim_id = ?",
                (analysis_result.get("simulation_video_path"), claim_id)
            )
            conn.commit()

        logger.info(
            f"[background] Member analysis complete for {claim_id} "
            f"(Risk: {analysis_result['fraud_risk_score']}/100 - hidden from member)"
        )
    except Exception as e:
        logger.error(f"[background] Member analysis failed for {claim_id}: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())


async def _process_assessor_analysis_background(
    claim_id: str,
    member_narrative: str,
    member_photo_data: list,
    clean_report: str,
    assessor_photo_data: list,
    estimated_cost: float,
    location: str,
):
    try:
        orchestrator = get_claim_orchestrator()
        analysis_result = await orchestrator.analyze_multiparty_claim(
            claim_id=claim_id,
            member_narrative=member_narrative,
            member_photos=member_photo_data,
            assessor_report=clean_report,
            assessor_photos=assessor_photo_data,
            estimated_cost=estimated_cost,
            location=location
        )
        db_manager.store_claim(analysis_result)
        logger.info(
            f"[background] Assessor analysis complete for {claim_id} "
            f"(Risk: {analysis_result['fraud_risk_score']}/100 - hidden from assessor)"
        )
    except Exception as e:
        logger.error(f"[background] Assessor analysis failed for {claim_id}: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())


async def _process_repairshop_analysis_background(
    claim_id: str,
    member_narrative: str,
    member_photo_data: list,
    assessor_report: str,
    assessor_photo_data: list,
    clean_estimate: str,
    repair_photo_data: list,
    total_cost: float,
    location: str,
):
    try:
        orchestrator = get_claim_orchestrator()
        analysis_result = await orchestrator.analyze_multiparty_claim(
            claim_id=claim_id,
            member_narrative=member_narrative,
            member_photos=member_photo_data,
            assessor_report=assessor_report,
            assessor_photos=assessor_photo_data,
            repair_estimate=clean_estimate,
            repair_photos=repair_photo_data,
            estimated_cost=total_cost,
            location=location
        )
        db_manager.store_claim(analysis_result)
        logger.info(
            f"[background] FINAL analysis complete for {claim_id} "
            f"(Risk: {analysis_result['fraud_risk_score']}/100 - hidden from repair shop)"
        )
    except Exception as e:
        logger.error(f"[background] Repair shop analysis failed for {claim_id}: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())


@analysis_router.post("/intake/assess-narrative")
async def assess_narrative_completeness(
    narrative: str = Form(..., description="Claimant's narrative so far"),
    claim_type: str = Form("", description="motor / marine / domestic"),
    round: int = Form(0, description="How many clarification rounds already asked this claim"),
):
    """
    Agentic Claims Intake: assess whether the claimant's narrative so far
    gives an assessor enough detail to work with and, if not, generate a
    single natural follow-up question tailored to what's actually missing.

    This is what makes intake "agentic" rather than a static form -- the
    next question the claimant sees depends on what they just said, not a
    fixed script. Capped at 2 clarification rounds so it can't loop
    indefinitely, and fails open (never blocks submission) if the model
    call itself fails.
    """
    if round >= 2:
        return {"sufficient": True, "clarifying_question": None, "missing_aspect": None}

    prompt = f"""
You are assisting with First Notification of Loss (FNOL) intake for a {claim_type or "motor"} insurance claim.

Claimant's narrative so far:
"{narrative}"

Note: the incident date and general location have ALREADY been captured separately earlier in
this conversation -- do not ask about those. Focus only on the narrative itself: how the incident
actually occurred (the sequence of events / mechanism), what damage resulted, and who or what
else was involved (other vehicle, pedestrian, object, etc).

Assess whether this narrative gives an assessor enough detail on those points to work with.
A single vague sentence (e.g. "car accident happened", "someone hit my car") is NOT sufficient.
A narrative that already describes how the collision happened and what was damaged IS sufficient,
even if it omits precise street names or exact addresses.

Respond in JSON only:
{{
    "sufficient": true/false,
    "missing_aspect": "the single most important missing detail (not date or location), or null if sufficient",
    "clarifying_question": "one natural, conversational follow-up question about that one missing aspect, or null if sufficient"
}}

Ask about only ONE missing aspect at a time, phrased the way a helpful human intake agent would
(e.g. "Could you tell me a bit more about how the two vehicles actually collided?"), not clinically
(e.g. "Please specify the impact mechanism.").
"""
    try:
        result = await asyncio.to_thread(
            generate_json, prompt, model=TEXT_REASONING_MODEL, timeout=60, retries=1,
        )
        return {
            "sufficient": bool(result.get("sufficient", True)),
            "clarifying_question": result.get("clarifying_question"),
            "missing_aspect": result.get("missing_aspect"),
        }
    except Exception as e:
        logger.warning(f"Narrative sufficiency check failed: {e} — defaulting to sufficient")
        return {"sufficient": True, "clarifying_question": None, "missing_aspect": None}


@analysis_router.post("/member")
async def submit_member_claim(
    background_tasks: BackgroundTasks,
    claim_id: str = Form(..., description="Unique claim identifier"),
    member_id: str = Form(..., description="Member/policy holder ID"),
    narrative: str = Form(..., description="Member's description of the accident"),
    estimated_cost: float = Form(..., description="Member's estimated repair cost"),
    location: str = Form(..., description="Accident location"),
    incident_date: str = Form(..., description="Date of incident (YYYY-MM-DD)"),
    photos: List[UploadFile] = File(..., description="Photos from member"),
    third_party_involved: Optional[str] = Form(None),
    third_party_details: Optional[str] = Form(None),
    third_party_fled: Optional[str] = Form(None),
    police_reported: Optional[str] = Form(None),
    police_ob_number: Optional[str] = Form(None),
    witnesses_present: Optional[str] = Form(None),
    witness_details: Optional[str] = Form(None),
    injuries_reported: Optional[str] = Form(None),
    injury_details: Optional[str] = Form(None),
):
    """
    **Member submits initial claim**

    Simple submission - no fraud analysis shown to member.
    Returns only acknowledgment and next steps.
    """
    try:
        logger.info(f"Member submission received for claim {claim_id}")
 
        if not claim_id.startswith('CLM-'):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid claim ID format. Expected: CLM-YYYY-XXXXXX"
            )
 
        photo_data = []
        stored_count = 0
 
        for photo in photos:
            if not photo.content_type.startswith('image/'):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"File {photo.filename} is not a valid image"
                )
 
            content = await photo.read()
 
            if len(content) > 10 * 1024 * 1024:
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail=f"File {photo.filename} exceeds 10MB limit"
                )
 
            try:
                db_manager.store_photo_file(
                    claim_id=claim_id,
                    filename=photo.filename,
                    party='member',
                    file_data=content,
                    content_type=photo.content_type
                )
                stored_count += 1
                logger.info(f"Stored member photo: {photo.filename}")
            except Exception as e:
                logger.error(f"Failed to store photo {photo.filename}: {str(e)}")
 
            photo_data.append((content, photo.filename))
 
        clean_narrative = ValidationUtils.sanitize_narrative(narrative)
        structured_block = build_structured_intake_block(
            third_party_involved, third_party_details, third_party_fled,
            police_reported, police_ob_number,
            witnesses_present, witness_details,
            injuries_reported, injury_details,
        )
        enriched_narrative = f"{structured_block}\n\nCLAIMANT NARRATIVE:\n{clean_narrative}"

        # member_id doesn't depend on the AI analysis at all — write it now
        # rather than waiting for the background task, so the claim record
        # is correct immediately instead of appearing unlinked for minutes.
        with db_manager.get_connection() as conn:
            conn.execute(
                "UPDATE claims SET member_id = ? WHERE claim_id = ?",
                (member_id, claim_id)
            )
            conn.commit()

        # The AI chain (photo/narrative analysis, physics, video) routinely
        # takes minutes under shared-GPU load. The member never sees any of
        # its output (fraud data is deliberately hidden from submitters), so
        # it runs in the background — the acknowledgment below doesn't wait.
        background_tasks.add_task(
            _process_member_claim_background,
            claim_id, enriched_narrative, photo_data, estimated_cost, location
        )

        logger.info(f"Member submission accepted for {claim_id} — analysis running in background")

        return {
            "success": True,
            "claim_id": claim_id,
            "message": "Your claim has been submitted successfully",
            "submission_details": {
                "photos_uploaded": len(photo_data),
                "estimated_cost": f"KES {estimated_cost:,.2f}",
                "location": location,
                "incident_date": incident_date
            },
            "next_steps": [
                "Your claim is being reviewed",
                "An assessor will be assigned within 24-48 hours",
                "You will be notified once the assessment is scheduled",
                "Expected timeline: 5-7 business days"
            ],
            "status": "SUBMITTED",
            "submitted_at": datetime.now().isoformat(),
            "reference_number": claim_id
        }
 
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error processing member submission: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error processing submission: {str(e)}"
        )
 
 
@analysis_router.post("/assessor")
async def submit_assessor_report(
    background_tasks: BackgroundTasks,
    claim_id: str = Form(..., description="Claim ID"),
    assessor_id: str = Form(..., description="Assessor ID"),
    damage_report: str = Form(..., description="Detailed damage assessment"),
    estimated_cost: float = Form(..., description="Assessor's estimated repair cost"),
    inspection_date: str = Form(..., description="Date of inspection"),
    photos: List[UploadFile] = File(..., description="Photos from assessor inspection")
):
    """
    **Assessor submits damage assessment**
 
    Simple submission - no fraud analysis shown to assessor.
    Returns only acknowledgment and next steps.
    """
    try:
        logger.info(f"Assessor submission received for claim {claim_id}")
 
        existing_claim = db_manager.get_claim(claim_id)
        if not existing_claim:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Claim {claim_id} not found"
            )
 
        assessor_photo_data = []
        stored_count = 0
 
        for photo in photos:
            if not photo.content_type.startswith('image/'):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"File {photo.filename} is not a valid image"
                )
 
            content = await photo.read()
 
            if len(content) > 10 * 1024 * 1024:
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail=f"File {photo.filename} exceeds 10MB limit"
                )
 
            file_hash = hashlib.md5(content).hexdigest()
 
            try:
                db_manager.store_photo_file(
                    claim_id=claim_id,
                    filename=photo.filename,
                    party='assessor',
                    file_data=content,
                    file_hash=file_hash,
                    content_type=photo.content_type
                )
                stored_count += 1
                logger.info(f"Stored assessor photo: {photo.filename}")
            except Exception as e:
                logger.error(f"Failed to store photo {photo.filename}: {str(e)}")
 
            assessor_photo_data.append((content, photo.filename))
 
        member_photos_from_db = db_manager.get_photos_by_claim_and_party(claim_id, 'member')
        member_photo_data = [(p['file_data'], p['filename']) for p in member_photos_from_db]
 
        analysis_data = existing_claim.get('analysis_result')
        member_narrative = ''
        location = existing_claim.get('location', '')
 
        if analysis_data:
            try:
                if isinstance(analysis_data, str):
                    analysis_data = json.loads(analysis_data)
                member_submission = analysis_data.get('member_submission', {})
                member_narrative = member_submission.get('narrative', '')
                location = analysis_data.get('location', location)
            except (json.JSONDecodeError, AttributeError) as e:
                logger.error(f"Error parsing analysis_result: {e}")
 
        clean_report = ValidationUtils.sanitize_narrative(damage_report)

        background_tasks.add_task(
            _process_assessor_analysis_background,
            claim_id, member_narrative, member_photo_data, clean_report,
            assessor_photo_data, estimated_cost, location
        )

        logger.info(f"Assessor submission accepted for {claim_id} — analysis running in background")

        return {
            "success": True,
            "claim_id": claim_id,
            "message": "Your assessment has been submitted successfully",
            "submission_details": {
                "photos_uploaded": len(assessor_photo_data),
                "estimated_cost": f"KES {estimated_cost:,.2f}",
                "inspection_date": inspection_date
            },
            "next_steps": [
                "Your assessment is being reviewed",
                "Repair shop will provide estimate",
                "Final decision will be communicated within 3-5 business days"
            ],
            "status": "ASSESSMENT_SUBMITTED",
            "submitted_at": datetime.now().isoformat(),
            "reference_number": claim_id
        }
 
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error processing assessor submission: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error processing submission: {str(e)}"
        )
 
 
@analysis_router.post("/repair-shop")
async def submit_repair_shop_estimate(
    background_tasks: BackgroundTasks,
    claim_id: str = Form(..., description="Claim ID"),
    shop_id: str = Form(..., description="Repair shop ID"),
    repair_estimate: str = Form(..., description="Detailed repair estimate breakdown"),
    total_cost: float = Form(..., description="Total estimated repair cost"),
    estimate_date: str = Form(..., description="Date of estimate"),
    photos: List[UploadFile] = File(..., description="Photos from repair shop")
):
    """
    **Repair shop submits estimate**
 
    Simple submission - no fraud analysis shown to repair shop.
    Returns only acknowledgment and next steps.
    """
    try:
        logger.info(f"Repair shop submission received for claim {claim_id}")
 
        existing_claim = db_manager.get_claim(claim_id)
        if not existing_claim:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Claim {claim_id} not found"
            )
 
        repair_photo_data = []
        stored_count = 0
 
        for photo in photos:
            if not photo.content_type.startswith('image/'):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"File {photo.filename} is not a valid image"
                )
 
            content = await photo.read()
 
            if len(content) > 10 * 1024 * 1024:
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail=f"File {photo.filename} exceeds 10MB limit"
                )
 
            file_hash = hashlib.md5(content).hexdigest()
 
            try:
                db_manager.store_photo_file(
                    claim_id=claim_id,
                    filename=photo.filename,
                    party='repair_shop',
                    file_data=content,
                    file_hash=file_hash,
                    content_type=photo.content_type
                )
                stored_count += 1
                logger.info(f"Stored repair shop photo: {photo.filename}")
            except Exception as e:
                logger.error(f"Failed to store photo {photo.filename}: {str(e)}")
 
            repair_photo_data.append((content, photo.filename))
 
        all_photos = db_manager.get_all_photos_by_claim(claim_id)
        member_photo_data = [(p['file_data'], p['filename']) for p in all_photos['member']]
        assessor_photo_data = [(p['file_data'], p['filename']) for p in all_photos['assessor']]
 
        analysis_data = {}
        member_narrative = ''
        assessor_report = ''
        location = existing_claim.get('location', '')
 
        analysis_result_json = existing_claim.get('analysis_result')
        if analysis_result_json:
            try:
                if isinstance(analysis_result_json, str):
                    analysis_data = json.loads(analysis_result_json)
                else:
                    analysis_data = analysis_result_json
 
                member_submission = analysis_data.get('member_submission', {})
                member_narrative = member_submission.get('narrative', '')
 
                assessor_submission = analysis_data.get('assessor_submission', {})
                if assessor_submission:
                    assessor_report = assessor_submission.get('report', '')
 
                location = analysis_data.get('location', location)
            except (json.JSONDecodeError, AttributeError) as e:
                logger.error(f"Error parsing analysis_result: {e}")
 
        clean_estimate = ValidationUtils.sanitize_narrative(repair_estimate)

        background_tasks.add_task(
            _process_repairshop_analysis_background,
            claim_id, member_narrative, member_photo_data, assessor_report,
            assessor_photo_data, clean_estimate, repair_photo_data, total_cost, location
        )

        logger.info(f"Repair shop submission accepted for {claim_id} — final analysis running in background")

        return {
            "success": True,
            "claim_id": claim_id,
            "message": "Your repair estimate has been submitted successfully",
            "submission_details": {
                "photos_uploaded": len(repair_photo_data),
                "estimated_cost": f"KES {total_cost:,.2f}",
                "estimate_date": estimate_date
            },
            "next_steps": [
                "All submissions received - claim under final review",
                "Insurance team will process the claim",
                "Decision will be communicated within 2-3 business days",
                "You will be notified of approval or further requirements"
            ],
            "status": "ESTIMATE_SUBMITTED",
            "all_submissions_complete": True,
            "submitted_at": datetime.now().isoformat(),
            "reference_number": claim_id
        }
 
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error processing repair shop submission: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error processing submission: {str(e)}"
        )
 
 
@analysis_router.get("/claims/{claim_id}/simulation-video")
async def get_simulation_video(claim_id: str):
    """
    **Retrieve the physics simulation video for a claim**
 
    Internal/assessor use only — not exposed to members.
    Returns the MP4 file if it has been generated, otherwise 404.
    """
    with db_manager.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT simulation_video_path, physics_fraud_score, physics_verdict "
            "FROM claims WHERE claim_id = ?",
            (claim_id,)
        )
        row = cursor.fetchone()
 
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Claim {claim_id} not found")
 
    video_path = row["simulation_video_path"]
 
    if not video_path:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"No simulation video available for {claim_id}. "
                f"Either physics reconstruction has not run yet, "
                f"or video rendering failed for this claim."
            )
        )
 
    if not os.path.exists(video_path):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Video file missing on disk for {claim_id} (path: {video_path})"
        )
 
    return FileResponse(
        path=video_path,
        media_type="video/mp4",
        filename=f"collision_simulation_{claim_id}.mp4",
    )
 
 
@analysis_router.get("/claims/{claim_id}/simulation-status")
async def get_simulation_status(claim_id: str):
    """
    **Check whether a simulation video is ready, without downloading it**
 
    Useful for polling from an assessor dashboard while physics runs async.
    """
    with db_manager.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT simulation_video_path, physics_fraud_score, physics_verdict "
            "FROM claims WHERE claim_id = ?",
            (claim_id,)
        )
        row = cursor.fetchone()
 
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Claim {claim_id} not found")
 
    video_path = row["simulation_video_path"]
    video_ready = bool(video_path and os.path.exists(video_path))
 
    return {
        "claim_id": claim_id,
        "video_ready": video_ready,
        "video_url": f"/claims/{claim_id}/simulation-video" if video_ready else None,
        "physics_fraud_score": row["physics_fraud_score"],
        "physics_verdict": row["physics_verdict"],
    }
 
 
@analysis_router.get("/claim/{claim_id}/full-report")
async def get_full_fraud_analysis(
    claim_id: str,
    include_timeline: bool = Query(False, description="Include full animation timeline frames (39 frames per claim)"),
):
    """
    **ADMIN ONLY: Get complete fraud analysis for a claim**
 
    Returns comprehensive fraud detection results including:
    - Cross-party verification
    - All fraud indicators
    - Risk breakdown
    - Final decision recommendation
 
    This endpoint is for administrators and SIU investigators only.
    """
    try:
        logger.info(f"Admin requesting full fraud analysis for {claim_id}")
 
        claim = db_manager.get_claim(claim_id)
        if not claim:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Claim {claim_id} not found")
 
        analysis_result = claim.get('analysis_result')
        if isinstance(analysis_result, str):
            analysis_result = json.loads(analysis_result)
 
        risk_score      = analysis_result.get('fraud_risk_score', 0)
        estimated_cost  = analysis_result.get('estimated_cost', 0)
        location        = analysis_result.get('location', '')
        cross_party     = analysis_result.get('cross_party_verification', {})
        photo_analysis  = analysis_result.get('photo_analysis', {})
        risk_scoring    = analysis_result.get('risk_scoring', {})
        physics_summary = analysis_result.get('physics_reconstruction', {})
 
        all_anomalies = []
        for photo_result in photo_analysis.get('results', []):
            all_anomalies.extend(photo_result.get('anomalies', []))
 
        all_inconsistencies = []
        if 'member_submission' in analysis_result:
            member_na = analysis_result['member_submission'].get('narrative_analysis', {})
            all_inconsistencies.extend(member_na.get('inconsistencies', []))
        if analysis_result.get('assessor_submission'):
            assessor_na = analysis_result['assessor_submission'].get('analysis', {})
            if assessor_na:
                all_inconsistencies.extend(assessor_na.get('inconsistencies', []))
        if analysis_result.get('repair_shop_submission'):
            repair_na = analysis_result['repair_shop_submission'].get('analysis', {})
            if repair_na:
                all_inconsistencies.extend(repair_na.get('inconsistencies', []))
 
        critical_count      = len([a for a in all_anomalies if a.get('severity') == 'critical'])
        high_count          = len([a for a in all_anomalies if a.get('severity') == 'high'])
        physics_verdict     = physics_summary.get('physics_verdict') if physics_summary else None
        physics_fraud_score = physics_summary.get('physics_fraud_score', 0) if physics_summary else 0
 
        if critical_count > 0:
            final_decision  = "INVESTIGATE_FURTHER"
            decision_reason = f"CRITICAL FRAUD INDICATORS ({critical_count} critical issues)"
        elif physics_verdict == "INCONSISTENT" or physics_fraud_score >= 45:
            final_decision  = "INVESTIGATE_FURTHER"
            decision_reason = (
                f"Physics reconstruction flagged significant inconsistencies "
                f"(score: {physics_fraud_score}/100, verdict: {physics_verdict}). "
                f"Manual review required before payment."
            )
        elif physics_verdict == "SUSPICIOUS":
            final_decision  = "INVESTIGATE_FURTHER"
            decision_reason = (
                f"Minor physics inconsistencies detected (score: {physics_fraud_score}/100). "
                f"Approve only after mandatory assessor review."
            )
        elif high_count >= 2:
            final_decision  = "INVESTIGATE_FURTHER"
            decision_reason = f"Multiple HIGH-RISK indicators ({high_count} issues)"
        elif risk_score >= 75:
            final_decision  = "DECLINE_CLAIM"
            decision_reason = "High fraud risk detected"
        elif risk_score >= 50:
            final_decision  = "INVESTIGATE_FURTHER"
            decision_reason = "Medium fraud risk - SIU investigation required"
        else:
            final_decision  = "APPROVE_CLAIM"
            decision_reason = "Low fraud risk - proceed with payment"
 
        timeline      = physics_summary.get('timeline') if physics_summary else None
        timeline_meta = timeline.get('simulation_metadata') if isinstance(timeline, dict) else None
 
        return {
            "claim_id":           claim_id,
            "analysis_timestamp": analysis_result.get('timestamp'),
            "estimated_cost":     estimated_cost,
            "location":           location,
            "final_assessment": {
                "decision":         final_decision,
                "decision_reason":  decision_reason,
                "fraud_risk_score": risk_score,
                "risk_level":       analysis_result.get('risk_level', 'unknown'),
                "estimated_cost":   estimated_cost,
                "location":         location,
            },
            "parties_analyzed": analysis_result.get('parties_analyzed', {}),
            "cross_party_verification": {
                "inconsistencies_found":     cross_party.get('inconsistencies_found', False),
                "inconsistency_count":       cross_party.get('inconsistency_count', 0),
                "cross_party_risk_score":    cross_party.get('cross_party_risk_score', 0),
                "verification_quality":      cross_party.get('verification_quality', 'unknown'),
                "photo_counts":              cross_party.get('photo_counts', {}),
                "duplicate_photos_detected": cross_party.get('duplicate_photos_detected', 0),
                "duplicate_details":         cross_party.get('duplicate_details', []),
                "damage_comparison":         cross_party.get('damage_claims_by_party', {}),
                "details":                   cross_party.get('inconsistencies', [])
            },
            "fraud_indicators": {
                "photo_anomalies": [
                    {"type": a.get('type'), "severity": a.get('severity'), "description": a.get('description'),
                     "party": a.get('party'), "confidence": a.get('confidence')}
                    for a in all_anomalies
                ],
                "narrative_inconsistencies": [
                    {"type": i.get('type'), "severity": i.get('severity'), "description": i.get('description'),
                     "party": i.get('party'), "confidence": i.get('confidence')}
                    for i in all_inconsistencies
                ],
                "cross_party_issues": cross_party.get('inconsistencies', [])
            },
            "risk_breakdown": {
                "photo_risk":       risk_scoring.get('component_scores', {}).get('photo_analysis', 0),
                "narrative_risk":   risk_scoring.get('component_scores', {}).get('narrative_analysis', 0),
                "amount_risk":      risk_scoring.get('component_scores', {}).get('amount_based', 0),
                "estimated_cost":   estimated_cost,
                "location_risk":    risk_scoring.get('component_scores', {}).get('location_based', 0),
                "location":         location,
                "historical_risk":  risk_scoring.get('component_scores', {}).get('historical_patterns', 0),
                "physics_risk":     risk_scoring.get('component_scores', {}).get('physics_reconstruction', 0),
                "cross_party_risk": cross_party.get('cross_party_risk_score', 0),
                "weights_applied":  risk_scoring.get('weights', {}),
                "explanation":      risk_scoring.get('explanation', ''),
                "overall_score":    risk_score
            },
            "physics_reconstruction": {
                "status":              physics_summary.get('status', 'not_run'),
                "claim_category":      physics_summary.get('claim_category'),
                "pathway":             physics_summary.get('pathway'),
                "physics_fraud_score": physics_summary.get('physics_fraud_score'),
                "physics_verdict":     physics_verdict,
                "verdict_reason":      physics_summary.get('verdict_reason'),
                "physics_explanation": physics_summary.get('physics_explanation', ''),
                "simulation_method":   physics_summary.get('simulation_method'),
                "inconsistencies":     physics_summary.get('inconsistencies', []),
                "warnings":            physics_summary.get('warnings', []),
                "signature":           physics_summary.get('signature'),
                "timeline_metadata":   timeline_meta,
                "timeline":            timeline if include_timeline else None,
                "comparison":          physics_summary.get('comparison'),
            },
            "detection_summary": {
                "total_anomalies":       len(all_anomalies),
                "critical_issues":       critical_count,
                "high_risk_issues":      high_count,
                "medium_risk_issues":    len([a for a in all_anomalies if a.get('severity') == 'medium']),
                "low_risk_issues":       len([a for a in all_anomalies if a.get('severity') == 'low']),
                "total_inconsistencies": len(all_inconsistencies),
                "primary_concerns":      [a.get('description') for a in all_anomalies if a.get('severity') in ['critical', 'high']][:5]
            },
            "critical_warnings": [
                {"severity": "CRITICAL", "type": a.get('type'), "message": a.get('description'),
                 "confidence": a.get('confidence'), "party": a.get('party')}
                for a in all_anomalies if a.get('severity') == 'critical'
            ] + [
                {"severity": "HIGH", "type": a.get('type'), "message": a.get('description'),
                 "confidence": a.get('confidence'), "party": a.get('party')}
                for a in all_anomalies if a.get('severity') == 'high'
            ],
            "member_submission":      analysis_result.get('member_submission'),
            "assessor_submission":    analysis_result.get('assessor_submission'),
            "repair_shop_submission": analysis_result.get('repair_shop_submission'),
            "recommendations":        analysis_result.get('recommendations', []),
            # Consolidated Narrative Intelligence + Computer Vision + Physics +
            # Cross-validation recommendation from claims-advisory-v1 (see
            # ClaimOrchestrator._build_ai_advisory in service.py).
            "ai_advisory":            analysis_result.get('ai_advisory'),
            "next_actions": (
                [f"Decision: {final_decision}", "Review complete fraud analysis report", "Notify all parties of decision"]
                + (["Escalate to SIU for investigation"] if final_decision == "INVESTIGATE_FURTHER" else [])
                + (["Process payment authorization"]     if final_decision == "APPROVE_CLAIM"       else [])
                + (["Issue decline letter with reasons"] if final_decision == "DECLINE_CLAIM"       else [])
            )
        }
 
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error retrieving fraud analysis: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Error retrieving analysis: {str(e)}")
 
 
@analysis_router.get("/claim/{claim_id}/ai-rol")
async def get_ai_rol_audit_trail(claim_id: str):
    """
    AI Governance & Regulatory Layer (AI-ROL) audit trail for a claim.

    Returns the full chronological record of every AI recommendation
    generated for this claim (Narrative Intelligence, Computer Vision,
    Physics & Mathematical Consistency, Cross-validation & Risk
    Intelligence, AI Advisory), each with its confidence, supporting
    evidence, and -- once a claims handler has acted on it -- the
    resulting action, handler ID, and any override reason.
    """
    try:
        trail = ai_rol.get_audit_trail(claim_id)
        return {"claim_id": claim_id, "records": trail}
    except Exception as e:
        logger.error(f"Error retrieving AI-ROL audit trail for {claim_id}: {str(e)}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Error retrieving AI-ROL audit trail: {str(e)}")


@analysis_router.post("/claim/{claim_id}/ai-rol/action")
async def record_ai_rol_action(
    claim_id: str,
    handler_id: str = Form(...),
    action: str = Form(...),
    capability: str = Form("ai_advisory"),
    reason: Optional[str] = Form(None),
):
    """
    Record a claims handler's decision (proceed / clarify / escalate /
    override) against the most recent AI recommendation for the given
    capability -- default ai_advisory, the consolidated recommendation
    the handler actually reviews. An override requires a reason.
    """
    try:
        ai_rol.record_handler_action(
            claim_id=claim_id,
            handler_id=handler_id,
            action=action,
            capability=capability,
            reason=reason,
        )
        return {"success": True, "claim_id": claim_id, "action": action}
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        logger.error(f"Error recording AI-ROL handler action for {claim_id}: {str(e)}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Error recording handler action: {str(e)}")


@analysis_router.get("/claim/{claim_id}/summary")
async def get_analysis_summary(claim_id: str):
    """
    **ADMIN ONLY: Get quick summary of fraud analysis**
 
    Returns condensed view of key fraud indicators and decision.
    """
    try:
        claim = db_manager.get_claim(claim_id)
        if not claim:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Claim {claim_id} not found")
 
        analysis_result = claim.get('analysis_result')
        if isinstance(analysis_result, str):
            analysis_result = json.loads(analysis_result)
 
        risk_score  = analysis_result.get('fraud_risk_score', 0)
        cross_party = analysis_result.get('cross_party_verification', {})
 
        if risk_score >= 75:
            decision, color = "DECLINE", "🔴"
        elif risk_score >= 50:
            decision, color = "INVESTIGATE", "🟡"
        else:
            decision, color = "APPROVE", "🟢"
 
        return {
            "claim_id":          claim_id,
            "decision":          decision,
            "decision_icon":     color,
            "fraud_risk_score":  risk_score,
            "risk_level":        analysis_result.get('risk_level'),
            "cross_party_issues": cross_party.get('inconsistency_count', 0),
            "duplicate_photos":  cross_party.get('duplicate_photos_detected', 0),
            "parties_completed": {
                "member":       analysis_result.get('parties_analyzed', {}).get('member', False),
                "assessor":     analysis_result.get('parties_analyzed', {}).get('assessor', False),
                "repair_shop":  analysis_result.get('parties_analyzed', {}).get('repair_shop', False)
            },
            "timestamp": analysis_result.get('timestamp')
        }
 
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Error retrieving summary: {str(e)}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Error retrieving summary: {str(e)}")
 
 
@analysis_router.get("/claims/all")
async def get_all_claims_analysis(
    limit: int = Query(20, description="Number of claims to return"),
    offset: int = Query(0, description="Pagination offset"),
    risk_level: Optional[str] = Query(None, description="Filter by risk level: low / medium / high"),
    verdict: Optional[str] = Query(None, description="Filter by physics verdict: CONSISTENT / SUSPICIOUS / INCONSISTENT"),
    decision: Optional[str] = Query(None, description="Filter by decision: APPROVE_CLAIM / INVESTIGATE_FURTHER / DECLINE_CLAIM"),
):
    """
    **ADMIN ONLY: Get all claims with full analysis details**
 
    Returns paginated list of all claims with the same detail level
    as the single claim full-report endpoint.
 
    Filters:
    - risk_level: low / medium / high
    - verdict: CONSISTENT / SUSPICIOUS / INCONSISTENT
    - decision: APPROVE_CLAIM / INVESTIGATE_FURTHER / DECLINE_CLAIM
    """
    try:
        logger.info(f"Admin requesting all claims analysis (limit={limit}, offset={offset})")
 
        with db_manager.get_connection() as conn:
            cursor = conn.cursor()
 
            query  = "SELECT * FROM claims WHERE 1=1"
            params = []
            if risk_level:
                query += " AND risk_level = ?"
                params.append(risk_level.lower())
            query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
            params.extend([limit, offset])
            cursor.execute(query, params)
            rows = cursor.fetchall()
 
            count_query  = "SELECT COUNT(*) FROM claims WHERE 1=1"
            count_params = []
            if risk_level:
                count_query += " AND risk_level = ?"
                count_params.append(risk_level.lower())
            cursor.execute(count_query, count_params)
            total = cursor.fetchone()[0]
 
        claims_list = []
 
        for row in rows:
            claim = dict(row)
            analysis_result = claim.get('analysis_result')
            if isinstance(analysis_result, str):
                try:
                    analysis_result = json.loads(analysis_result)
                except Exception:
                    analysis_result = {}
            if not analysis_result:
                continue
 
            risk_score      = analysis_result.get('fraud_risk_score', 0)
            estimated_cost  = analysis_result.get('estimated_cost', 0)
            location        = analysis_result.get('location', '')
            cross_party     = analysis_result.get('cross_party_verification', {})
            photo_analysis  = analysis_result.get('photo_analysis', {})
            risk_scoring    = analysis_result.get('risk_scoring', {})
            physics_summary = analysis_result.get('physics_reconstruction', {})
 
            all_anomalies = []
            for photo_result in photo_analysis.get('results', []):
                all_anomalies.extend(photo_result.get('anomalies', []))
 
            all_inconsistencies = []
            if 'member_submission' in analysis_result:
                member_na = analysis_result['member_submission'].get('narrative_analysis', {})
                all_inconsistencies.extend(member_na.get('inconsistencies', []))
            if analysis_result.get('assessor_submission'):
                assessor_na = analysis_result['assessor_submission'].get('analysis', {})
                if assessor_na:
                    all_inconsistencies.extend(assessor_na.get('inconsistencies', []))
            if analysis_result.get('repair_shop_submission'):
                repair_na = analysis_result['repair_shop_submission'].get('analysis', {})
                if repair_na:
                    all_inconsistencies.extend(repair_na.get('inconsistencies', []))
 
            critical_count      = len([a for a in all_anomalies if a.get('severity') == 'critical'])
            high_count          = len([a for a in all_anomalies if a.get('severity') == 'high'])
            physics_verdict     = physics_summary.get('physics_verdict') if physics_summary else None
            physics_fraud_score = physics_summary.get('physics_fraud_score', 0) if physics_summary else 0
 
            if critical_count > 0:
                final_decision  = "INVESTIGATE_FURTHER"
                decision_reason = f"CRITICAL FRAUD INDICATORS ({critical_count} critical issues)"
            elif physics_verdict == "INCONSISTENT" or physics_fraud_score >= 45:
                final_decision  = "INVESTIGATE_FURTHER"
                decision_reason = (
                    f"Physics reconstruction flagged significant inconsistencies "
                    f"(score: {physics_fraud_score}/100, verdict: {physics_verdict}). "
                    f"Manual review required before payment."
                )
            elif physics_verdict == "SUSPICIOUS":
                final_decision  = "INVESTIGATE_FURTHER"
                decision_reason = (
                    f"Minor physics inconsistencies detected (score: {physics_fraud_score}/100). "
                    f"Approve only after mandatory assessor review."
                )
            elif high_count >= 2:
                final_decision  = "INVESTIGATE_FURTHER"
                decision_reason = f"Multiple HIGH-RISK indicators ({high_count} issues)"
            elif risk_score >= 75:
                final_decision  = "DECLINE_CLAIM"
                decision_reason = "High fraud risk detected"
            elif risk_score >= 50:
                final_decision  = "INVESTIGATE_FURTHER"
                decision_reason = "Medium fraud risk - SIU investigation required"
            else:
                final_decision  = "APPROVE_CLAIM"
                decision_reason = "Low fraud risk - proceed with payment"
 
            if decision and final_decision != decision.upper():
                continue
            if verdict and physics_verdict != verdict.upper():
                continue
 
            timeline      = physics_summary.get('timeline') if physics_summary else None
            timeline_meta = timeline.get('simulation_metadata') if isinstance(timeline, dict) else None
 
            claims_list.append({
                "claim_id":           claim.get('claim_id'),
                "analysis_timestamp": analysis_result.get('timestamp'),
                "created_at":         claim.get('created_at'),
                "member_id":          claim.get('member_id'),
                "estimated_cost":     estimated_cost,
                "location":           location,
                "final_assessment": {
                    "decision":         final_decision,
                    "decision_reason":  decision_reason,
                    "fraud_risk_score": risk_score,
                    "risk_level":       analysis_result.get('risk_level', 'unknown'),
                    "estimated_cost":   estimated_cost,
                    "location":         location,
                },
                "parties_analyzed": analysis_result.get('parties_analyzed', {}),
                "cross_party_verification": {
                    "inconsistencies_found":     cross_party.get('inconsistencies_found', False),
                    "inconsistency_count":       cross_party.get('inconsistency_count', 0),
                    "cross_party_risk_score":    cross_party.get('cross_party_risk_score', 0),
                    "duplicate_photos_detected": cross_party.get('duplicate_photos_detected', 0),
                    "verification_quality":      cross_party.get('verification_quality', 'unknown'),
                },
                "fraud_indicators": {
                    "photo_anomalies": [
                        {"type": a.get('type'), "severity": a.get('severity'), "description": a.get('description'),
                         "party": a.get('party'), "confidence": a.get('confidence')}
                        for a in all_anomalies
                    ],
                    "narrative_inconsistencies": [
                        {"type": i.get('type'), "severity": i.get('severity'), "description": i.get('description'),
                         "party": i.get('party'), "confidence": i.get('confidence')}
                        for i in all_inconsistencies
                    ],
                    "cross_party_issues": cross_party.get('inconsistencies', [])
                },
                "risk_breakdown": {
                    "photo_risk":       risk_scoring.get('component_scores', {}).get('photo_analysis', 0),
                    "narrative_risk":   risk_scoring.get('component_scores', {}).get('narrative_analysis', 0),
                    "amount_risk":      risk_scoring.get('component_scores', {}).get('amount_based', 0),
                    "estimated_cost":   estimated_cost,
                    "location_risk":    risk_scoring.get('component_scores', {}).get('location_based', 0),
                    "location":         location,
                    "historical_risk":  risk_scoring.get('component_scores', {}).get('historical_patterns', 0),
                    "physics_risk":     risk_scoring.get('component_scores', {}).get('physics_reconstruction', 0),
                    "cross_party_risk": cross_party.get('cross_party_risk_score', 0),
                    "weights_applied":  risk_scoring.get('weights', {}),
                    "explanation":      risk_scoring.get('explanation', ''),
                    "overall_score":    risk_score
                },
                "physics_reconstruction": {
                    "status":              physics_summary.get('status', 'not_run') if physics_summary else 'not_run',
                    "claim_category":      physics_summary.get('claim_category') if physics_summary else None,
                    "pathway":             physics_summary.get('pathway') if physics_summary else None,
                    "physics_fraud_score": physics_summary.get('physics_fraud_score') if physics_summary else None,
                    "physics_verdict":     physics_verdict,
                    "verdict_reason":      physics_summary.get('verdict_reason') if physics_summary else None,
                    "physics_explanation": physics_summary.get('physics_explanation', '') if physics_summary else '',
                    "simulation_method":   physics_summary.get('simulation_method') if physics_summary else None,
                    "inconsistencies":     physics_summary.get('inconsistencies', []) if physics_summary else [],
                    "signature":           physics_summary.get('signature') if physics_summary else None,
                    "timeline_metadata":   timeline_meta,
                },
                "detection_summary": {
                    "total_anomalies":       len(all_anomalies),
                    "critical_issues":       critical_count,
                    "high_risk_issues":      high_count,
                    "medium_risk_issues":    len([a for a in all_anomalies if a.get('severity') == 'medium']),
                    "low_risk_issues":       len([a for a in all_anomalies if a.get('severity') == 'low']),
                    "total_inconsistencies": len(all_inconsistencies),
                    "primary_concerns":      [a.get('description') for a in all_anomalies if a.get('severity') in ['critical', 'high']][:3]
                },
                "critical_warnings": [
                    {"severity": "CRITICAL", "type": a.get('type'), "message": a.get('description'),
                     "confidence": a.get('confidence'), "party": a.get('party')}
                    for a in all_anomalies if a.get('severity') == 'critical'
                ] + [
                    {"severity": "HIGH", "type": a.get('type'), "message": a.get('description'),
                     "confidence": a.get('confidence'), "party": a.get('party')}
                    for a in all_anomalies if a.get('severity') == 'high'
                ],
                "member_submission":      analysis_result.get('member_submission'),
                "assessor_submission":    analysis_result.get('assessor_submission'),
                "repair_shop_submission": analysis_result.get('repair_shop_submission'),
                "recommendations":        analysis_result.get('recommendations', []),
                "next_actions": (
                    [f"Decision: {final_decision}", "Review complete fraud analysis report", "Notify all parties of decision"]
                    + (["Escalate to SIU for investigation"] if final_decision == "INVESTIGATE_FURTHER" else [])
                    + (["Process payment authorization"]     if final_decision == "APPROVE_CLAIM"       else [])
                    + (["Issue decline letter with reasons"] if final_decision == "DECLINE_CLAIM"       else [])
                )
            })
 
        return {
            "success": True,
            "total":   total,
            "limit":   limit,
            "offset":  offset,
            "count":   len(claims_list),
            "claims":  claims_list
        }
 
    except Exception as e:
        logger.error(f"Error retrieving all claims analysis: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Error retrieving claims: {str(e)}")
 
 
@analysis_router.get("/status/{claim_id}")
async def get_multiparty_status(claim_id: str):
    """
    Get status of multi-party claim submission
 
    Shows which parties have submitted and current analysis status
    """
    try:
        claim_data = db_manager.get_claim(claim_id)
        if not claim_data:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Claim {claim_id} not found")
 
        parties_analyzed = claim_data.get('parties_analyzed', {})
 
        return {
            "claim_id": claim_id,
            "submission_status": {
                "member":       parties_analyzed.get('member', False),
                "assessor":     parties_analyzed.get('assessor', False),
                "repair_shop":  parties_analyzed.get('repair_shop', False)
            },
            "current_risk_score": claim_data.get('fraud_risk_score'),
            "risk_level":         claim_data.get('risk_level'),
            "next_step":          _determine_next_step(parties_analyzed),
            "created_at":         claim_data.get('timestamp'),
            "last_updated":       claim_data.get('timestamp')
        }
 
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Error getting claim status: {str(e)}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Error retrieving claim status: {str(e)}")
 
 
def _determine_next_step(parties_analyzed: Dict[str, bool]) -> str:
    if not parties_analyzed.get('member'):
        return "Waiting for member submission"
    elif not parties_analyzed.get('assessor'):
        return "Waiting for assessor inspection and report"
    elif not parties_analyzed.get('repair_shop'):
        return "Waiting for repair shop estimate"
    else:
        return "All parties submitted - final decision ready"
 
 
@system_router.post("/ensure-ollama-ready")
async def ensure_ollama_ready_endpoint():
    """
    Called when a member starts filing a claim (Claims Intake mount). Checks
    whether the self-hosted Ollama instance is reachable through the SSH
    tunnel and, if not, attempts to recover it -- starting the Ollama docker
    container on the devserver if it's stopped, and relaunching the local
    tunnel process if it's died. Never blocks claim submission indefinitely;
    returns 'unreachable' rather than hanging if recovery doesn't succeed
    within the wait window.
    """
    try:
        result = await asyncio.to_thread(devserver_watchdog.ensure_ollama_ready)
        return {"success": True, **result}
    except Exception as e:
        logger.error(f"ensure_ollama_ready failed unexpectedly: {str(e)}")
        return {"success": False, "status": "error", "detail": str(e)}


@system_router.get("/health", response_model=HealthCheckSchema)
async def health_check():
    """
    System health check endpoint
    """
    try:
        test_metrics = db_manager.get_system_metrics()
        db_status = "operational" if test_metrics else "degraded"
        return HealthCheckSchema(
            status="healthy",
            version="1.0.0",
            services={
                "photo_analysis": "operational",
                "narrative_analysis": "operational",
                "risk_scoring": "operational",
                "database": db_status
            }
        )
    except Exception as e:
        logger.error(f"Health check failed: {str(e)}")
        return HealthCheckSchema(
            status="degraded",
            version="1.0.0",
            services={
                "photo_analysis": "operational",
                "narrative_analysis": "operational",
                "risk_scoring": "operational",
                "database": "error"
            }
        )
 
 
@system_router.get("/stats", response_model=SystemStatsSchema)
async def get_system_stats():
    """
    Get system performance statistics
    """
    try:
        metrics = db_manager.get_system_metrics()
        return SystemStatsSchema(
            total_claims_processed=metrics["total_claims_processed"],
            high_risk_claims=metrics["high_risk_claims"],
            average_processing_time_ms=metrics["average_processing_time_ms"],
            system_uptime=metrics["system_uptime"],
            fraud_detection_rate=f"{metrics['fraud_detection_rate']}%",
            false_positive_rate=f"{metrics['false_positive_rate']}%"
        )
    except Exception as e:
        logger.error(f"Error getting system stats: {str(e)}")
        return SystemStatsSchema(
            total_claims_processed=0,
            high_risk_claims=0,
            average_processing_time_ms=0.0,
            system_uptime="0%",
            fraud_detection_rate="0%",
            false_positive_rate="0%"
        )
 
 
@system_router.get("/metrics/detailed")
async def get_detailed_metrics():
    """
    Get detailed system metrics for monitoring
    """
    try:
        metrics = db_manager.get_system_metrics()
        total_claims     = metrics["total_claims_processed"]
        high_risk_claims = metrics["high_risk_claims"]
        return {
            "performance": {
                "total_claims_processed":    total_claims,
                "high_risk_detection_rate":  (high_risk_claims / total_claims * 100) if total_claims > 0 else 0,
                "average_processing_time_ms": metrics["average_processing_time_ms"],
                "p95_processing_time_ms":    metrics["p95_processing_time_ms"],
                "p99_processing_time_ms":    metrics["p99_processing_time_ms"]
            },
            "accuracy": {
                "fraud_detection_rate": metrics["fraud_detection_rate"],
                "false_positive_rate":  metrics["false_positive_rate"]
            },
            "system": {
                "uptime":       metrics["system_uptime"],
                "status":       "operational",
                "last_updated": datetime.now().isoformat()
            },
            "distribution": {
                "low_risk_claims":    total_claims - high_risk_claims - int(total_claims * 0.3),
                "medium_risk_claims": int(total_claims * 0.3),
                "high_risk_claims":   high_risk_claims
            }
        }
    except Exception as e:
        logger.error(f"Error getting detailed metrics: {str(e)}")
        return {
            "performance": {"total_claims_processed": 0, "high_risk_detection_rate": 0,
                            "average_processing_time_ms": 0, "p95_processing_time_ms": 0, "p99_processing_time_ms": 0},
            "accuracy":    {"fraud_detection_rate": 0, "false_positive_rate": 0},
            "system":      {"uptime": "0%", "status": "error", "last_updated": datetime.now().isoformat()},
            "distribution":{"low_risk_claims": 0, "medium_risk_claims": 0, "high_risk_claims": 0}
        }
 
 
@system_router.get("/database/status")
async def get_database_status():
    """
    Get database status and statistics
    """
    try:
        with db_manager.get_connection() as conn:
            cursor = conn.cursor()
            tables_info = {}
            for table in ['claims', 'claim_photos', 'system_metrics', 'processing_logs']:
                cursor.execute(f'SELECT COUNT(*) FROM {table}')
                count = cursor.fetchone()[0]
                tables_info[table] = {"record_count": count}
            cursor.execute("PRAGMA page_size")
            page_size = cursor.fetchone()[0]
            cursor.execute("PRAGMA page_count")
            page_count = cursor.fetchone()[0]
            db_size_bytes = page_size * page_count
            return {
                "status":           "operational",
                "database_size_mb": round(db_size_bytes / (1024 * 1024), 2),
                "tables":           tables_info,
                "last_check":       datetime.now().isoformat()
            }
    except Exception as e:
        logger.error(f"Error getting database status: {str(e)}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Error getting database status")
 