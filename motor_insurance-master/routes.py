
from fastapi import APIRouter, HTTPException, Depends, File, UploadFile, Form, status, Query, BackgroundTasks, Response
from fastapi.responses import JSONResponse, FileResponse, PlainTextResponse
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
import business_rules
import devserver_watchdog
import email_service
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


def build_assessor_measurement_block(
    crush_depth_mm: Optional[float] = None,
    approach_angle_deg: Optional[float] = None,
    third_party_vehicle_confirmed: Optional[str] = None,
) -> str:
    """
    Same idea as build_structured_intake_block, but for the assessor's
    on-site findings. Crush depth and approach angle are also passed through
    separately as direct physics overrides (see
    ClaimOrchestrator._run_physics_reconstruction) -- this text block exists
    so the numbers are ALSO visible to narrative extraction and any human
    reading the report, not just the physics engine.
    """
    lines = []

    if crush_depth_mm is not None:
        lines.append(f"Measured crush/deformation depth: {crush_depth_mm}mm (assessor on-site measurement)")
    if approach_angle_deg is not None:
        lines.append(f"Assessed impact/approach angle: {approach_angle_deg}° (0=rear-end, 90=T-bone, 180=head-on)")
    if third_party_vehicle_confirmed:
        lines.append(f"Third-party vehicle confirmed on-site: {third_party_vehicle_confirmed}")

    if not lines:
        return ""

    return "ASSESSOR ON-SITE MEASUREMENTS (professional observation, not narrative inference):\n" + "\n".join(f"- {l}" for l in lines)


async def _process_member_claim_background(
    claim_id: str,
    clean_narrative: str,
    photo_data: list,
    estimated_cost: float,
    location: str,
    id_document_data: Optional[tuple] = None,
):
    try:
        from document_ocr import extract_document_data, build_document_evidence_block

        # Documents already uploaded+OCR'd earlier in the intake flow -- e.g.
        # the police abstract, which the wizard now uploads and reads
        # immediately when the claimant confirms police were involved,
        # instead of waiting until this final submission step.
        documents_ocr = [
            {
                "document_type": d.get("document_type"),
                "raw_text": d.get("raw_text"),
                "parsed_fields": d.get("parsed_fields"),
                "extraction_confidence": d.get("extraction_confidence"),
            }
            for d in db_manager.get_documents_by_claim(claim_id, party="member")
        ]

        for doc_data, doc_type in (
            (id_document_data, "id_document"),
        ):
            if not doc_data:
                continue
            content, filename = doc_data
            ocr_result = await extract_document_data(content, filename, doc_type)
            try:
                db_manager.store_document(
                    claim_id=claim_id, party="member", document_type=doc_type,
                    filename=filename, file_data=content, ocr_result=ocr_result,
                )
            except Exception as e:
                logger.error(f"Failed to store document {filename} for {claim_id}: {str(e)}")
            documents_ocr.append(ocr_result)

        evidence_block = build_document_evidence_block(documents_ocr)
        enriched_narrative = f"{evidence_block}\n\n{clean_narrative}" if evidence_block else clean_narrative

        claim_row = db_manager.get_claim(claim_id) or {}
        incident_details = {"police_reported": bool(next(
            (d.get("parsed_fields", {}).get("ob_number") for d in documents_ocr if d.get("document_type") == "police_abstract"),
            None,
        ))}
        ob_number = next(
            (d.get("parsed_fields", {}).get("ob_number") for d in documents_ocr if d.get("document_type") == "police_abstract"),
            None,
        )
        if ob_number:
            incident_details["ob_number"] = ob_number

        orchestrator = get_claim_orchestrator()
        analysis_result = await orchestrator.analyze_multiparty_claim(
            claim_id=claim_id,
            member_narrative=enriched_narrative,
            member_photos=photo_data,
            estimated_cost=estimated_cost,
            location=location,
            member_id=claim_row.get("member_id"),
            policy_id=claim_row.get("policy_id"),
            claim_type="motor",
            incident_details=incident_details,
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
    assessor_crush_depth_mm: Optional[float] = None,
    assessor_approach_angle_deg: Optional[float] = None,
    assessor_id: Optional[str] = None,
    garage_quote_data: Optional[tuple] = None,
    id_document_data: Optional[tuple] = None,
):
    try:
        from document_ocr import extract_document_data, build_document_evidence_block

        documents_ocr = []
        for doc_data, doc_type in (
            (garage_quote_data, "garage_quote"),
            (id_document_data, "id_document"),
        ):
            if not doc_data:
                continue
            content, filename = doc_data
            ocr_result = await extract_document_data(content, filename, doc_type)
            try:
                db_manager.store_document(
                    claim_id=claim_id, party="assessor", document_type=doc_type,
                    filename=filename, file_data=content, ocr_result=ocr_result,
                )
            except Exception as e:
                logger.error(f"Failed to store document {filename} for {claim_id}: {str(e)}")
            documents_ocr.append(ocr_result)

        evidence_block = build_document_evidence_block(documents_ocr)
        enriched_report = f"{evidence_block}\n\n{clean_report}" if evidence_block else clean_report

        orchestrator = get_claim_orchestrator()
        analysis_result = await orchestrator.analyze_multiparty_claim(
            claim_id=claim_id,
            member_narrative=member_narrative,
            member_photos=member_photo_data,
            assessor_report=enriched_report,
            assessor_photos=assessor_photo_data,
            estimated_cost=estimated_cost,
            location=location,
            assessor_crush_depth_mm=assessor_crush_depth_mm,
            assessor_approach_angle_deg=assessor_approach_angle_deg,
            assessor_id=assessor_id,
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
        return {"sufficient": True, "clarifying_question": None, "missing_aspect": None, "extracted_facts": {}}

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

Separately, the intake flow also asks the claimant four yes/no questions after this narrative:
whether a third party was involved, whether police were called, whether there were witnesses, and
whether anyone was injured. Asking these blind when the narrative already answered them reads as
not having listened -- so also extract what the narrative ALREADY makes clear about each, to be
confirmed with the claimant instead of asked cold. Only fill a field when the narrative is explicit
or unambiguous about it (e.g. describing a collision with another vehicle clearly means a third
party was involved; "no injuries" clearly means injuries_reported is No). Leave a field null if the
narrative is silent on it or genuinely ambiguous -- do not guess.

Respond in JSON only:
{{
    "sufficient": true/false,
    "missing_aspect": "the single most important missing detail (not date or location), or null if sufficient",
    "clarifying_question": "one natural, conversational follow-up question about that one missing aspect, or null if sufficient",
    "extracted_facts": {{
        "third_party_involved": "Yes"/"No"/null,
        "third_party_summary": "one short phrase describing the other party/vehicle if involved, or null",
        "police_reported": "Yes"/"No"/null,
        "witnesses_present": "Yes"/"No"/null,
        "injuries_reported": "Yes"/"No"/null
    }}
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
            "extracted_facts": result.get("extracted_facts") or {},
        }
    except Exception as e:
        logger.warning(f"Narrative sufficiency check failed: {e} — defaulting to sufficient")
        return {"sufficient": True, "clarifying_question": None, "missing_aspect": None, "extracted_facts": {}}


async def _reevaluate_business_rules_background(claim_id: str):
    """
    Re-runs the Business Rules Engine after a document is added to a claim
    that has already been through its initial analysis -- most importantly,
    a police abstract arriving hours after the member filed. Without this,
    a claim gets permanently stuck with an "unreported theft" / "missing
    evidence" flag even after the claimant does the right thing and reports
    it late, because the original evaluation only ever ran once, right
    after initial submission.

    Only touches the business_rules section, the AI advisory's
    business_rules_observation, and the risk level (raise-only, same floor
    logic as the initial evaluation) -- it does not re-run photo/narrative/
    physics analysis, which stays exactly as originally computed.
    """
    try:
        claim = db_manager.get_claim(claim_id)
        if not claim or not claim.get("member_id") or not claim.get("policy_id"):
            return

        analysis_result = json.loads(claim.get("analysis_result") or "{}")
        if not analysis_result:
            # Initial analysis hasn't run/finished yet -- it will pick up
            # this document naturally when it does.
            return

        photos = db_manager.get_photos_metadata_by_claim(claim_id, party="member")
        documents = db_manager.get_documents_by_claim(claim_id, party="member")
        ob_doc = next((d for d in documents if d.get("document_type") == "police_abstract"), None)

        incident_details = {}
        if ob_doc:
            fields = ob_doc.get("corrected_fields") or ob_doc.get("parsed_fields") or {}
            ob_number = fields.get("ob_number")
            if ob_number:
                incident_details["police_reported"] = True
                incident_details["ob_number"] = ob_number

        narrative = claim.get("narrative") or analysis_result.get("narrative", "")

        result = business_rules.BusinessRulesEngine(db_manager).evaluate(
            claim_id=claim_id,
            member_id=claim["member_id"],
            policy_id=claim["policy_id"],
            claim_type="motor",
            narrative_text=narrative,
            photo_count=len(photos),
            incident_details=incident_details,
        )

        analysis_result["business_rules"] = result.to_dict()
        if isinstance(analysis_result.get("ai_advisory"), dict):
            analysis_result["ai_advisory"]["business_rules_observation"] = result.observation_text

        # Raise-only floor, applied against whatever risk_level is currently
        # stored -- matches the pattern used during the initial evaluation.
        # New evidence resolving a flag doesn't retroactively lower a level
        # that other signals (photo/narrative/physics) already justified.
        _level_order = {"low": 0, "medium": 1, "high": 2}
        current_level = analysis_result.get("risk_level", "low")
        highest_severity = max(
            (f.severity for f in result.findings),
            key=lambda s: business_rules.SEVERITY_WEIGHT.get(s, 0),
            default=None,
        )
        implied_level = (
            "high" if highest_severity in ("high", "critical") else
            "medium" if highest_severity == "medium" else
            "low"
        )
        new_level = implied_level if _level_order[implied_level] > _level_order.get(current_level, 0) else current_level

        analysis_result["risk_level"] = new_level
        if isinstance(analysis_result.get("final_assessment"), dict):
            analysis_result["final_assessment"]["risk_level"] = new_level
        if isinstance(analysis_result.get("risk_scoring"), dict):
            analysis_result["risk_scoring"].setdefault("component_scores", {})["business_rules"] = result.risk_score

        with db_manager.get_connection() as conn:
            conn.execute(
                "UPDATE claims SET analysis_result = ?, risk_level = ? WHERE claim_id = ?",
                (json.dumps(analysis_result), new_level, claim_id),
            )
            conn.commit()

        ai_rol.record_recommendation(
            claim_id=claim_id,
            capability="business_rules",
            recommendation=(
                f"Re-evaluated after new document upload — {len(result.findings)} rule(s) triggered"
                if result.findings else
                "Re-evaluated after new document upload — no rules triggered"
            ),
            confidence=None,
            evidence=result.to_dict(),
        )
        logger.info(f"Business rules re-evaluated for {claim_id} after document upload — risk_level now {new_level}")
    except Exception as e:
        logger.error(f"Business rules re-evaluation failed for {claim_id}: {str(e)}")


@analysis_router.post("/documents/upload")
async def upload_and_ocr_document(
    background_tasks: BackgroundTasks,
    claim_id: str = Form(...),
    party: str = Form(..., description="'member' or 'assessor'"),
    document_type: str = Form(..., description="police_abstract / id_document / garage_quote / other"),
    uploader_id: str = Form(..., description="member_id or assessor_id, for ownership verification"),
    file: UploadFile = File(...),
):
    """
    **Upload a single supporting document ahead of full claim submission and OCR it immediately**

    Unlike the documents bundled into /api/analysis/member or /assessor
    (which OCR in the background, after the claimant has already moved on),
    this runs synchronously -- a few seconds -- so the intake wizard can
    show the claimant what was read (e.g. the OB number) and let them
    confirm or correct it right there, instead of asking them to separately
    type a number that a photo of the same document already contains.

    Already-uploaded documents are picked up by the final submission's
    background processing (see _process_member_claim_background) rather
    than needing to be re-uploaded at that point.
    """
    try:
        if not file.content_type or not file.content_type.startswith('image/'):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="File must be an image")

        content = await file.read()
        if len(content) > 10 * 1024 * 1024:
            raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="File exceeds 10MB limit")

        claim = db_manager.get_claim(claim_id)
        if not claim:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Claim not found")

        authorized = False
        if party == "member":
            authorized = claim.get("member_id") == uploader_id
        elif party == "assessor":
            with db_manager.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT 1 FROM claim_assignments WHERE claim_id = ? AND assessor_id = ?",
                    (claim_id, uploader_id)
                )
                authorized = cursor.fetchone() is not None
        else:
            authorized = True

        if not authorized:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to upload documents for this claim")

        from document_ocr import extract_document_data
        ocr_result = await extract_document_data(content, file.filename, document_type)

        doc_id = db_manager.store_document(
            claim_id=claim_id, party=party, document_type=document_type,
            filename=file.filename, file_data=content, ocr_result=ocr_result,
            content_type=file.content_type,
        )

        # A police abstract arriving after the claim's already been analyzed
        # (e.g. the member gets the OB number hours later) should clear the
        # "unreported theft" / "missing evidence" business-rules flags
        # instead of leaving them stuck from the original evaluation.
        if document_type == "police_abstract" and party == "member":
            background_tasks.add_task(_reevaluate_business_rules_background, claim_id)

        return {
            "success": True,
            "document_id": doc_id,
            "parsed_fields": ocr_result.get("parsed_fields", {}),
            "extraction_confidence": ocr_result.get("extraction_confidence", 0),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error uploading/OCR'ing document for {claim_id}: {str(e)}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


@analysis_router.get("/documents/{document_id}/file")
async def get_document_file(document_id: int):
    """
    **Serve the raw image bytes of an uploaded document, for inline viewing**

    Lets the UI show an eye icon next to a document card that opens the
    actual photo (police abstract, ID, garage quote) instead of only ever
    showing the OCR-extracted text -- useful for a human to sanity-check
    what OCR read against the real document.
    """
    try:
        with db_manager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT file_data, content_type FROM claim_documents WHERE id = ?",
                (document_id,)
            )
            row = cursor.fetchone()
        if not row or not row["file_data"]:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document file not found")
        return Response(content=row["file_data"], media_type=row["content_type"] or "image/jpeg")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error serving document file {document_id}: {str(e)}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


@analysis_router.get("/photos/{photo_id}/file")
async def get_photo_file(photo_id: int):
    """
    **Serve the raw image bytes of an uploaded claim photo, for inline viewing**

    The Evidence & Photos tab previously only ever showed filename/size and
    AI-analysis anomalies -- there was no way to actually see the photo
    itself. This lets the UI put a real <img> next to that metadata.
    """
    try:
        with db_manager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT file_data, content_type FROM claim_photo_files WHERE id = ?",
                (photo_id,)
            )
            row = cursor.fetchone()
        if not row or not row["file_data"]:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Photo file not found")
        return Response(content=row["file_data"], media_type=row["content_type"] or "image/jpeg")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error serving photo file {photo_id}: {str(e)}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


@analysis_router.get("/claim/{claim_id}/lookup")
async def lookup_claim(claim_id: str):
    """
    **Minimal, safe claim lookup -- confirms a claim ID is real before an
    analyst navigates to it**

    Deliberately returns only enough to let an analyst confirm "yes, this is
    the right claim" (who it belongs to, where, when) -- no fraud/risk data,
    consistent with what's already shown to members about their own claims.
    Backs the "look up any claim" search on the analyst dashboard, since an
    analyst picking up a follow-up call about a claim they didn't personally
    file has no other way to find it (their dashboard only lists claims
    they filed themselves).
    """
    try:
        claim = db_manager.get_claim(claim_id)
        if not claim:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Claim {claim_id} not found")

        member = db_manager.get_member_info(claim.get("member_id")) if claim.get("member_id") else None

        return {
            "success": True,
            "claim_id": claim_id,
            "member_id": claim.get("member_id"),
            "member_name": member.get("name") if member else None,
            "location": claim.get("location"),
            "estimated_cost": claim.get("estimated_cost"),
            "created_at": claim.get("created_at"),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error looking up claim {claim_id}: {str(e)}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


@analysis_router.get("/claim/{claim_id}/photos")
async def list_claim_photos(claim_id: str, party: Optional[str] = Query(None)):
    """
    Lightweight photo listing (no raw bytes) for an already-submitted claim
    -- backs both the member's and the analyst's "view + add more photos"
    screens.
    """
    try:
        if not db_manager.get_claim(claim_id):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Claim {claim_id} not found")
        photos = db_manager.get_photos_metadata_by_claim(claim_id, party=party)
        return {"success": True, "claim_id": claim_id, "photos": photos}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing photos for {claim_id}: {str(e)}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


@analysis_router.post("/claim/{claim_id}/photos")
async def add_claim_photos(
    claim_id: str,
    uploader_type: str = Form(..., description="'member' or 'analyst'"),
    uploader_id: str = Form(..., description="member_id or analyst_id"),
    photos: List[UploadFile] = File(...),
):
    """
    **Add photos to an already-submitted claim**

    The original /api/analysis/member submission only supports one round of
    photo evidence. This covers everything after that: a member who didn't
    have photos ready at filing time, or an analyst adding photos a caller
    emailed in after a phone-filed claim (see /api/analysis/documents/upload
    for the equivalent on documents).

    Each photo gets the same per-photo CV analysis as the original
    submission (real anomaly detection, not inert storage) and is logged to
    the AI-ROL trail. This does NOT re-run narrative analysis or physics
    reconstruction for the claim as a whole -- those reflect the evidence
    available at original submission time, same boundary as document
    corrections (see /documents/{id}/correct).
    """
    try:
        claim = db_manager.get_claim(claim_id)
        if not claim:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Claim {claim_id} not found")

        if uploader_type == "member":
            if claim.get("member_id") != uploader_id:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to add photos to this claim")
        elif uploader_type == "analyst":
            with db_manager.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT 1 FROM analysts WHERE analyst_id = ? AND active_status = 1", (uploader_id,))
                if not cursor.fetchone():
                    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Unknown or inactive analyst")
        else:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="uploader_type must be 'member' or 'analyst'")

        orchestrator = get_claim_orchestrator()
        stored = []

        for photo in photos:
            if not photo.content_type or not photo.content_type.startswith('image/'):
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"{photo.filename} is not a valid image")
            content = await photo.read()
            if len(content) > 10 * 1024 * 1024:
                raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=f"{photo.filename} exceeds 10MB limit")

            photo_id = db_manager.store_photo_file(
                claim_id=claim_id, filename=photo.filename, party='member',
                file_data=content, content_type=photo.content_type,
                uploaded_by=uploader_id,
            )

            try:
                result = await orchestrator.photo_service.analyze_photo(
                    content, photo.filename, claim_id, party='member',
                )
                ai_rol.record_recommendation(
                    claim_id=claim_id,
                    capability="computer_vision",
                    recommendation=f"Late-added photo analyzed — {len(result.anomalies)} anomaly(ies) detected",
                    confidence=result.analysis_confidence / 100,
                    evidence={"filename": photo.filename, "risk_score": result.risk_score, "anomalies": result.anomalies},
                )
            except Exception as e:
                logger.warning(f"CV analysis failed for late-added photo {photo.filename} on {claim_id}: {e}")

            stored.append({"photo_id": photo_id, "filename": photo.filename})

        logger.info(f"📸 {len(stored)} photo(s) added to {claim_id} by {uploader_type} {uploader_id}")
        return {"success": True, "claim_id": claim_id, "photos_added": len(stored), "photos": stored}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error adding photos to {claim_id}: {str(e)}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


@analysis_router.post("/claim/{claim_id}/notify-member")
async def notify_member_to_add_photos(
    claim_id: str,
    requested_by: str = Form(..., description="analyst_id or admin_id triggering this"),
):
    """
    **Email the member a link to add photos to a claim filed on their behalf**

    For phone-filed claims with no photos yet -- lets the analyst hand off
    getting real evidence to the person who actually has the vehicle in
    front of them, instead of that evidence never arriving at all. Email is
    best-effort: if GMAIL_ADDRESS/GMAIL_APP_PASSWORD aren't configured on
    this deployment, this returns success=false with a clear reason rather
    than failing the request outright -- filing a claim should never be
    blocked by whether notification email happens to be set up.
    """
    try:
        claim = db_manager.get_claim(claim_id)
        if not claim:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Claim {claim_id} not found")

        member_id = claim.get("member_id")
        member = db_manager.get_member_info(member_id) if member_id else None
        if not member or not member.get("email"):
            return {"success": False, "reason": "No email on file for this claim's member"}

        sent = email_service.send_add_photos_email(
            to_email=member["email"],
            member_name=member.get("name") or "there",
            member_id=member_id,
            claim_id=claim_id,
        )

        if sent:
            logger.info(f"📧 Notified member {member_id} about claim {claim_id} (requested by {requested_by})")
            return {"success": True, "sent_to": member["email"]}
        else:
            return {"success": False, "reason": "Email is not configured on this deployment"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error notifying member for {claim_id}: {str(e)}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


@analysis_router.post("/documents/{document_id}/correct")
async def correct_document(
    document_id: int,
    corrected_fields: str = Form(..., description="JSON object of corrected field values"),
    corrected_by: str = Form(..., description="member_id or assessor_id making the correction"),
):
    """
    **Member or assessor corrects a field OCR got wrong on their own uploaded document**

    The original OCR output (raw_text / parsed_fields) is never overwritten
    -- it stays as the ground truth for audit. The correction is stored
    alongside it, so admin can see both and a self-serving "correction" that
    changes a fraud-relevant field (e.g. an OB number) is visible to a
    reviewer, not silently trusted the way the raw OCR read would be.

    Note: this updates the stored document record only. It does not
    re-trigger narrative analysis or physics reconstruction, which already
    ran against the original OCR output at submission time.
    """
    try:
        doc = db_manager.get_document_by_id(document_id)
        if not doc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

        claim_id = doc["claim_id"]
        party = doc["party"]

        authorized = False
        if party == "member":
            claim = db_manager.get_claim(claim_id)
            authorized = bool(claim and claim.get("member_id") == corrected_by)
        elif party == "assessor":
            with db_manager.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT 1 FROM claim_assignments WHERE claim_id = ? AND assessor_id = ?",
                    (claim_id, corrected_by)
                )
                authorized = cursor.fetchone() is not None
        else:
            # repair_shop / other -- no ownership table wired up for this
            # party yet, so no strict check within current PoC scope.
            authorized = True

        if not authorized:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not authorized to correct this document"
            )

        try:
            fields_dict = json.loads(corrected_fields)
        except json.JSONDecodeError:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="corrected_fields must be valid JSON")

        db_manager.correct_document_fields(document_id, fields_dict, corrected_by)

        return {"success": True, "document_id": document_id, "corrected_fields": fields_dict}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error correcting document {document_id}: {str(e)}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


@analysis_router.post("/member")
async def submit_member_claim(
    background_tasks: BackgroundTasks,
    claim_id: str = Form(..., description="Unique claim identifier"),
    member_id: str = Form(..., description="Member/policy holder ID"),
    narrative: str = Form(..., description="Member's description of the accident"),
    estimated_cost: float = Form(..., description="Member's estimated repair cost"),
    location: str = Form(..., description="Accident location"),
    incident_date: str = Form(..., description="Date of incident (YYYY-MM-DD)"),
    # Required for a member self-filing (they have the vehicle in front of
    # them), but not for a claims analyst filing over the phone -- they have
    # no photos to upload in the moment. The assessor's own on-site
    # inspection photos (captured later, independently) still provide real
    # visual evidence for the claim either way.
    photos: List[UploadFile] = File(default=[], description="Photos from member -- required when the member self-files, optional when a claims analyst files on their behalf"),
    third_party_involved: Optional[str] = Form(None),
    third_party_details: Optional[str] = Form(None),
    third_party_fled: Optional[str] = Form(None),
    police_reported: Optional[str] = Form(None),
    police_ob_number: Optional[str] = Form(None),
    witnesses_present: Optional[str] = Form(None),
    witness_details: Optional[str] = Form(None),
    injuries_reported: Optional[str] = Form(None),
    injury_details: Optional[str] = Form(None),
    id_document: Optional[UploadFile] = File(None, description="Photo of national ID or driving licence — optional"),
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

        # OCR runs in the background task (it's a vision-LLM call, same cost
        # class as photo analysis) — only the raw bytes are read here so the
        # acknowledgment below doesn't wait on it. (Police abstract is no
        # longer uploaded here -- it's uploaded and OCR'd immediately,
        # earlier in the intake wizard, right when the claimant confirms
        # police were involved -- see /api/analysis/documents/upload.)
        id_document_data = None
        if id_document:
            id_document_data = (await id_document.read(), id_document.filename)

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
            claim_id, enriched_narrative, photo_data, estimated_cost, location,
            id_document_data,
        )

        logger.info(f"Member submission accepted for {claim_id} — analysis running in background")

        return {
            "success": True,
            "claim_id": claim_id,
            "message": "Your claim has been submitted successfully",
            "submission_details": {
                "photos_uploaded": len(photo_data),
                "documents_uploaded": len(db_manager.get_documents_by_claim(claim_id, party="member")) + (1 if id_document_data else 0),
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
    photos: List[UploadFile] = File(..., description="Photos from assessor inspection"),
    crush_depth_mm: Optional[float] = Form(None, description="Measured crush/deformation depth in mm"),
    approach_angle_deg: Optional[float] = Form(None, description="Assessed impact angle in degrees"),
    third_party_vehicle_confirmed: Optional[str] = Form(None, description="Third-party vehicle details confirmed on-site"),
    garage_quote: Optional[UploadFile] = File(None, description="Photo of a garage repair quote collected on-site — optional"),
    id_document: Optional[UploadFile] = File(None, description="Photo of claimant/third-party ID verified on-site — optional"),
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

        garage_quote_data = None
        if garage_quote:
            garage_quote_data = (await garage_quote.read(), garage_quote.filename)

        id_document_data = None
        if id_document:
            id_document_data = (await id_document.read(), id_document.filename)

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
        measurement_block = build_assessor_measurement_block(
            crush_depth_mm, approach_angle_deg, third_party_vehicle_confirmed
        )
        enriched_report = f"{measurement_block}\n\nASSESSOR REPORT:\n{clean_report}" if measurement_block else clean_report

        background_tasks.add_task(
            _process_assessor_analysis_background,
            claim_id, member_narrative, member_photo_data, enriched_report,
            assessor_photo_data, estimated_cost, location,
            crush_depth_mm, approach_angle_deg, assessor_id,
            garage_quote_data, id_document_data,
        )

        logger.info(f"Assessor submission accepted for {claim_id} — analysis running in background")

        return {
            "success": True,
            "claim_id": claim_id,
            "message": "Your assessment has been submitted successfully",
            "submission_details": {
                "photos_uploaded": len(assessor_photo_data),
                "documents_uploaded": sum(1 for d in (garage_quote_data, id_document_data) if d),
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

        # Supporting documents (police abstract / ID / garage quote) + their
        # OCR extraction — not previously surfaced anywhere in the UI.
        documents = db_manager.get_documents_by_claim(claim_id)

        # Assessor's historical measurement-discrepancy rate, if this claim
        # has an assigned assessor — same collusion-pattern signal used to
        # gate the pattern-risk flag in physics reconstruction, surfaced
        # here for a human reviewer instead of only ever affecting scoring.
        assessor_track_record = None
        with db_manager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT assessor_id FROM claim_assignments WHERE claim_id = ? ORDER BY assigned_at DESC LIMIT 1",
                (claim_id,)
            )
            assignment_row = cursor.fetchone()
        if assignment_row and assignment_row["assessor_id"]:
            assessor_track_record = db_manager.get_assessor_track_record(assignment_row["assessor_id"])

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
                "measurement_flags":   physics_summary.get('measurement_flags', []),
                "has_measurement_discrepancy": physics_summary.get('has_measurement_discrepancy', False),
            },
            "documents": [
                {
                    "id":                    d.get("id"),
                    "party":                 d.get("party"),
                    "document_type":         d.get("document_type"),
                    "filename":              d.get("filename"),
                    "raw_text":              d.get("raw_text"),
                    "parsed_fields":         d.get("parsed_fields"),
                    "extraction_confidence": d.get("extraction_confidence"),
                    "extraction_method":     d.get("extraction_method"),
                    "uploaded_at":           d.get("uploaded_at"),
                    "corrected_fields":      d.get("corrected_fields"),
                    "corrected_by":          d.get("corrected_by"),
                    "corrected_at":          d.get("corrected_at"),
                }
                for d in documents
            ],
            "assessor_track_record": assessor_track_record,
            "claim_decision": db_manager.get_latest_claim_decision(claim_id),
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


VALID_CLAIM_DECISIONS = {"PAY", "DENY", "ESCALATE"}


@analysis_router.get("/claim/{claim_id}/decision")
async def get_claim_decision(claim_id: str):
    """
    Current (and full history of) triage decision(s) for a claim -- distinct
    from `final_assessment.decision` in the full-report, which is a
    recomputed AI suggestion, never persisted. This is the actual recorded
    human call.
    """
    try:
        latest = db_manager.get_latest_claim_decision(claim_id)
        history = db_manager.get_claim_decision_history(claim_id)
        return {"claim_id": claim_id, "current_decision": latest, "history": history}
    except Exception as e:
        logger.error(f"Error retrieving decision for {claim_id}: {str(e)}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


@analysis_router.post("/claim/{claim_id}/decision")
async def record_claim_decision(
    claim_id: str,
    decision: str = Form(..., description="PAY, DENY, or ESCALATE"),
    decided_by: str = Form(..., description="admin_id of the reviewer recording this decision"),
    reason: Optional[str] = Form(None, description="Required for DENY and ESCALATE"),
    payout_amount: Optional[float] = Form(None, description="Only meaningful for PAY"),
):
    """
    **Record the final triage decision on a claim: PAY / DENY / ESCALATE**

    This is the human reviewer's actual, persisted business decision --
    nothing in this system recorded that before (the "Recommended Action"
    shown throughout the admin report is only ever a freshly recomputed AI
    suggestion, never written to the database). A claim can be re-decided
    later (e.g. an ESCALATE resolved afterward into a PAY or DENY); this
    table is append-only and callers read the latest row.
    """
    try:
        decision = decision.upper().strip()
        if decision not in VALID_CLAIM_DECISIONS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"decision must be one of {sorted(VALID_CLAIM_DECISIONS)}"
            )
        if decision in ("DENY", "ESCALATE") and not (reason and reason.strip()):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"A reason is required to {decision.lower()} a claim"
            )

        claim = db_manager.get_claim(claim_id)
        if not claim:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Claim {claim_id} not found")

        decision_id = db_manager.record_claim_decision(
            claim_id=claim_id,
            decision=decision,
            decided_by=decided_by,
            reason=reason,
            payout_amount=payout_amount if decision == "PAY" else None,
        )

        # Also logged into the AI-ROL trail as a handler action against the
        # ai_advisory capability so it shows up in that audit view too,
        # alongside every other reviewer action on this claim.
        try:
            ai_rol.record_handler_action(
                claim_id=claim_id,
                handler_id=decided_by,
                action="override" if decision != "ESCALATE" else "escalate",
                capability="ai_advisory",
                reason=reason or f"Claim {decision.lower()}ed",
            )
        except Exception as e:
            logger.warning(f"Could not mirror claim decision into AI-ROL trail for {claim_id}: {e}")

        return {
            "success": True,
            "claim_id": claim_id,
            "decision_id": decision_id,
            "decision": decision,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error recording decision for {claim_id}: {str(e)}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


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
 
 
@system_router.get("/logs")
async def tail_backend_logs(lines: int = Query(80, ge=1, le=1000)):
    """
    **Tail the backend's own process log, as plain text**

    For checking "is it actually processing or stuck" during a live demo
    without needing SSH access -- just open this URL in a browser tab.
    Reads whatever log file this process was launched with stdout/stderr
    redirected to (see devserver run command); if that's not discoverable,
    falls back to an explicit message rather than a stack trace.
    """
    log_path = os.environ.get("BACKEND_LOG_PATH", os.path.expanduser("~/debra_projects/backend_devserver.log"))
    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            all_lines = f.readlines()
        tail = "".join(all_lines[-lines:])
        return PlainTextResponse(tail or "(log file is empty)")
    except FileNotFoundError:
        return PlainTextResponse(
            f"Log file not found at {log_path}. Set BACKEND_LOG_PATH if it's launched with a different redirect target.",
            status_code=404,
        )
    except Exception as e:
        return PlainTextResponse(f"Could not read log file: {str(e)}", status_code=500)


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
 