
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


def _resolve_claim_type(policy_id: Optional[str]) -> str:
    """
    The `claims` table deliberately has no claim_type column (see
    database.py) -- claim type is only ever knowable via the claim's own
    policy_id -> policies.policy_type. Every background analysis worker
    below was previously hardcoding claim_type="motor" regardless of what
    the claim's actual policy was, which meant a Domestic/Marine claim's
    entire analysis (business rules, physics-applicability classification)
    silently ran as if it were Motor. Falls back to "motor" only when the
    policy genuinely can't be resolved, not as a default assumption.
    """
    if not policy_id:
        return "motor"
    try:
        with db_manager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT policy_type FROM policies WHERE policy_id = ?", (policy_id,))
            row = cursor.fetchone()
            return row["policy_type"] if row and row["policy_type"] else "motor"
    except Exception as e:
        logger.warning(f"Could not resolve claim_type for policy {policy_id}: {e}")
        return "motor"


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
    other_vehicle_position: Optional[str] = None,
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
        # Claimant-confirmed collision geometry -- physics reconstruction
        # previously had to guess this from narrative wording (or defaulted
        # to a fixed 90-degree T-bone layout regardless of what actually
        # happened), which produced a simulation that could contradict the
        # claimant's own account. Asked directly at intake, using the exact
        # phrasing ("left side"/"right side"/"rear"/"front") already
        # recognized by the narrative-keyword impact-zone inference, so it
        # can be prioritized as a confirmed fact over any narrative guess.
        if other_vehicle_position:
            lines.append(f"Position of other vehicle at moment of impact (claimant-confirmed): {other_vehicle_position}")
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
    extra_incident_details: Optional[Dict[str, Any]] = None,
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

        # Marine/Domestic/Goods-in-Transit fields the intake form captured
        # directly (vessel_id, shipment_id, navigation_zone, vehicle_reg,
        # item_serial_number, etc.) -- see submit_member_claim's
        # structured_details param. Doesn't override the OCR-derived keys
        # above (police_reported/ob_number), which take precedence since
        # they're independently verified rather than analyst-entered.
        if extra_incident_details:
            incident_details = {**extra_incident_details, **incident_details}

        # Re-check for assessor/repair-shop data that may have arrived WHILE
        # this task was queued or running (this pipeline routinely takes
        # minutes) -- without this, whichever of the member/assessor/
        # repair-shop background tasks happens to finish last wins the
        # final store_claim() write outright, silently erasing the other
        # parties' submissions from analysis_result even though they were
        # genuinely submitted (see CLM-2026-000040: assessor submitted and
        # its own analysis briefly reflected that, then the member task
        # finished afterward with no knowledge of it and wiped
        # assessor_submission back to None). Mirrors the same recovery
        # pattern submit_repair_shop_estimate already uses, just re-checked
        # here at execution time instead of request time, since request
        # time is exactly when this race can still be lost.
        assessor_report, assessor_photos = None, None
        repair_estimate, repair_photos = None, None
        try:
            existing_analysis = claim_row.get("analysis_result")
            if isinstance(existing_analysis, str):
                existing_analysis = json.loads(existing_analysis) if existing_analysis else {}
            existing_analysis = existing_analysis or {}

            assessor_submission = existing_analysis.get("assessor_submission")
            if assessor_submission and assessor_submission.get("report"):
                assessor_report = assessor_submission["report"]
                assessor_photo_rows = db_manager.get_photos_by_claim_and_party(claim_id, "assessor")
                assessor_photos = [(p["file_data"], p["filename"]) for p in assessor_photo_rows]

            repair_submission = existing_analysis.get("repair_shop_submission")
            if repair_submission and repair_submission.get("estimate"):
                repair_estimate = repair_submission["estimate"]
                repair_photo_rows = db_manager.get_photos_by_claim_and_party(claim_id, "repair_shop")
                repair_photos = [(p["file_data"], p["filename"]) for p in repair_photo_rows]
        except Exception as e:
            logger.warning(f"Could not recover existing assessor/repair-shop data for {claim_id}: {e}")

        orchestrator = get_claim_orchestrator()
        analysis_result = await orchestrator.analyze_multiparty_claim(
            claim_id=claim_id,
            member_narrative=enriched_narrative,
            member_photos=photo_data,
            assessor_report=assessor_report,
            assessor_photos=assessor_photos,
            repair_estimate=repair_estimate,
            repair_photos=repair_photos,
            estimated_cost=estimated_cost,
            location=location,
            member_id=claim_row.get("member_id"),
            policy_id=claim_row.get("policy_id"),
            claim_type=_resolve_claim_type(claim_row.get("policy_id")),
            incident_details=incident_details,
        )

        new_video_path = analysis_result.get("simulation_video_path")
        if new_video_path:
            with db_manager.get_connection() as conn:
                conn.execute(
                    "UPDATE claims SET simulation_video_path = ? WHERE claim_id = ?",
                    (new_video_path, claim_id)
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

        claim_row = db_manager.get_claim(claim_id) or {}

        # Same re-check as _process_member_claim_background above -- pick up
        # a repair-shop submission that may have arrived while this task was
        # queued/running, instead of silently erasing it on store_claim().
        repair_estimate, repair_photos = None, None
        try:
            existing_analysis = claim_row.get("analysis_result")
            if isinstance(existing_analysis, str):
                existing_analysis = json.loads(existing_analysis) if existing_analysis else {}
            existing_analysis = existing_analysis or {}

            repair_submission = existing_analysis.get("repair_shop_submission")
            if repair_submission and repair_submission.get("estimate"):
                repair_estimate = repair_submission["estimate"]
                repair_photo_rows = db_manager.get_photos_by_claim_and_party(claim_id, "repair_shop")
                repair_photos = [(p["file_data"], p["filename"]) for p in repair_photo_rows]
        except Exception as e:
            logger.warning(f"Could not recover existing repair-shop data for {claim_id}: {e}")

        orchestrator = get_claim_orchestrator()
        analysis_result = await orchestrator.analyze_multiparty_claim(
            claim_id=claim_id,
            member_narrative=member_narrative,
            member_photos=member_photo_data,
            assessor_report=enriched_report,
            assessor_photos=assessor_photo_data,
            repair_estimate=repair_estimate,
            repair_photos=repair_photos,
            estimated_cost=estimated_cost,
            location=location,
            assessor_crush_depth_mm=assessor_crush_depth_mm,
            assessor_approach_angle_deg=assessor_approach_angle_deg,
            assessor_id=assessor_id,
            member_id=claim_row.get("member_id"),
            policy_id=claim_row.get("policy_id"),
            claim_type=_resolve_claim_type(claim_row.get("policy_id")),
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
        claim_row = db_manager.get_claim(claim_id) or {}

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
            location=location,
            member_id=claim_row.get("member_id"),
            policy_id=claim_row.get("policy_id"),
            claim_type=_resolve_claim_type(claim_row.get("policy_id")),
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
            claim_type=_resolve_claim_type(claim.get("policy_id")),
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


async def _reprocess_claim_after_late_photos_background(claim_id: str):
    """
    Fully re-runs the multi-party analysis (photo CV/damage detection,
    narrative, physics reconstruction, business rules, risk scoring) after
    a claim that already went through (or was supposed to go through)
    initial analysis changes in some way that should be reflected in
    analysis_result -- late photos, or a member filling in a field the
    original paper-form OCR left blank (see member_update_claim_fields).

    Without this, a late-added photo's CV analysis was computed once (for
    the AI-ROL audit trail) and then discarded -- never shown to the
    assessor, and the claim's risk/decision stayed frozen at whatever the
    evidence-free original submission produced. Same problem for a
    paper-form claim filed with a missing estimated_cost/location/
    narrative: analyze_multiparty_claim never runs at intake time for those
    (analysis_result stays "{}"), so the claim was both invisible on the
    analyst claims list (which skips any claim with no analysis_result) and,
    even once it did get analyzed via some other trigger, its estimated_cost
    stayed frozen at whatever analysis_result captured that run -- never the
    member's later answer, since update_claim_member_fields only writes the
    raw `claims` column and previously never triggered a re-run at all.
    This re-runs the exact same analyze_multiparty_claim() pipeline the
    original submission used, over ALL currently-stored member photos (not
    just the newest one, and possibly zero -- a pure field-fill has none)
    plus the claim's CURRENT narrative/estimated_cost/location, so the
    result reflects whatever is true right now, not what was true at
    initial filing.
    """
    try:
        claim = db_manager.get_claim(claim_id)
        if not claim:
            logger.error(f"[background] Reprocess skipped — claim {claim_id} not found")
            return

        with db_manager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT filename, file_data FROM claim_photo_files WHERE claim_id = ? AND party = 'member' ORDER BY uploaded_at ASC",
                (claim_id,),
            )
            member_photos = [(row["file_data"], row["filename"]) for row in cursor.fetchall()]

        # Zero photos is a valid, common case here (a paper-form claim with
        # no photos at all, reprocessed purely because a pending field was
        # just filled in) -- analyze_multiparty_claim handles an empty photo
        # list fine, so this no longer bails out on that alone.
        orchestrator = get_claim_orchestrator()
        analysis_result = await orchestrator.analyze_multiparty_claim(
            claim_id=claim_id,
            member_narrative=claim.get("narrative") or "",
            member_photos=member_photos,
            estimated_cost=claim.get("estimated_cost") or 0.0,
            location=claim.get("location") or "",
            member_id=claim.get("member_id"),
            policy_id=claim.get("policy_id"),
            claim_type=_resolve_claim_type(claim.get("policy_id")),
        )

        # Physics reconstruction (and its rendered video) only re-runs when
        # it hasn't already run for this claim (see "Physics already run —
        # skipping" in service.py) -- so on a re-run, analysis_result often
        # has no simulation_video_path at all. Only overwrite the existing
        # path when a real one comes back; an unconditional UPDATE here
        # would otherwise wipe out a perfectly good, already-rendered video
        # every time a claim gets re-processed after its first analysis.
        new_video_path = analysis_result.get("simulation_video_path")
        if new_video_path:
            with db_manager.get_connection() as conn:
                conn.execute(
                    "UPDATE claims SET simulation_video_path = ? WHERE claim_id = ?",
                    (new_video_path, claim_id),
                )
                conn.commit()

        logger.info(
            f"[background] Reprocessed {claim_id} "
            f"over {len(member_photos)} total member photo(s) "
            f"(Risk: {analysis_result['fraud_risk_score']}/100)"
        )
    except Exception as e:
        logger.error(f"[background] Reprocess after late photos failed for {claim_id}: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())


@analysis_router.post("/speech-to-text")
async def speech_to_text(
    audio: UploadFile = File(..., description="Recorded audio clip (webm/mp4/ogg/wav from MediaRecorder)"),
):
    """
    **Transcribe a recorded voice answer to text**

    Server-side (Whisper, via faster-whisper), not the browser's Web Speech
    API — Safari/iOS never implemented that, and most of this team is on
    iPhone. The frontend records with MediaRecorder (works on every modern
    browser) and uploads the clip here.
    """
    try:
        content = await audio.read()
        if len(content) > 15 * 1024 * 1024:
            raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Recording exceeds 15MB limit")
        if not content:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Empty recording")

        import speech_to_text as stt
        result = await asyncio.to_thread(stt.transcribe_audio, content, audio.filename or "audio.webm")
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error transcribing audio: {str(e)}")
        return {"success": False, "error": "Transcription failed — you can type your answer instead."}


@analysis_router.post("/photos/damage-decision")
async def save_damage_decision(
    claim_id: str = Form(...),
    filename: str = Form(...),
    detection_index: int = Form(...),
    component: str = Form(...),
    ai_recommendation: str = Form(...),
    assessor_decision: str = Form(..., description="'repair' or 'replace'"),
    assessor_id: str = Form(...),
):
    """Assessor's own repair/replace call on one AI-detected damage component. AI recommends, assessor decides."""
    if assessor_decision not in ("repair", "replace"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="assessor_decision must be 'repair' or 'replace'")
    ok = db_manager.save_photo_damage_decision(
        claim_id, filename, detection_index, component, ai_recommendation, assessor_decision, assessor_id,
    )
    if not ok:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Could not save decision")
    return {"success": True}


@analysis_router.get("/claim/{claim_id}/damage-decisions")
async def get_damage_decisions(claim_id: str):
    """All of an assessor's repair/replace decisions recorded for this claim's photos."""
    decisions = db_manager.get_photo_damage_decisions(claim_id)
    return {"success": True, "decisions": decisions}


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
        # content_type isn't reliable alone -- some browsers/OSes send
        # application/octet-stream for .webp (and other) image uploads, which
        # silently 400'd every such upload even though the file was a real
        # image. Fall back to the filename extension in that case.
        IMAGE_EXTENSIONS = ('.jpg', '.jpeg', '.png', '.webp', '.gif', '.bmp', '.heic', '.heif')
        looks_like_image = (file.content_type or '').startswith('image/') or (file.filename or '').lower().endswith(IMAGE_EXTENSIONS)
        if not looks_like_image:
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


@analysis_router.post("/claim-form/extract")
async def extract_claim_form(
    analyst_id: str = Form(..., description="Analyst performing the extraction, for logging"),
    files: List[UploadFile] = File(..., description="One image per page of the filled paper claim form, OR a single multi-page PDF"),
):
    """
    **ANALYST ONLY: OCR a scanned/photographed physical claim form BEFORE a
    claim exists.**

    This is the real analyst workflow: a member fills in and hands over the
    paper "Motor Accident Claim Form" (policy details, vehicle, accident
    circumstances, damage, driver/owner statements...), and the analyst's
    job is to get that form's contents into the system -- not to re-type it
    while re-interviewing the member over the phone (the older
    ClaimChatbot(analystMode) flow this replaces for form-based intake).

    Runs all page images through one vision-model call so multi-page
    answers (e.g. a statement that continues onto page 2) get merged
    correctly, rather than extracting each page in isolation.

    Stateless -- no claim_id exists yet at this point, so nothing is
    persisted here. The analyst reviews/corrects the returned fields in the
    UI, then the normal /check-coverage -> /create-claim -> /api/analysis/member
    pipeline runs with the (possibly-edited) extracted values. The original
    page images can be attached afterward via /documents/upload with
    document_type=claim_form once a claim_id exists.
    """
    try:
        if not files:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="At least one page image or a PDF is required")
        if len(files) > 8:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Too many pages (max 8)")

        images = []
        for f in files:
            is_pdf = f.content_type == "application/pdf" or (f.filename or "").lower().endswith(".pdf")
            if not is_pdf and not (f.content_type and f.content_type.startswith("image/")):
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"{f.filename} must be an image or a PDF")
            content = await f.read()
            if len(content) > 20 * 1024 * 1024:
                raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=f"{f.filename} exceeds 20MB")

            if is_pdf:
                try:
                    import fitz
                    pdf = fitz.open(stream=content, filetype="pdf")
                    if pdf.page_count > 8:
                        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"{f.filename} has too many pages (max 8)")
                    for page in pdf:
                        pix = page.get_pixmap(dpi=200)
                        images.append(pix.tobytes("png"))
                    pdf.close()
                except HTTPException:
                    raise
                except Exception as e:
                    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Couldn't read {f.filename} as a PDF: {e}")
            else:
                images.append(content)

        from document_ocr import extract_document_data
        ocr_result = await extract_document_data(images, files[0].filename, "claim_form")

        logger.info(f"Analyst {analyst_id} extracted claim form from {len(files)} page(s), confidence={ocr_result.get('extraction_confidence')}")

        return {
            "success": True,
            "parsed_fields": ocr_result.get("parsed_fields", {}),
            "extraction_confidence": ocr_result.get("extraction_confidence", 0),
            "document_appears_genuine": ocr_result.get("document_appears_genuine"),
            "quality_notes": ocr_result.get("quality_notes", ""),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error extracting claim form: {str(e)}")
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


@analysis_router.post("/claim/{claim_id}/tracking-data")
async def upload_tracking_data(
    claim_id: str,
    party: str = Form(..., description="'member' or 'assessor'"),
    data_type: str = Form(..., description="'ais_gps' / 'telematics' / 'temperature_log'"),
    uploader_id: str = Form(..., description="member_id or assessor_id, for ownership verification"),
    file: UploadFile = File(...),
):
    """
    **Upload an AIS/GPS track, telematics log, or temperature-logger file**

    Evidence for Marine Hull collision/grounding, Goods in Transit
    overturning, and Marine Cargo temperature-excursion claims -- these are
    CSV/log files, not images, so this is deliberately a separate endpoint
    from /documents/upload rather than overloading that one's image-only
    validation. Stores the raw file only; row-by-row parsing into a
    structured timeline (for an actual physics/plausibility check) is a
    later phase -- this makes the evidence attachable and retrievable now
    rather than blocking on that.
    """
    try:
        if len(file.filename or "") == 0:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="File must have a filename")
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
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="party must be 'member' or 'assessor'")

        if not authorized:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to upload tracking data for this claim")

        tracking_id = db_manager.store_tracking_data(
            claim_id=claim_id, party=party, data_type=data_type,
            filename=file.filename, file_data=content,
        )
        logger.info(f"Tracking data uploaded for {claim_id}: {file.filename} ({data_type}) -> id {tracking_id}")
        return {"success": True, "tracking_id": tracking_id, "claim_id": claim_id, "data_type": data_type}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error uploading tracking data for {claim_id}: {str(e)}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


@analysis_router.get("/claim/{claim_id}/tracking-data")
async def list_tracking_data(claim_id: str, party: Optional[str] = Query(None)):
    """**List AIS/GPS/telematics/temperature-log files attached to a claim** (metadata only, not the raw file)."""
    try:
        records = db_manager.get_tracking_data_by_claim(claim_id, party=party)
        return {"success": True, "claim_id": claim_id, "tracking_data": records}
    except Exception as e:
        logger.error(f"Error listing tracking data for {claim_id}: {str(e)}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


@analysis_router.get("/tracking-data/{tracking_id}/file")
async def get_tracking_data_file(tracking_id: int):
    """**Download the raw AIS/GPS/telematics/temperature-log file** attached via /tracking-data."""
    try:
        with db_manager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT file_data, filename FROM claim_tracking_data WHERE id = ?",
                (tracking_id,)
            )
            row = cursor.fetchone()
        if not row or not row["file_data"]:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tracking data file not found")
        return Response(
            content=row["file_data"], media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{row["filename"]}"'},
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error serving tracking data file {tracking_id}: {str(e)}")
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


@analysis_router.get("/claims/list")
async def list_all_claims(limit: int = Query(300, description="Max claims to return, most recent first")):
    """
    **Every claim in the system, lightweight -- backs the analyst dashboard's
    claim picker**

    Same "no fraud/risk data" boundary as /claim/{claim_id}/lookup above,
    just for all claims at once instead of one at a time -- lets an analyst
    pick any claim from a dropdown instead of having to already know and
    type its exact ID.
    """
    try:
        with db_manager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT c.claim_id, c.member_id, c.location, c.created_at, m.name AS member_name
                FROM claims c
                LEFT JOIN members m ON m.member_id = c.member_id
                ORDER BY c.created_at DESC
                LIMIT ?
                """,
                (limit,),
            )
            claims = [dict(row) for row in cursor.fetchall()]
        return {"success": True, "claims": claims, "total": len(claims)}
    except Exception as e:
        logger.error(f"Error listing all claims: {str(e)}")
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
    background_tasks: BackgroundTasks,
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

    Storing the photo is synchronous (the response confirms it's saved);
    the full multi-party re-analysis (photo CV/damage detection, narrative,
    physics, business rules, risk scoring -- see
    _reprocess_claim_after_late_photos_background) runs in the background
    over ALL of the claim's member photos so far, same as original
    submission would have. Until it finishes, the assessor's damage-
    detection view reflects the claim's prior evidence only.
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
            stored.append({"photo_id": photo_id, "filename": photo.filename})

        if stored:
            background_tasks.add_task(_reprocess_claim_after_late_photos_background, claim_id)

        logger.info(f"{len(stored)} photo(s) added to {claim_id} by {uploader_type} {uploader_id} — full re-analysis queued")
        return {"success": True, "claim_id": claim_id, "photos_added": len(stored), "photos": stored}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error adding photos to {claim_id}: {str(e)}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


# Claim fields a member can answer themselves when the paper form left them
# blank -- kept in lockstep with DatabaseManager.MEMBER_EDITABLE_CLAIM_FIELDS,
# since these are the only keys that can end up in claims.pending_member_fields.
MEMBER_FIELD_LABELS = {
    "estimated_cost": "Estimated Repair Cost (KES)",
    "location": "Incident Location",
    "narrative": "What Happened (narrative)",
}


@analysis_router.post("/claim/{claim_id}/notify-member")
async def notify_member_to_add_photos(
    claim_id: str,
    requested_by: str = Form(..., description="analyst_id or admin_id triggering this"),
    missing_fields: Optional[str] = Form(
        None, description='JSON list of claim field keys left blank on the paper form, e.g. ["estimated_cost"]'
    ),
):
    """
    **Email the member a link to add photos (and answer any blank fields) to a claim filed on their behalf**

    For phone- or paper-form-filed claims with no photos yet -- lets the
    analyst hand off getting real evidence to the person who actually has
    the vehicle in front of them, instead of that evidence never arriving
    at all. `missing_fields` additionally flags claim fields the paper form
    left blank (e.g. estimated repair cost) so the member's claim page
    prompts them to fill those in too. Email is best-effort: if
    GMAIL_ADDRESS/GMAIL_APP_PASSWORD aren't configured on this deployment,
    this returns success=false with a clear reason rather than failing the
    request outright -- filing a claim should never be blocked by whether
    notification email happens to be set up.
    """
    try:
        claim = db_manager.get_claim(claim_id)
        if not claim:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Claim {claim_id} not found")

        member_id = claim.get("member_id")
        member = db_manager.get_member_info(member_id) if member_id else None
        if not member or not member.get("email"):
            return {"success": False, "reason": "No email on file for this claim's member"}

        missing_list: List[str] = []
        if missing_fields:
            try:
                missing_list = [f for f in json.loads(missing_fields) if f in MEMBER_FIELD_LABELS]
            except (json.JSONDecodeError, TypeError):
                missing_list = []

        if missing_list:
            with db_manager.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    'UPDATE claims SET pending_member_fields = ? WHERE claim_id = ?',
                    (json.dumps(missing_list), claim_id),
                )
                conn.commit()

        sent = email_service.send_add_photos_email(
            to_email=member["email"],
            member_name=member.get("name") or "there",
            member_id=member_id,
            claim_id=claim_id,
            missing_field_labels=[MEMBER_FIELD_LABELS[f] for f in missing_list],
        )

        if sent:
            logger.info(f"Notified member {member_id} about claim {claim_id} (requested by {requested_by})")
            return {"success": True, "sent_to": member["email"]}
        else:
            return {"success": False, "reason": "Email is not configured on this deployment"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error notifying member for {claim_id}: {str(e)}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


@analysis_router.post("/claim/{claim_id}/member-update-fields")
async def member_update_claim_fields(
    background_tasks: BackgroundTasks,
    claim_id: str,
    member_id: str = Form(...),
    fields: str = Form(..., description='JSON object of {field_key: value}, e.g. {"estimated_cost": 45000}'),
):
    """
    **Member answers claim fields the paper form left blank**

    Restricted to DatabaseManager.MEMBER_EDITABLE_CLAIM_FIELDS -- a member
    can only fill in the handful of fields the notify-member email flagged
    as missing, not rewrite arbitrary claim state.
    """
    try:
        try:
            parsed_fields = json.loads(fields)
        except (json.JSONDecodeError, TypeError):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="fields must be valid JSON")

        result = db_manager.update_claim_member_fields(claim_id, member_id, parsed_fields)
        # Filling in a pending field (e.g. estimated_cost) previously only
        # ever touched the raw `claims` column -- a paper-form claim that
        # never had analyze_multiparty_claim run at intake (analysis_result
        # still "{}") stayed invisible on the analyst claims list forever,
        # and even an already-analyzed claim kept showing the pre-fill
        # value everywhere that reads analysis_result.estimated_cost. Same
        # background reprocessing photo uploads use, so this claim ends up
        # analyzed/visible with the member's real answer either way.
        if result.get("updated"):
            background_tasks.add_task(_reprocess_claim_after_late_photos_background, claim_id)
        return {"success": True, **result}

    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating member fields for {claim_id}: {str(e)}")
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
    other_vehicle_position: Optional[str] = Form(None, description="Claimant-confirmed position of the other vehicle at impact -- behind / oncoming / left side / right side"),
    police_reported: Optional[str] = Form(None),
    police_ob_number: Optional[str] = Form(None),
    witnesses_present: Optional[str] = Form(None),
    witness_details: Optional[str] = Form(None),
    injuries_reported: Optional[str] = Form(None),
    injury_details: Optional[str] = Form(None),
    id_document: Optional[UploadFile] = File(None, description="Photo of national ID or driving licence — optional"),
    structured_details: Optional[str] = Form(
        None,
        description=(
            "JSON object of claim-type-specific fields the business rules engine reads directly "
            "(not narrative text) -- e.g. Marine Hull: vessel_id, navigation_zone, incident_datetime; "
            "Marine Cargo: shipment_id; Goods in Transit: vehicle_reg, driver_name, transporter_name; "
            "Domestic: item_serial_number, incident_address, alarm_armed. Unrecognised keys are simply "
            "ignored by every rule that doesn't look for them -- this is intentionally generic rather "
            "than one Form parameter per claim type."
        ),
    ),
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

        parsed_structured_details: Dict[str, Any] = {}
        if structured_details:
            try:
                parsed_structured_details = json.loads(structured_details)
                if not isinstance(parsed_structured_details, dict):
                    parsed_structured_details = {}
            except json.JSONDecodeError:
                logger.warning(f"Could not parse structured_details JSON for {claim_id}, ignoring: {structured_details!r}")

        clean_narrative = ValidationUtils.sanitize_narrative(narrative)
        structured_block = build_structured_intake_block(
            third_party_involved, third_party_details, third_party_fled,
            other_vehicle_position,
            police_reported, police_ob_number,
            witnesses_present, witness_details,
            injuries_reported, injury_details,
        )
        enriched_narrative = f"{structured_block}\n\nCLAIMANT NARRATIVE:\n{clean_narrative}"

        # member_id doesn't depend on the AI analysis at all — write it now
        # rather than waiting for the background task, so the claim record
        # is correct immediately instead of appearing unlinked for minutes.
        # initial_estimated_cost is captured once here and never touched
        # again (COALESCE keeps whatever was first set) -- the repair-shop
        # stage's estimate then becomes the "final" figure the
        # cost_escalation business rule compares against.
        with db_manager.get_connection() as conn:
            conn.execute(
                "UPDATE claims SET member_id = ?, "
                "initial_estimated_cost = COALESCE(initial_estimated_cost, ?) WHERE claim_id = ?",
                (member_id, estimated_cost, claim_id)
            )
            conn.commit()

        # The AI chain (photo/narrative analysis, physics, video) routinely
        # takes minutes under shared-GPU load. The member never sees any of
        # its output (fraud data is deliberately hidden from submitters), so
        # it runs in the background — the acknowledgment below doesn't wait.
        background_tasks.add_task(
            _process_member_claim_background,
            claim_id, enriched_narrative, photo_data, estimated_cost, location,
            id_document_data, parsed_structured_details,
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

        # Captured once here, separate from claims.estimated_cost (which the
        # repair-shop stage later overwrites as the "final" figure) -- gives
        # the cost-divergence rule a real assessor-side number to compare
        # against the member's and repair shop's independently.
        with db_manager.get_connection() as conn:
            conn.execute(
                "UPDATE claims SET assessor_estimated_cost = ? WHERE claim_id = ?",
                (estimated_cost, claim_id)
            )
            conn.commit()

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

        # Best-effort -- never blocks the assessor's submission if email
        # fails/isn't configured.
        try:
            member = db_manager.get_member_info(existing_claim.get("member_id")) if existing_claim.get("member_id") else None
            if member and member.get("email"):
                email_service.send_assessor_report_email(
                    to_email=member["email"],
                    member_name=member.get("name") or "there",
                    member_id=existing_claim["member_id"],
                    claim_id=claim_id,
                )
        except Exception as e:
            logger.warning(f"Assessor-report email failed for {claim_id}: {str(e)}")

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

        # shop_id doesn't depend on the AI analysis -- persist it now so the
        # repeat_repair_shop business rule and graph relationship analysis
        # have something to check against (previously accepted here and
        # silently dropped).
        with db_manager.get_connection() as conn:
            conn.execute(
                "UPDATE claims SET repair_shop_id = ? WHERE claim_id = ?",
                (shop_id, claim_id)
            )
            conn.commit()

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
 
 
@analysis_router.get("/admin/analytics/overview")
async def get_admin_analytics_overview(days: int = Query(30, description="Window for the claims-over-time trend")):
    """
    **ADMIN ONLY: System-wide oversight — fraud trends, rule-trigger frequency,
    risk distribution, and AI-ROL audit activity across every claim.**

    Distinct from the paginated claims list (/admin claims endpoint): that
    view's "stats" only ever reflected whatever 20 claims were on the
    current page. This scans every claim once and aggregates properly, so
    the numbers here are true totals, not a page-sized sample.
    """
    try:
        with db_manager.get_connection() as conn:
            cursor = conn.cursor()

            # ── Risk distribution + global average (cheap — columns, no JSON) ──
            cursor.execute("SELECT risk_level, COUNT(*) as cnt FROM claims GROUP BY risk_level")
            risk_distribution = {row["risk_level"] or "unknown": row["cnt"] for row in cursor.fetchall()}

            cursor.execute("SELECT COUNT(*) as total, AVG(fraud_risk_score) as avg_score FROM claims")
            totals_row = cursor.fetchone()
            total_claims = totals_row["total"] or 0
            avg_risk_score = round(totals_row["avg_score"], 1) if totals_row["avg_score"] is not None else 0

            # ── Final human decisions (latest per claim) ────────────────────────
            cursor.execute('''
                SELECT decision, COUNT(*) as cnt FROM (
                    SELECT claim_id, decision,
                           ROW_NUMBER() OVER (PARTITION BY claim_id ORDER BY decided_at DESC) as rn
                    FROM claim_decisions
                ) WHERE rn = 1
                GROUP BY decision
            ''')
            decision_distribution = {row["decision"]: row["cnt"] for row in cursor.fetchall()}

            # ── AI-ROL activity: volume by capability + handler-action split ────
            cursor.execute("SELECT capability, COUNT(*) as cnt FROM ai_rol_records GROUP BY capability ORDER BY cnt DESC")
            ai_rol_by_capability = {row["capability"]: row["cnt"] for row in cursor.fetchall()}

            cursor.execute('''
                SELECT COALESCE(handler_action, 'pending') as action, COUNT(*) as cnt
                FROM ai_rol_records GROUP BY handler_action
            ''')
            handler_action_distribution = {row["action"]: row["cnt"] for row in cursor.fetchall()}

            # ── Claims filed per day, last N days ───────────────────────────────
            cursor.execute('''
                SELECT DATE(created_at) as day, COUNT(*) as cnt
                FROM claims
                WHERE created_at >= datetime('now', ?)
                GROUP BY DATE(created_at)
                ORDER BY day
            ''', (f'-{days} days',))
            claims_over_time = [{"date": row["day"], "count": row["cnt"]} for row in cursor.fetchall()]

            # ── Business-rule / relationship / similarity trigger frequency ─────
            # These live inside analysis_result JSON, not their own columns, so
            # this part scans -- fine at PoC claim volumes, would move to a
            # materialized rollup table if this ever runs against a real book.
            cursor.execute("SELECT analysis_result FROM claims WHERE analysis_result IS NOT NULL AND analysis_result != '{}'")
            rule_trigger_counts: Dict[str, int] = {}
            relationship_type_counts: Dict[str, int] = {}
            claims_with_relationship_findings = 0
            claims_with_similarity_matches = 0
            claims_analyzed = 0

            for row in cursor.fetchall():
                try:
                    ar = json.loads(row["analysis_result"])
                except (json.JSONDecodeError, TypeError):
                    continue
                if not ar:
                    continue
                claims_analyzed += 1

                business_rules_data = ar.get("business_rules") or {}
                for rule_id in business_rules_data.get("rules_fired", []):
                    rule_trigger_counts[rule_id] = rule_trigger_counts.get(rule_id, 0) + 1

                relationship_data = ar.get("relationship_analysis") or {}
                findings = relationship_data.get("findings", [])
                if findings:
                    claims_with_relationship_findings += 1
                for f in findings:
                    rtype = f.get("type", "unknown")
                    relationship_type_counts[rtype] = relationship_type_counts.get(rtype, 0) + 1

                similarity_data = ar.get("narrative_similarity") or {}
                if similarity_data.get("matches"):
                    claims_with_similarity_matches += 1

            top_rules = sorted(rule_trigger_counts.items(), key=lambda kv: kv[1], reverse=True)

            # ── Operational KPIs: live assignment workload, not just claim counts ──
            cursor.execute('''
                SELECT ca.status, ca.assigned_at, c.risk_level, c.estimated_cost
                FROM claim_assignments ca JOIN claims c ON c.claim_id = ca.claim_id
            ''')
            assignment_rows = [dict(r) for r in cursor.fetchall()]

        active_assessments = 0
        pending_review = 0
        completed_assessments = 0
        overdue_assessments = 0
        claim_value_under_assessment = 0.0
        for r in assignment_rows:
            computed = _classify_assignment_status(r)
            if computed == "Completed":
                completed_assessments += 1
            elif computed == "Overdue":
                overdue_assessments += 1
                active_assessments += 1
                claim_value_under_assessment += r.get("estimated_cost") or 0
            else:
                active_assessments += 1
                claim_value_under_assessment += r.get("estimated_cost") or 0
            if (r.get("status") or "").lower() == "pending":
                pending_review += 1

        return {
            "success": True,
            "total_claims": total_claims,
            "claims_analyzed": claims_analyzed,
            "active_assessments": active_assessments,
            "pending_review": pending_review,
            "completed_assessments": completed_assessments,
            "overdue_assessments": overdue_assessments,
            "claim_value_under_assessment": round(claim_value_under_assessment, 2),
            "avg_risk_score": avg_risk_score,
            "risk_distribution": risk_distribution,
            "decision_distribution": decision_distribution,
            "ai_rol_activity_by_capability": ai_rol_by_capability,
            "handler_action_distribution": handler_action_distribution,
            "claims_over_time": claims_over_time,
            "business_rules_trigger_frequency": [{"rule_id": r, "count": c} for r, c in top_rules],
            "relationship_findings": {
                "claims_with_findings": claims_with_relationship_findings,
                "by_type": relationship_type_counts,
            },
            "narrative_similarity": {
                "claims_with_matches": claims_with_similarity_matches,
            },
        }
    except Exception as e:
        logger.error(f"Error building admin analytics overview: {str(e)}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


def _classify_assignment_status(row: Dict[str, Any]) -> str:
    """A single human-readable status for the Live Operations table --
    collapses the raw assignment status + timing into the four buckets an
    admin actually cares about at a glance."""
    status_raw = (row.get("status") or "pending").lower()
    if status_raw == "in_progress":
        return "AI Review" if row.get("risk_level") == "pending" else "Inspection"
    if status_raw in ("pending",):
        assigned_at = row.get("assigned_at")
        if assigned_at:
            try:
                age_hours = (datetime.now() - datetime.fromisoformat(assigned_at.split(".")[0])).total_seconds() / 3600
                if age_hours > 72:
                    return "Overdue"
            except Exception:
                pass
        return "Inspection"
    if status_raw == "completed":
        return "Completed"
    return status_raw.replace("_", " ").title()


@analysis_router.get("/admin/live-operations")
async def get_live_assessment_operations(
    limit: int = Query(20, description="Page size"),
    offset: int = Query(0, description="Pagination offset"),
    status_filter: Optional[str] = Query(None, alias="status", description="Inspection / AI Review / Overdue / Completed"),
    risk_level: Optional[str] = Query(None, description="low / medium / high"),
):
    """
    **ADMIN ONLY: Live Assessment Operations feed** -- every assessment
    currently in the system with vehicle, assessor, status and AI risk in
    one row, matching a real claims-ops board rather than a bare claims list.
    """
    try:
        with db_manager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT
                    ca.claim_id, ca.assignment_id, ca.assessor_id, ca.assigned_at, ca.status,
                    c.estimated_cost, c.risk_level, c.fraud_risk_score, c.created_at, c.policy_id,
                    a.name as assessor_name,
                    m.vehicle_make, m.vehicle_model, m.vehicle_year
                FROM claim_assignments ca
                JOIN claims c ON c.claim_id = ca.claim_id
                LEFT JOIN assessors a ON a.assessor_id = ca.assessor_id
                LEFT JOIN motor_policy_details m ON m.policy_id = c.policy_id
                ORDER BY ca.assigned_at DESC
            ''')
            rows = [dict(r) for r in cursor.fetchall()]

        for r in rows:
            r["computed_status"] = _classify_assignment_status(r)

        if status_filter:
            rows = [r for r in rows if r["computed_status"].lower() == status_filter.lower()]
        if risk_level:
            rows = [r for r in rows if (r.get("risk_level") or "").lower() == risk_level.lower()]

        total = len(rows)
        page = rows[offset:offset + limit]

        operations = []
        for r in page:
            vehicle = " ".join(filter(None, [
                str(r["vehicle_year"]) if r.get("vehicle_year") else None,
                r.get("vehicle_make"), r.get("vehicle_model"),
            ])) or None
            operations.append({
                "claim_id": r["claim_id"],
                "assignment_id": r["assignment_id"],
                "vehicle": vehicle,
                "assessor_name": r.get("assessor_name") or r.get("assessor_id"),
                "status": r["computed_status"],
                "ai_risk": (r.get("risk_level") or "unknown"),
                "fraud_risk_score": r.get("fraud_risk_score"),
                "value": r.get("estimated_cost"),
                "assigned_at": r.get("assigned_at"),
            })

        return {"success": True, "total": total, "limit": limit, "offset": offset, "operations": operations}
    except Exception as e:
        logger.error(f"Error building live assessment operations: {str(e)}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


# Component-name buckets for the AI vs Assessor disagreement breakdown --
# groups the many specific part names (from both the YOLO taxonomy and the
# vision-LLM's free-form part identification) into the handful of families
# an admin actually wants a rate for, rather than one row per exact part.
_COMPONENT_BUCKETS = [
    ("Bumper", ["bumper"]),
    ("Headlamp", ["headlamp", "headlight"]),
    ("Taillamp", ["taillamp", "taillight"]),
    ("Fender", ["fender"]),
    ("Door", ["door"]),
    ("Glass/Windscreen", ["windscreen", "window", "windshield"]),
    ("Bonnet/Hood", ["bonnet", "hood"]),
    ("Roof", ["roof"]),
    ("Mirror", ["mirror"]),
    ("Grille", ["grille"]),
]


def _bucket_component(component: str) -> str:
    c = (component or "").lower()
    for label, keywords in _COMPONENT_BUCKETS:
        if any(kw in c for kw in keywords):
            return label
    return "Other"


@analysis_router.get("/admin/ai/overview")
async def get_ai_intelligence_overview():
    """
    **ADMIN ONLY: AI Intelligence Centre -- top-level AI performance.**

    Every number here is computed from what the system actually stored
    (detections, whole-photo scan zones, and assessor decisions) rather
    than a static claim about accuracy -- so it moves as real claims are
    assessed, and reads as "unknown" rather than a fabricated figure
    wherever there isn't yet enough data to compute it honestly.
    """
    try:
        with db_manager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT analysis_result FROM claims WHERE analysis_result IS NOT NULL AND analysis_result != '{}'")
            claim_rows = cursor.fetchall()

            cursor.execute("SELECT COUNT(*) as cnt FROM photo_damage_decisions")
            total_decisions = cursor.fetchone()["cnt"] or 0
            cursor.execute("SELECT COUNT(*) as cnt FROM photo_damage_decisions WHERE ai_recommendation = assessor_decision")
            confirmed_decisions = cursor.fetchone()["cnt"] or 0

        images_analysed = 0
        total_detections = 0
        confidence_sum = 0.0
        confidence_count = 0
        low_confidence_count = 0
        pending_review_count = 0

        for row in claim_rows:
            try:
                ar = json.loads(row["analysis_result"])
            except (json.JSONDecodeError, TypeError):
                continue
            photo_analysis = ar.get("photo_analysis") or {}
            for photo_result in photo_analysis.get("results", []):
                images_analysed += 1
                detections = photo_result.get("detections") or []
                damage_zones = photo_result.get("damage_zones") or []
                for d in detections:
                    total_detections += 1
                    conf = d.get("confidence")
                    if conf is not None:
                        confidence_sum += conf
                        confidence_count += 1
                    if d.get("low_confidence") or (conf is not None and conf < 0.4):
                        low_confidence_count += 1
                        pending_review_count += 1
                for z in damage_zones:
                    total_detections += 1
                    conf = z.get("confidence")
                    if conf is not None:
                        confidence_sum += conf
                        confidence_count += 1
                    if conf is not None and conf < 0.4:
                        low_confidence_count += 1
                        pending_review_count += 1

        avg_confidence = round((confidence_sum / confidence_count) * 100, 1) if confidence_count else None
        confirmation_rate = round((confirmed_decisions / total_decisions) * 100, 1) if total_decisions else None
        override_rate = round(100 - confirmation_rate, 1) if confirmation_rate is not None else None
        # Detections/zones outstanding an assessor decision -- decided ones
        # are already excluded from pending, so this is a genuine backlog
        # count, not a static placeholder.
        pending_assessor_decisions = max(0, total_detections - total_decisions)

        return {
            "success": True,
            "images_analysed": images_analysed,
            "damage_detections": total_detections,
            "assessor_confirmation_rate": confirmation_rate,
            "human_override_rate": override_rate,
            "ai_confidence_avg": avg_confidence,
            "low_confidence_cases": low_confidence_count,
            "pending_assessor_decisions": pending_assessor_decisions,
            "total_assessor_decisions_logged": total_decisions,
        }
    except Exception as e:
        logger.error(f"Error building AI intelligence overview: {str(e)}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


@analysis_router.get("/admin/ai/comparison")
async def get_ai_vs_assessor_comparison():
    """
    **ADMIN ONLY: AI vs Assessor comparison** -- overall agreement rate plus
    a per-component-family disagreement breakdown, built entirely from
    logged assessor decisions (photo_damage_decisions), never inferred.
    """
    try:
        with db_manager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT claim_id, filename, component, ai_recommendation, assessor_decision, assessor_id, decided_at
                FROM photo_damage_decisions ORDER BY decided_at DESC
            ''')
            rows = [dict(r) for r in cursor.fetchall()]

        bucket_totals: Dict[str, int] = {}
        bucket_disagreements: Dict[str, int] = {}
        agreements = 0
        recent = []

        for r in rows:
            bucket = _bucket_component(r.get("component"))
            agree = r.get("ai_recommendation") == r.get("assessor_decision")
            bucket_totals[bucket] = bucket_totals.get(bucket, 0) + 1
            if agree:
                agreements += 1
            else:
                bucket_disagreements[bucket] = bucket_disagreements.get(bucket, 0) + 1
            if len(recent) < 25:
                recent.append({
                    "claim_id": r["claim_id"],
                    "component": r.get("component"),
                    "ai_recommendation": r.get("ai_recommendation"),
                    "assessor_decision": r.get("assessor_decision"),
                    "agreed": agree,
                    "decided_at": r.get("decided_at"),
                })

        total = len(rows)
        agreement_rate = round((agreements / total) * 100, 1) if total else None
        disagreement_by_component = [
            {
                "component": bucket,
                "total": bucket_totals[bucket],
                "disagreements": bucket_disagreements.get(bucket, 0),
                "disagreement_rate": round((bucket_disagreements.get(bucket, 0) / bucket_totals[bucket]) * 100, 1),
            }
            for bucket in sorted(bucket_totals.keys(), key=lambda b: bucket_totals[b], reverse=True)
        ]

        return {
            "success": True,
            "total_decisions": total,
            "agreement_rate": agreement_rate,
            "disagreement_by_component": disagreement_by_component,
            "recent_decisions": recent,
        }
    except Exception as e:
        logger.error(f"Error building AI vs assessor comparison: {str(e)}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


@analysis_router.get("/admin/ai/risk-queue")
async def get_ai_risk_fraud_queue(limit: int = Query(30, description="Max claims to return")):
    """
    **ADMIN ONLY: AI Risk & Fraud Centre** -- a triaged queue built from the
    business-rules engine's findings already stored per claim. Presented as
    risk indicators requiring review, never as a fraud verdict -- the
    engine itself never declares a claim fraudulent, only flags patterns
    worth a human look.
    """
    try:
        with db_manager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT claim_id, analysis_result, created_at, estimated_cost
                FROM claims WHERE analysis_result IS NOT NULL AND analysis_result != '{}'
                ORDER BY created_at DESC
            ''')
            rows = cursor.fetchall()

        queue = []
        for row in rows:
            try:
                ar = json.loads(row["analysis_result"])
            except (json.JSONDecodeError, TypeError):
                continue
            business_rules_data = ar.get("business_rules") or {}
            findings = business_rules_data.get("findings") or []
            if not findings:
                continue

            severities = [f.get("severity") for f in findings]
            if "high" in severities:
                tier = "critical"
            elif "medium" in severities:
                tier = "review"
            else:
                tier = "normal"

            queue.append({
                "claim_id": row["claim_id"],
                "tier": tier,
                "risk_score": business_rules_data.get("risk_score"),
                "created_at": row["created_at"],
                "estimated_cost": row["estimated_cost"],
                "indicators": [
                    {"type": f.get("type"), "severity": f.get("severity"), "description": f.get("description"), "confidence": f.get("confidence")}
                    for f in findings
                ],
            })

        tier_order = {"critical": 0, "review": 1, "normal": 2}
        queue.sort(key=lambda q: (tier_order.get(q["tier"], 3), -(q["risk_score"] or 0)))

        return {
            "success": True,
            "total_flagged": len(queue),
            "critical_count": sum(1 for q in queue if q["tier"] == "critical"),
            "review_count": sum(1 for q in queue if q["tier"] == "review"),
            "queue": queue[:limit],
        }
    except Exception as e:
        logger.error(f"Error building AI risk & fraud queue: {str(e)}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


@analysis_router.get("/admin/ai/decision-trace/{claim_id}")
async def get_ai_decision_trace(claim_id: str):
    """
    **ADMIN ONLY: AI Decision Trace** -- the real per-detection pipeline
    (what actually runs in service.py/damage_detector.py/part_identifier.py
    for every photo) rendered as an explainable step list, so "why did the
    AI recommend this" has a concrete answer instead of a black box.
    """
    try:
        claim = db_manager.get_claim(claim_id)
        if not claim:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Claim {claim_id} not found")

        analysis_result = claim.get("analysis_result")
        if isinstance(analysis_result, str):
            analysis_result = json.loads(analysis_result)

        with db_manager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT filename, detection_index, assessor_decision FROM photo_damage_decisions WHERE claim_id = ?",
                (claim_id,),
            )
            decisions = {(r["filename"], r["detection_index"]): r["assessor_decision"] for r in cursor.fetchall()}

        photo_analysis = analysis_result.get("photo_analysis") or {}
        traces = []
        for photo_result in photo_analysis.get("results", []):
            filename = photo_result.get("filename")
            for i, d in enumerate(photo_result.get("detections") or []):
                conf = d.get("confidence")
                low_conf = bool(d.get("low_confidence")) or (conf is not None and conf < 0.4)
                decided = decisions.get((filename, i))
                traces.append({
                    "filename": filename,
                    "source": "detector",
                    "component": d.get("component"),
                    "steps": [
                        {"label": "Image received", "done": True},
                        {"label": "Photo quality check", "done": True},
                        {"label": "Vehicle component detected", "done": True, "detail": d.get("component")},
                        {"label": "Damage detected", "done": True, "detail": d.get("class", "").replace("-", " ")},
                        {"label": "Severity estimated", "done": True, "detail": d.get("severity", "moderate")},
                        {"label": "Repair/Replace recommendation", "done": True, "detail": d.get("recommended_action")},
                        {"label": "Confidence", "done": True, "detail": f"{round((conf or 0) * 100)}%"},
                        {"label": "Human verification required", "done": True, "detail": "YES" if low_conf else "Recommended"},
                    ],
                    "confidence": conf,
                    "low_confidence": low_conf,
                    "recommended_action": d.get("recommended_action"),
                    "assessor_decision": decided,
                })
            for i, z in enumerate(photo_result.get("damage_zones") or []):
                conf = z.get("confidence")
                low_conf = conf is not None and conf < 0.4
                decided = decisions.get((filename, 100 + i))
                traces.append({
                    "filename": filename,
                    "source": "whole_photo_scan",
                    "component": z.get("part"),
                    "steps": [
                        {"label": "Image received", "done": True},
                        {"label": "Photo quality check", "done": True},
                        {"label": "Vehicle component identified", "done": True, "detail": z.get("part")},
                        {"label": "Damage classified", "done": True, "detail": z.get("damage_type")},
                        {"label": "Severity estimated", "done": True, "detail": z.get("severity")},
                        {"label": "Repair/Replace recommendation", "done": True, "detail": z.get("recommended_action")},
                        {"label": "Confidence", "done": True, "detail": f"{round((conf or 0) * 100)}%"},
                        {"label": "Human verification required", "done": True, "detail": "YES" if low_conf else "Recommended"},
                    ],
                    "confidence": conf,
                    "low_confidence": low_conf,
                    "recommended_action": z.get("recommended_action"),
                    "assessor_decision": decided,
                })

        return {"success": True, "claim_id": claim_id, "traces": traces}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error building AI decision trace for {claim_id}: {str(e)}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


@analysis_router.get("/admin/claim/{claim_id}/details")
async def get_admin_claim_details(claim_id: str):
    """
    **ADMIN ONLY: unrestricted claim details** -- same shape as the
    assessor's /claim-details endpoint (raw analysis_result, photos,
    documents, member info) but with no assessor-assignment check, since an
    admin needs to open any claim, not just ones assigned to them. This is
    what the AI Intelligence Centre's Assessment Review tab reads photo
    detections/damage_zones from -- the curated /full-report endpoint
    deliberately strips that raw detail out into a fraud-summary shape.
    """
    try:
        claim_data = db_manager.get_claim(claim_id)
        if not claim_data:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Claim {claim_id} not found")

        member_info = db_manager.get_member_info(claim_data.get("member_id", "")) if claim_data.get("member_id") else None

        with db_manager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT id, filename, party, file_size, content_type, uploaded_at
                FROM claim_photo_files WHERE claim_id = ? ORDER BY uploaded_at ASC
            ''', (claim_id,))
            photos = [dict(row) for row in cursor.fetchall()]

            cursor.execute('''
                SELECT ca.*, a.name as assessor_name FROM claim_assignments ca
                LEFT JOIN assessors a ON a.assessor_id = ca.assessor_id
                WHERE ca.claim_id = ? ORDER BY ca.assigned_at DESC
            ''', (claim_id,))
            assignments = [dict(row) for row in cursor.fetchall()]

            policy_info = None
            if claim_data.get("policy_id"):
                cursor.execute('''
                    SELECT p.sum_insured, p.excess, p.cover_type, m.vehicle_make, m.vehicle_model, m.vehicle_year
                    FROM policies p LEFT JOIN motor_policy_details m ON m.policy_id = p.policy_id
                    WHERE p.policy_id = ?
                ''', (claim_data["policy_id"],))
                row = cursor.fetchone()
                if row:
                    policy_info = dict(row)

        documents = db_manager.get_documents_by_claim(claim_id)
        damage_decisions = db_manager.get_photo_damage_decisions(claim_id)

        return {
            "success": True,
            "claim_id": claim_id,
            "claim_details": claim_data,
            "member_info": member_info,
            "policy_info": policy_info,
            "photos": photos,
            "documents": documents,
            "assignments": assignments,
            "damage_decisions": damage_decisions,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error building admin claim details for {claim_id}: {str(e)}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


@analysis_router.get("/admin/claim/{claim_id}/recipients")
async def get_claim_recipients(claim_id: str):
    """
    **ADMIN ONLY: candidate recipients for the AI communications flow** --
    every party actually attached to this claim (member, assigned assessor,
    filing analyst, repair shop) with a name + email where one exists, so
    the admin picks a person, not types an address from memory.
    """
    try:
        claim = db_manager.get_claim(claim_id)
        if not claim:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Claim {claim_id} not found")

        recipients = []
        with db_manager.get_connection() as conn:
            cursor = conn.cursor()

            if claim.get("member_id"):
                cursor.execute("SELECT name, email FROM members WHERE member_id = ?", (claim["member_id"],))
                row = cursor.fetchone()
                if row and row["email"]:
                    recipients.append({"type": "member", "id": claim["member_id"], "name": row["name"], "email": row["email"]})

            cursor.execute('''
                SELECT a.assessor_id, a.name, a.email FROM claim_assignments ca
                JOIN assessors a ON a.assessor_id = ca.assessor_id
                WHERE ca.claim_id = ? ORDER BY ca.assigned_at DESC LIMIT 1
            ''', (claim_id,))
            row = cursor.fetchone()
            if row and row["email"]:
                recipients.append({"type": "assessor", "id": row["assessor_id"], "name": row["name"], "email": row["email"]})

            if claim.get("filed_by_analyst_id"):
                cursor.execute("SELECT name, email FROM analysts WHERE analyst_id = ?", (claim["filed_by_analyst_id"],))
                row = cursor.fetchone()
                if row and row["email"]:
                    recipients.append({"type": "analyst", "id": claim["filed_by_analyst_id"], "name": row["name"], "email": row["email"]})

            if claim.get("repair_shop_id"):
                cursor.execute("SELECT shop_id, name FROM repair_shops WHERE shop_id = ?", (claim["repair_shop_id"],))
                row = cursor.fetchone()
                if row:
                    # repair_shops carries no email column in this schema --
                    # still surfaced so the admin can type one in manually
                    # rather than not seeing the repairer as an option at all.
                    recipients.append({"type": "repair_shop", "id": row["shop_id"], "name": row["name"], "email": None})

        return {"success": True, "claim_id": claim_id, "recipients": recipients}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error building recipients for {claim_id}: {str(e)}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


@analysis_router.post("/admin/claim/{claim_id}/ai-draft-message")
async def ai_draft_claim_message(
    claim_id: str,
    recipient_type: str = Form(..., description="member / assessor / analyst / repair_shop"),
    recipient_name: str = Form(""),
    instruction: str = Form("", description="What the admin wants said, in their own words"),
):
    """
    **ADMIN ONLY: AI-drafted email for a claim** -- pulls real claim context
    (status, decision, risk indicators) into a short professional draft the
    admin reviews and edits before sending, mirroring the "AI drafts, human
    approves and sends" pattern -- nothing here ever sends on its own.
    """
    try:
        claim = db_manager.get_claim(claim_id)
        if not claim:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Claim {claim_id} not found")

        analysis_result = claim.get("analysis_result")
        if isinstance(analysis_result, str):
            try:
                analysis_result = json.loads(analysis_result)
            except (json.JSONDecodeError, TypeError):
                analysis_result = {}
        analysis_result = analysis_result or {}

        audience_profile = {
            "member": {
                "who": "the POLICYHOLDER who filed this claim -- a customer, not a colleague",
                "tone": "plain, reassuring, non-technical language, no internal risk/fraud terminology, no operational jargon",
                "default_ask": "a general status update on their own claim -- reassure them it's being handled",
                "never": "do not ask them to inspect anything, do not reference assignments or workload -- they are not staff",
            },
            "assessor": {
                "who": "the ASSESSOR assigned to inspect this claim -- a staff member doing a job, not the customer",
                "tone": "direct, operational, colleague-to-colleague -- can reference inspection scheduling, assignment details, documentation needed",
                "default_ask": "a reminder or status check on their assigned inspection for this claim",
                "never": "do not write as if the recipient is the person whose car was damaged, do not reassure them about 'their claim being handled' -- it is not their claim, it is their assignment",
            },
            "analyst": {
                "who": "the CLAIMS ANALYST who filed this claim on the member's behalf -- a staff member, not the customer",
                "tone": "operational, colleague-to-colleague, can reference claim status and next processing steps",
                "default_ask": "a status update on a claim they filed, for their records",
                "never": "do not address them as if they are the policyholder",
            },
            "repair_shop": {
                "who": "the REPAIR SHOP handling the vehicle -- an external vendor, not the customer or staff",
                "tone": "focused on repair scope and parts, no internal risk data",
                "default_ask": "a request for a repair quote or status update on repair work",
                "never": "do not discuss claim risk/fraud assessment with them",
            },
        }.get(recipient_type, {"who": "the recipient", "tone": "professional", "default_ask": "a general update", "never": ""})

        context = {
            "claim_id": claim_id,
            "status": claim.get("risk_level"),
            "estimated_cost": claim.get("estimated_cost"),
            "location": claim.get("location"),
            "decision": analysis_result.get("final_assessment", {}).get("decision") if isinstance(analysis_result.get("final_assessment"), dict) else None,
        }

        prompt = f"""Draft a short, professional email about motor insurance claim {claim_id}.

You are writing this email TO {recipient_name or "the recipient"} DIRECTLY -- address them as "you" throughout. This person is {audience_profile['who']}.
Tone for this recipient: {audience_profile['tone']}.
{audience_profile['never']}.

Claim context (ONLY facts, from the system -- treat "null" as "not yet known", never invent a value for it): {json.dumps(context)}
What the sender (a claims admin) wants communicated: {instruction or audience_profile['default_ask']}

Critical: never state or imply a claim outcome (approved, declined, payment authorized, payment refused, etc.) unless "decision" above is a real non-null value that says so. If "decision" is null, the claim is still under review -- say exactly that, don't guess. Never mention internal fraud/risk scores or business-rule findings unless the recipient is explicitly the assessor and the instruction asks for it. Stay strictly in the voice appropriate for THIS recipient -- do not slip into language meant for a different audience (e.g. never console an assessor as if they were the person whose car was damaged).
Respond with ONLY this JSON: {{"subject": "...", "body": "..."}}
"body" should be plain text (no HTML), 3-6 short sentences, signed "Claims Team"."""

        draft = generate_json(prompt, timeout=60)
        subject = draft.get("subject") or f"Update on claim {claim_id}"
        body = draft.get("body") or "We are reviewing your claim and will be in touch shortly."

        return {"success": True, "subject": subject, "body": body}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error drafting AI message for {claim_id}: {str(e)}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


@analysis_router.post("/admin/claim/{claim_id}/send-message")
async def send_claim_message(
    claim_id: str,
    to_email: str = Form(...),
    subject: str = Form(...),
    body: str = Form(...),
    recipient_type: str = Form(""),
):
    """**ADMIN ONLY: send an (admin-approved, possibly AI-drafted) email for this claim.**"""
    try:
        if not email_service.is_configured():
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Email sending is not configured on this server.")
        html_body = "".join(f"<p>{line}</p>" for line in body.split("\n") if line.strip())
        sent = email_service.send_email(to_email, subject, html_body)
        if not sent:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Email failed to send.")
        return {"success": True, "claim_id": claim_id, "to": to_email, "recipient_type": recipient_type}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error sending message for {claim_id}: {str(e)}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


# rb_field pulls the raw 0-100 component score from risk_breakdown;
# weight_key pulls the matching weight from risk_breakdown.weights_applied
# -- these use two DIFFERENT naming schemes in the underlying data
# (weights_applied uses short keys like "photo"/"business_rules", while the
# score fields use "_risk"-suffixed names), so both must be mapped
# explicitly rather than assumed to match "key".
_RISK_FACTOR_META = [
    {"key": "photo_analysis", "rb_field": "photo_risk", "weight_key": "photo", "label": "Photo Evidence", "icon": "photo_camera"},
    {"key": "narrative_analysis", "rb_field": "narrative_risk", "weight_key": "narrative", "label": "Claim Narrative", "icon": "description"},
    {"key": "business_rules", "rb_field": "business_rules_risk", "weight_key": "business_rules", "label": "Business Rules", "icon": "rule"},
    {"key": "amount_based", "rb_field": "amount_risk", "weight_key": "amount", "label": "Claim Amount", "icon": "payments"},
    {"key": "location_based", "rb_field": "location_risk", "weight_key": "location", "label": "Location", "icon": "location_on"},
    {"key": "historical_patterns", "rb_field": "historical_risk", "weight_key": "historical", "label": "Historical Comparison", "icon": "history"},
]


def _impact_tier(pct: float) -> str:
    if pct >= 0.6:
        return "high"
    if pct >= 0.3:
        return "medium"
    return "low"


@analysis_router.post("/admin/claim/{claim_id}/explain-risk-score")
async def explain_risk_score(
    claim_id: str,
    risk_breakdown: str = Form(..., description="JSON string of the claim's risk_breakdown object, as already shown on screen"),
    risk_level: str = Form(""),
    decision: str = Form(""),
    photo_anomaly_count: int = Form(0),
    narrative_issue_count: int = Form(0),
    cross_party_issue_count: int = Form(0),
    business_rule_findings: str = Form("[]", description="JSON array of {type, severity, description} from the claim's business_rules.findings"),
):
    """
    **ADMIN ONLY: structured AI Investigation Summary for a claim's risk score.**

    Every number here (points, max points, impact tier) is computed
    deterministically in Python from the same risk_breakdown weights/scores
    already shown on screen -- the LLM is used ONLY to phrase the reasoning
    sentence per factor, grounded in the real finding counts passed in, so
    it can't invent a count or a determination that wasn't actually made.
    """
    try:
        try:
            rb = json.loads(risk_breakdown)
        except (json.JSONDecodeError, TypeError):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="risk_breakdown must be valid JSON")
        try:
            findings = json.loads(business_rule_findings)
            if not isinstance(findings, list):
                findings = []
        except (json.JSONDecodeError, TypeError):
            findings = []

        weights = rb.get("weights_applied") or {}
        overall = rb.get("overall_score") or 0

        factors = []
        for meta in _RISK_FACTOR_META:
            weight = weights.get(meta["weight_key"])
            raw_score = rb.get(meta["rb_field"])
            if weight is None or raw_score is None:
                continue
            max_points = round(weight * 100, 1)
            points = round(raw_score * weight, 1)
            pct = (raw_score / 100) if raw_score else 0
            factors.append({
                "key": meta["key"], "label": meta["label"], "icon": meta["icon"],
                "points": points, "max_points": max_points, "impact": _impact_tier(pct),
            })
        factors.sort(key=lambda f: f["points"], reverse=True)

        # Ground truth counts for the factors that have them -- these are
        # real, already-computed numbers from this claim's own analysis
        # (fraud_indicators / business_rules.findings), not invented.
        evidence = {
            "photo_analysis": f"{photo_anomaly_count} flagged photo anomal{'y' if photo_anomaly_count == 1 else 'ies'}" if photo_anomaly_count else "no specific photo anomalies flagged individually",
            "narrative_analysis": f"{narrative_issue_count} narrative inconsistenc{'y' if narrative_issue_count == 1 else 'ies'}" if narrative_issue_count else "no specific narrative inconsistencies flagged individually",
            "business_rules": f"{len(findings)} triggered rule(s): " + "; ".join(f.get("description", f.get("type", "")) for f in findings[:4]) if findings else "no business rules triggered",
        }

        # Only ask the LLM to phrase reasoning for factors that actually
        # matter (medium/high impact) -- low-impact factors get a fixed,
        # non-hallucinatable template sentence instead of a model call.
        needs_reasoning = [f for f in factors if f["impact"] != "low"]
        reasoning_map: Dict[str, str] = {}
        headline = f"This claim is rated {risk_level or 'unknown'} risk ({overall}/100) and requires additional review before settlement."
        if needs_reasoning:
            factor_lines = "\n".join(
                f"- {f['label']} ({f['key']}): {f['points']}/{f['max_points']} points, impact={f['impact']}. "
                f"Ground truth: {evidence.get(f['key'], 'no additional detail available')}."
                for f in needs_reasoning
            )
            prompt = f"""A motor insurance claim scored {overall}/100 ("{risk_level or 'unknown'}" risk, decision so far: {decision or 'not yet decided'}).

These factors need a one-sentence, plain-English reasoning for a non-technical claims admin, using ONLY the ground-truth detail given for each -- never invent a number, a detail, or an outcome not stated below:
{factor_lines}

Also write one overall headline sentence summarizing why this claim needs review (which 1-2 factors matter most, in plain terms).

Critical: never say "fraud" or "fraudulent" -- say "requires review" / "warrants investigation" instead. This is a risk indicator, not a fraud determination.

Respond with ONLY this JSON: {{"headline": "...", "reasoning": {{"{needs_reasoning[0]['key']}": "...", ...one entry per factor key above...}}}}"""
            try:
                result = generate_json(prompt, timeout=50)
                headline = result.get("headline") or headline
                reasoning_map = result.get("reasoning") or {}
            except Exception as e:
                logger.warning(f"AI investigation summary phrasing failed for {claim_id}, using fallback text: {e}")

        for f in factors:
            if f["impact"] == "low":
                f["reasoning"] = "Minimal contribution to the overall score."
            else:
                f["reasoning"] = reasoning_map.get(f["key"]) or f"Contributed {f['points']} of {f['max_points']} possible points -- {evidence.get(f['key'], 'reviewed as part of the overall assessment')}."

        what_to_review = []
        if any(f["key"] == "photo_analysis" and f["impact"] != "low" for f in factors):
            what_to_review.append("Vehicle photographs and AI damage annotations")
        if any(f["key"] == "business_rules" and f["impact"] != "low" for f in factors):
            what_to_review.append("Triggered business rules")
        if any(f["key"] == "narrative_analysis" and f["impact"] != "low" for f in factors):
            what_to_review.append("Accident narrative")
        if cross_party_issue_count:
            what_to_review.append("Cross-party verification issues")
        what_to_review.append("Supporting documents")
        if not what_to_review:
            what_to_review = ["Supporting documents"]

        return {
            "success": True,
            "claim_id": claim_id,
            "headline": headline,
            "risk_level": risk_level,
            "score": overall,
            "factors": factors,
            "what_to_review": what_to_review,
            "important_note": "This score is a risk indicator, not a fraud determination. It does not by itself confirm fraud or mean the claim should be rejected -- final decisions remain with the authorized claims team.",
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error explaining risk score for {claim_id}: {str(e)}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


@analysis_router.get("/admin/business-rules-config")
async def get_business_rules_config():
    """
    **ADMIN ONLY: current values (default or admin-overridden) for every
    configurable business-rule threshold, plus the schema (label/group/help/
    unit) the Settings page renders from.** Keeps the frontend from having
    to hardcode field labels/descriptions -- business_rules.py's
    CONFIG_SCHEMA is the single source of truth for both.
    """
    try:
        overrides = db_manager.get_system_config_overrides()
        fields = []
        for field in business_rules.CONFIG_SCHEMA:
            key = field["key"]
            fields.append({
                **field,
                "value": overrides.get(key, field["default"]),
                "is_overridden": key in overrides,
            })
        return {"success": True, "fields": fields}
    except Exception as e:
        logger.error(f"Error retrieving business rules config: {str(e)}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


@analysis_router.post("/admin/business-rules-config")
async def update_business_rules_config(
    updates: str = Form(..., description="JSON object of {config_key: number} to set, or {config_key: null} to reset to default"),
    admin_id: str = Form(..., description="Admin making the change, for the audit trail"),
):
    """
    **ADMIN ONLY: update one or more business-rule thresholds.** Only keys
    already present in business_rules.CONFIG_SCHEMA are accepted -- this is
    a retuning knob for existing rules, not a way to inject arbitrary config
    the rules engine was never written to read.
    """
    try:
        try:
            parsed = json.loads(updates)
            if not isinstance(parsed, dict):
                raise ValueError("updates must be a JSON object")
        except (json.JSONDecodeError, ValueError) as e:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid updates payload: {e}")

        valid_keys = {f["key"] for f in business_rules.CONFIG_SCHEMA}
        unknown_keys = set(parsed.keys()) - valid_keys
        if unknown_keys:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Unknown config key(s): {', '.join(sorted(unknown_keys))}")

        db_manager.set_system_config_overrides(parsed, updated_by=admin_id)
        logger.info(f"Business rules config updated by {admin_id}: {parsed}")
        return {"success": True, "updated": list(parsed.keys())}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating business rules config: {str(e)}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


@analysis_router.get("/admin/assessor-capacity")
async def get_assessor_capacity():
    """
    **Assessor roster with current/max workload, for the Settings page.**
    Auto-assignment silently fails once an assessor's current_workload hits
    max_workload -- this lets an analyst see and raise that cap before it
    blocks new claims.
    """
    try:
        assessors = db_manager.list_assessor_capacity()
        for a in assessors:
            a["available_capacity"] = a["max_workload"] - a["current_workload"]
            a["is_at_capacity"] = a["current_workload"] >= a["max_workload"]
        return {"success": True, "assessors": assessors}
    except Exception as e:
        logger.error(f"Error retrieving assessor capacity: {str(e)}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


@analysis_router.post("/admin/assessor-capacity")
async def update_assessor_capacity(
    assessor_id: str = Form(..., description="Assessor whose max_workload to change"),
    max_workload: int = Form(..., ge=1, description="New maximum concurrent claim capacity"),
    admin_id: str = Form(..., description="Analyst/admin making the change, for the audit trail"),
):
    """**Raise or lower one assessor's max concurrent-claim capacity.**"""
    try:
        result = db_manager.update_assessor_capacity(assessor_id, max_workload)
        if not result.get("success"):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=result.get("reason", "Update failed"))
        logger.info(f"Assessor {assessor_id} max_workload set to {max_workload} by {admin_id}")
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating assessor capacity: {str(e)}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


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
        # claims.estimated_cost is the live figure -- it's what
        # member_update_claim_fields writes when a member fills in a
        # pending value after the fact, and what the repair-shop stage
        # later overwrites as the "final" figure. analysis_result's own
        # copy is just a snapshot from whenever analysis last ran, so it
        # goes stale the moment either of those happens without a re-run.
        # Prefer the column; fall back to the blob only for the rare case
        # where the column is genuinely unset.
        estimated_cost  = claim.get('estimated_cost') if claim.get('estimated_cost') not in (None, 0) else analysis_result.get('estimated_cost', 0)
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
            "business_rules":     analysis_result.get('business_rules') or {},
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
                "photo_risk":           risk_scoring.get('component_scores', {}).get('photo_analysis', 0),
                "narrative_risk":       risk_scoring.get('component_scores', {}).get('narrative_analysis', 0),
                "amount_risk":          risk_scoring.get('component_scores', {}).get('amount_based', 0),
                "estimated_cost":       estimated_cost,
                "location_risk":        risk_scoring.get('component_scores', {}).get('location_based', 0),
                "location":             location,
                "historical_risk":      risk_scoring.get('component_scores', {}).get('historical_patterns', 0),
                "business_rules_risk":  risk_scoring.get('component_scores', {}).get('business_rules', 0),
                "physics_risk":         risk_scoring.get('component_scores', {}).get('physics_reconstruction', 0),
                "cross_party_risk":     cross_party.get('cross_party_risk_score', 0),
                "weights_applied":      risk_scoring.get('weights', {}),
                "explanation":          risk_scoring.get('explanation', ''),
                "overall_score":        risk_score
            },
            "physics_reconstruction": {
                "status":              physics_summary.get('status', 'not_run'),
                "claim_category":      physics_summary.get('claim_category'),
                "pathway":             physics_summary.get('pathway'),
                "vehicle_1_key":       physics_summary.get('vehicle_1_key'),
                "vehicle_2_key":       physics_summary.get('vehicle_2_key'),
                "v2_body_type":        physics_summary.get('v2_body_type'),
                "physics_fraud_score": physics_summary.get('physics_fraud_score'),
                "physics_verdict":     physics_verdict,
                "verdict_reason":      physics_summary.get('verdict_reason'),
                "physics_explanation": physics_summary.get('physics_explanation', ''),
                "simulation_method":   physics_summary.get('simulation_method'),
                "confidence":          physics_summary.get('confidence'),
                "inconsistencies":     physics_summary.get('inconsistencies', []),
                "warnings":            physics_summary.get('warnings', []),
                "signature":           physics_summary.get('signature'),
                "timeline_metadata":   timeline_meta,
                "timeline":            timeline if include_timeline else None,
                "comparison":          physics_summary.get('comparison'),
                # Which value each key physics input came from (assessor
                # measurement / Gemini narrative extraction / keyword
                # inference / stationary override) -- lets the reconstruction
                # UI label numbers as observed vs inferred vs calculated
                # instead of presenting everything with the same confidence.
                "data_sources":        physics_summary.get('data_sources'),
                "delta_v_kmh":         physics_summary.get('delta_v_kmh'),
                "kinetic_energy_j":    physics_summary.get('kinetic_energy_j'),
                "crush_energy_j":      physics_summary.get('crush_energy_j'),
                "energy_consistent":   physics_summary.get('energy_consistent'),
                "impact_force_magnitude_n": physics_summary.get('impact_force_magnitude_n'),
                "impact_force_is_estimated": physics_summary.get('impact_force_is_estimated'),
                "v1_impact_vertex_xyz": physics_summary.get('v1_impact_vertex_xyz'),
                "terrain_adjusted":    physics_summary.get('terrain_adjusted'),
                "slope_adjustment_kmh": physics_summary.get('slope_adjustment_kmh'),
                "impact_zone_v1":      physics_summary.get('impact_zone_v1'),
                "impact_zone_v1_source": physics_summary.get('impact_zone_v1_source'),
                "impact_zone_v1_detected_part": physics_summary.get('impact_zone_v1_detected_part'),
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
            # The member's own cost estimate at filing time never lived
            # inside member_submission (that JSON only ever held narrative
            # data) -- it's a separate column, captured once and never
            # overwritten, so it stays comparable to the assessor/repair
            # shop figures that come later.
            "member_stated_estimate": claim.get('initial_estimated_cost'),
            # AI's own independent repair-cost estimate from detected damage
            # (see part_identifier.estimate_damage_cost / the
            # cost_reasonableness business rule) -- an extra cross-check
            # figure, not sourced from any party with a stake in payout.
            # Prefer the blob's own figure (same run as ai_cost_breakdown
            # below) over the raw column -- the column is written slightly
            # earlier in analyze_multiparty_claim than the blob's
            # store_claim() call, and if the member/assessor/repair-shop
            # background tasks overlap, a different (later) run's column
            # write can otherwise end up paired with an earlier run's
            # breakdown text, showing a number that doesn't match the
            # sentence explaining it. Falls back to the column only for
            # claims analyzed before ai_estimated_cost existed in the blob.
            "ai_estimated_cost": analysis_result.get('ai_estimated_cost') or claim.get('ai_estimated_cost'),
            "ai_cost_breakdown": analysis_result.get('ai_cost_breakdown'),
            # assessor_submission/repair_shop_submission never carried a cost
            # figure at all (service.py only ever put report/estimate TEXT
            # and photo counts in there) -- the frontend's "Assessor
            # Physical Estimate"/"Repair Shop Invoice Proposal" rows always
            # showed N/A regardless of whether the party had submitted.
            # Enrich with the real figures from their own DB columns
            # (assessor_estimated_cost is written synchronously and
            # independent of analysis timing; the "final" estimated_cost
            # column becomes the repair shop's figure once they submit,
            # same convention member_stated_estimate above relies on).
            # assessor_estimated_cost is written synchronously the moment the
            # assessor submits (see submit_assessor_report), well before the
            # background re-analysis that fills in analysis_result's own
            # assessor_submission (report text/photo count) completes -- that
            # background run alone can take a minute or more. Previously this
            # whole block went to None until that background run finished, so
            # a just-submitted assessor estimate was invisible on the analyst
            # page for that whole window even though the number was already
            # in the database. Fall back to a synthetic entry (cost only,
            # report/analysis pending) instead of hiding a real submission.
            "assessor_submission": (
                {**analysis_result['assessor_submission'], "estimated_cost": claim.get('assessor_estimated_cost')}
                if analysis_result.get('assessor_submission')
                else ({"report": None, "analysis": None, "photos_count": None, "estimated_cost": claim.get('assessor_estimated_cost'), "processing": True}
                      if claim.get('assessor_estimated_cost') is not None else None)
            ),
            "repair_shop_submission": (
                {**analysis_result['repair_shop_submission'], "total_cost": claim.get('estimated_cost')}
                if analysis_result.get('repair_shop_submission') else None
            ),
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

        # Best-effort -- never blocks recording the decision if email
        # fails/isn't configured.
        try:
            member = db_manager.get_member_info(claim.get("member_id")) if claim.get("member_id") else None
            if member and member.get("email"):
                email_service.send_decision_email(
                    to_email=member["email"],
                    member_name=member.get("name") or "there",
                    member_id=claim["member_id"],
                    claim_id=claim_id,
                    decision=decision,
                    reason=reason,
                    payout_amount=payout_amount if decision == "PAY" else None,
                )
        except Exception as e:
            logger.warning(f"Decision email failed for {claim_id}: {str(e)}")

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
            decision, color ="DECLINE",""
        elif risk_score >= 50:
            decision, color ="INVESTIGATE",""
        else:
            decision, color ="APPROVE",""
 
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
        logger.error(f"Error retrieving summary: {str(e)}")
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
            # See the matching comment in get_full_fraud_analysis -- the
            # column is the live figure, the blob is a stale snapshot once a
            # member fills a pending field or the repair shop submits.
            estimated_cost  = claim.get('estimated_cost') if claim.get('estimated_cost') not in (None, 0) else analysis_result.get('estimated_cost', 0)
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
                "member_stated_estimate": claim.get('initial_estimated_cost'),
            # AI's own independent repair-cost estimate from detected damage
            # (see part_identifier.estimate_damage_cost / the
            # cost_reasonableness business rule) -- an extra cross-check
            # figure, not sourced from any party with a stake in payout.
            # Prefer the blob's own figure (same run as ai_cost_breakdown
            # below) over the raw column -- the column is written slightly
            # earlier in analyze_multiparty_claim than the blob's
            # store_claim() call, and if the member/assessor/repair-shop
            # background tasks overlap, a different (later) run's column
            # write can otherwise end up paired with an earlier run's
            # breakdown text, showing a number that doesn't match the
            # sentence explaining it. Falls back to the column only for
            # claims analyzed before ai_estimated_cost existed in the blob.
            "ai_estimated_cost": analysis_result.get('ai_estimated_cost') or claim.get('ai_estimated_cost'),
            "ai_cost_breakdown": analysis_result.get('ai_cost_breakdown'),
                # assessor_submission/repair_shop_submission never carried a cost
            # figure at all (service.py only ever put report/estimate TEXT
            # and photo counts in there) -- the frontend's "Assessor
            # Physical Estimate"/"Repair Shop Invoice Proposal" rows always
            # showed N/A regardless of whether the party had submitted.
            # Enrich with the real figures from their own DB columns
            # (assessor_estimated_cost is written synchronously and
            # independent of analysis timing; the "final" estimated_cost
            # column becomes the repair shop's figure once they submit,
            # same convention member_stated_estimate above relies on).
            # assessor_estimated_cost is written synchronously the moment the
            # assessor submits (see submit_assessor_report), well before the
            # background re-analysis that fills in analysis_result's own
            # assessor_submission (report text/photo count) completes -- that
            # background run alone can take a minute or more. Previously this
            # whole block went to None until that background run finished, so
            # a just-submitted assessor estimate was invisible on the analyst
            # page for that whole window even though the number was already
            # in the database. Fall back to a synthetic entry (cost only,
            # report/analysis pending) instead of hiding a real submission.
            "assessor_submission": (
                {**analysis_result['assessor_submission'], "estimated_cost": claim.get('assessor_estimated_cost')}
                if analysis_result.get('assessor_submission')
                else ({"report": None, "analysis": None, "photos_count": None, "estimated_cost": claim.get('assessor_estimated_cost'), "processing": True}
                      if claim.get('assessor_estimated_cost') is not None else None)
            ),
                "repair_shop_submission": (
                {**analysis_result['repair_shop_submission'], "total_cost": claim.get('estimated_cost')}
                if analysis_result.get('repair_shop_submission') else None
            ),
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
        logger.error(f"Error getting claim status: {str(e)}")
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
 