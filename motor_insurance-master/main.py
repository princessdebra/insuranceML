from fastapi import FastAPI, Request, HTTPException, APIRouter, Form, UploadFile, Query, status
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
import logging
import os
from datetime import datetime
from typing import List, Dict, Optional

from routes import claims_router, analysis_router, system_router
from reconstruction_routes import reconstruction_router

from schemas import ErrorResponseSchema
from agents import PolicyCoverageChecker
from database import db_manager
import email_service

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

coverage_checker = PolicyCoverageChecker()

# Create FastAPI application
app = FastAPI(
    title="Old Mutual Motor Underwriting AI",
    description="""
    AI-powered motor insurance claims analysis and fraud detection system.
    """,
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc"
)

# Security middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=["*"]
)

app.include_router(claims_router)
app.include_router(analysis_router)
app.include_router(system_router)
app.include_router(reconstruction_router)

@app.post("/check-coverage")
async def check_coverage_before_claim(
    member_id: str = Form(...),
    claim_type: str = Form(...),  # motor/marine/domestic
    incident_date: str = Form(...),
    incident_location: str = Form(...),
    driver_name: Optional[str] = Form(None),
    brief_description: str = Form(...)
):
    """
    **STEP 1: Check if member is covered BEFORE creating claim**
    
    This must be done first!
    
    Returns:
    - COVERED: Proceed to create claim
    - NOT_COVERED: Stop, explain why
    
    **Example:**
    ```json
    {
      "member_id": "MEM0001",
      "claim_type": "motor",
      "incident_date": "2026-03-14",
      "incident_location": "Thika Road, Nairobi",
      "driver_name": "Lucy Kirui",
      "brief_description": "Rear-ended at traffic lights"
    }
    ```
    """
    try:
        logger.info(f"Coverage check for {member_id} - {claim_type}")
        
        # Build incident details
        incident_details = {
            "incident_date": incident_date,
            "location": incident_location,
            "description": brief_description
        }
        
        if claim_type == "motor":
            # PolicyCoverageChecker (agents.py ~line 269) does
            # incident_details.get('driver_name', '').strip() -- that
            # default only applies when the KEY is absent, not when its
            # value is None, so passing the raw Optional[str]=None through
            # crashes with "'NoneType' object has no attribute 'strip'"
            # whenever a caller omits driver_name (confirmed live testing
            # the claim-form-upload flow, whose driver name can genuinely
            # be blank if it wasn't legible on the form).
            incident_details["driver_name"] = driver_name or ""
            incident_details["vehicle_use"] = "private"  # Can be made dynamic
            incident_details["damage_type"] = "collision"  # Can be made dynamic
        
        # Check coverage
        checker = PolicyCoverageChecker()
        result = await checker.check_coverage(
            member_id=member_id,
            claim_type=claim_type,
            incident_details=incident_details
        )
        
        # Generate check ID
        import uuid
        check_id = f"COV-{uuid.uuid4().hex[:12].upper()}"
        
        # Store coverage check
        with db_manager.get_connection() as conn:
            db_manager.store_coverage_check_result(
                conn=conn,
                check_id=check_id,
                member_id=member_id,
                policy_id=result.get('policy_id', ''),
                claim_type=claim_type,
                coverage_decision=result.get('coverage_decision', 'UNKNOWN'),
                coverage_details=result
            )
        
        if result.get('covered'):
            return {
                "success": True,
                "coverage_decision": "COVERED",
                "check_id": check_id,  # Save this for claim creation!
                "policy_number": result.get('policy_number'),
                "coverage_percentage": result.get('coverage_percentage'),
                "applicable_excess": result.get('applicable_excess'),
                "reasons": result.get('reasons_for_decision', []),
                "next_step": "Proceed to create claim",
                "message":"Member is covered. You can now create a claim."
            }
        else:
            return {
                "success": True,
                "coverage_decision": "NOT_COVERED",
                "check_id": check_id,
                "reasons": result.get('reasons_for_decision', []),
                "exclusions": result.get('exclusions_triggered', []),
                "next_step": "Cannot create claim",
                "message":"Member is NOT covered for this incident."
            }
            
    except Exception as e:
        logger.error(f"Coverage check error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/policy/ask")
async def ask_policy_question(
    member_id: str = Form(..., description="Member/policy holder ID"),
    claim_type: str = Form("motor", description="motor/marine/domestic"),
    question: str = Form(..., description="Free-text question about the member's policy")
):
    """
    **Open-ended policy Q&A** — unlike /check-coverage, this isn't tied to
    filing a claim. A member can ask anything about their own policy
    ("what's my excess?", "am I covered for theft outside Nairobi?") and get
    an answer grounded in their real policy record and wording.

    **Example:**
    ```json
    {
      "member_id": "MEM0001",
      "claim_type": "motor",
      "question": "What's my excess if I make a claim?"
    }
    ```
    """
    try:
        result = await coverage_checker.answer_policy_question(
            member_id=member_id,
            claim_type=claim_type,
            question=question
        )
        return {"success": True, **result}

    except Exception as e:
        logger.error(f"Policy Q&A error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


# ASSESSOR ENDPOINTS

@app.get("/")
async def list_all_assessors(
    active_only: bool = Query(True, description="Only show active assessors"),
    specialization: Optional[str] = Query(None, description="Filter by specialization (motor/marine/domestic)"),
    location: Optional[str] = Query(None, description="Filter by location"),
    available_only: bool = Query(False, description="Only show assessors with available capacity")
):
    """
    **List all assessors in the system**
    
    Filters:
    - active_only: Show only active assessors (default: true)
    - specialization: Filter by type (motor/marine/domestic)
    - location: Filter by location (Nairobi, Mombasa, etc.)
    - available_only: Only show assessors who can take new claims
    
    **Response:**
    ```json
    {
      "success": true,
      "total_assessors": 5,
      "filters_applied": {...},
      "assessors": [...]
    }
    ```
    """
    try:
        with db_manager.get_connection() as conn:
            cursor = conn.cursor()
            
            # Build query
            query = "SELECT * FROM assessors WHERE 1=1"
            params = []
            
            if active_only:
                query += " AND active_status = 1"
            
            if specialization:
                query += " AND specialization = ?"
                params.append(specialization)
            
            if location:
                query += " AND location LIKE ?"
                params.append(f"%{location}%")
            
            if available_only:
                query += " AND current_workload < max_workload"
            
            query += " ORDER BY rating DESC, current_workload ASC"
            
            cursor.execute(query, params)
            assessors = [dict(row) for row in cursor.fetchall()]
            
            # Calculate availability for each assessor
            for assessor in assessors:
                assessor['available_capacity'] = assessor['max_workload'] - assessor['current_workload']
                assessor['utilization_percentage'] = round(
                    (assessor['current_workload'] / assessor['max_workload'] * 100), 2
                ) if assessor['max_workload'] > 0 else 0
                assessor['is_available'] = assessor['current_workload'] < assessor['max_workload']
        
        logger.info(f"Retrieved {len(assessors)} assessors (filters: active={active_only}, spec={specialization})")
        
        return {
            "success": True,
            "total_assessors": len(assessors),
            "filters_applied": {
                "active_only": active_only,
                "specialization": specialization,
                "location": location,
                "available_only": available_only
            },
            "assessors": assessors
        }
        
    except Exception as e:
        logger.error(f"Error listing assessors: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error retrieving assessors: {str(e)}"
        )


# ANALYST ENDPOINTS

@app.get("/api/analysts")
async def list_all_analysts(active_only: bool = Query(True)):
    """List all claims analysts (staff who file claims on members' behalf)."""
    try:
        with db_manager.get_connection() as conn:
            cursor = conn.cursor()
            query = "SELECT * FROM analysts WHERE 1=1"
            params = []
            if active_only:
                query += " AND active_status = 1"
            query += " ORDER BY name"
            cursor.execute(query, params)
            analysts = [dict(row) for row in cursor.fetchall()]
        return {"success": True, "total_analysts": len(analysts), "analysts": analysts}
    except Exception as e:
        logger.error(f"Error listing analysts: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/analyst-claims")
async def get_claims_filed_by_analyst(analyst_id: str):
    """
    **Analyst Dashboard: claims this analyst has filed on behalf of members**

    Mirrors the assessor's /my-claims (claims assigned TO them), but for the
    opposite relationship -- claims this analyst filed, as the point of
    contact instead of the field inspector.
    """
    try:
        claims = db_manager.get_analyst_claims(analyst_id)
        return {"success": True, "analyst_id": analyst_id, "total_claims": len(claims), "claims": claims}
    except Exception as e:
        logger.error(f"Error fetching claims for analyst {analyst_id}: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/assign")
async def manually_assign_assessor(
    claim_id: str = Form(..., description="Claim ID to assign"),
    assessor_id: str = Form(..., description="Assessor ID to assign"),
    notes: Optional[str] = Form(None, description="Assignment notes")
):
    """
    **Manually assign an assessor to a claim**
    
    Use this to:
    - Override auto-assignment
    - Reassign a claim to different assessor
    - Assign after claim creation
    
    **Example:**
    ```bash
    curl -X POST "/api/assessors/assign" \
      -F "claim_id=CLM-2026-000001" \
      -F "assessor_id=ASS002" \
      -F "notes=Reassigned due to specialization"
    ```
    
    **Response:**
    ```json
    {
      "success": true,
      "message": "Assessor assigned successfully",
      "assignment_id": "ASG-ABC123",
      "assessor_name": "Mary Wanjiku"
    }
    ```
    """
    try:
        logger.info(f"Manual assignment: {assessor_id} → {claim_id}")
        
        with db_manager.get_connection() as conn:
            cursor = conn.cursor()
            
            # Verify claim exists
            cursor.execute("SELECT claim_id, location FROM claims WHERE claim_id = ?", (claim_id,))
            claim = cursor.fetchone()
            
            if not claim:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Claim {claim_id} not found"
                )
            
            # Verify assessor exists and is active
            cursor.execute('''
                SELECT assessor_id, name, active_status, current_workload, max_workload
                FROM assessors
                WHERE assessor_id = ?
            ''', (assessor_id,))
            
            assessor = cursor.fetchone()
            
            if not assessor:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Assessor {assessor_id} not found"
                )
            
            if not assessor['active_status']:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Assessor {assessor_id} is not active"
                )
            
            if assessor['current_workload'] >= assessor['max_workload']:
                logger.warning(f"Assessor {assessor_id} is at full capacity")
                # Allow override but warn
            
            # Check if already assigned
            cursor.execute('''
                SELECT assignment_id, assessor_id, status
                FROM claim_assignments
                WHERE claim_id = ?
            ''', (claim_id,))
            
            existing = cursor.fetchone()
            
            if existing:
                if existing['assessor_id'] == assessor_id:
                    return {
                        "success": True,
                        "message": "Assessor already assigned to this claim",
                        "assignment_id": existing['assignment_id'],
                        "assessor_id": assessor_id,
                        "assessor_name": assessor['name']
                    }
                else:
                    # Reassignment - cancel old, create new
                    logger.info(f"Reassigning from {existing['assessor_id']} to {assessor_id}")
                    
                    # Update old assignment
                    cursor.execute('''
                        UPDATE claim_assignments
                        SET status = 'cancelled',
                            notes = ?
                        WHERE assignment_id = ?
                    ''', (f"Reassigned to {assessor_id}", existing['assignment_id']))
                    
                    # Decrease old assessor workload
                    cursor.execute('''
                        UPDATE assessors
                        SET current_workload = GREATEST(current_workload - 1, 0)
                        WHERE assessor_id = ?
                    ''', (existing['assessor_id'],))
            
            # Create new assignment
            import uuid
            assignment_id = f"ASG-{uuid.uuid4().hex[:8].upper()}"
            
            cursor.execute('''
                INSERT INTO claim_assignments (assignment_id, claim_id, assessor_id, status, notes)
                VALUES (?, ?, ?, 'pending', ?)
            ''', (assignment_id, claim_id, assessor_id, notes))
            
            # Update assessor workload
            cursor.execute('''
                UPDATE assessors
                SET current_workload = current_workload + 1
                WHERE assessor_id = ?
            ''', (assessor_id,))
            
            # Add status history
            db_manager.add_claim_status(
                conn, claim_id, 'assessor_assigned', 'manual',
                f"Manually assigned to {assessor['name']} ({assessor_id}). {notes or ''}"
            )
            
            conn.commit()
        
        logger.info(f"Assignment complete: {assignment_id}")
        
        return {
            "success": True,
            "message": "Assessor assigned successfully",
            "assignment_id": assignment_id,
            "assessor_id": assessor_id,
            "assessor_name": assessor['name'],
            "claim_id": claim_id
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Assignment error: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )

@app.get("/my-claims")
async def get_my_assigned_claims(
    assessor_id: str,
    status: Optional[str] = None  # pending/in_progress/completed
):
    """
    **Assessor Dashboard: View assigned claims**
    
    Assessor sees all claims assigned to them
    
    Filter by status:
    - pending: Not yet inspected
    - in_progress: Inspection scheduled
    - completed: Report submitted
    
    **Example:**
    ```
    GET /api/assessor/my-claims?assessor_id=ASS001&status=pending
    ```
    """
    try:
        with db_manager.get_connection() as conn:
            claims = db_manager.get_assessor_claims(conn, assessor_id, status)
        
        return {
            "success": True,
            "assessor_id": assessor_id,
            "total_claims": len(claims),
            "claims": claims
        }
        
    except Exception as e:
        logger.error(f"Error fetching assessor claims: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/assessor/{assessor_id}/dashboard-overview")
async def get_assessor_dashboard_overview_endpoint(assessor_id: str):
    """
    **Operational widgets for the assessor's own dashboard** -- pending/
    scheduled/awaiting-submission/overdue/completed counts, turnaround time,
    claim value, and a status breakdown. Deliberately excludes any
    fraud/risk figures, consistent with those staying out of the assessor's
    view everywhere else in the app.
    """
    try:
        result = db_manager.get_assessor_dashboard_overview(assessor_id)
        return result
    except Exception as e:
        logger.error(f"Error building assessor dashboard overview: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/assessors/assignment/{assignment_id}/return-for-review")
async def return_assignment_for_review_endpoint(
    assignment_id: str,
    reason: str = Form(..., description="Why this report is being sent back"),
    returned_by: str = Form(..., description="admin_id of the reviewer"),
):
    """**ADMIN: Send a submitted assessor report back for revision** -- moves the assignment to 'returned_for_review'."""
    try:
        ok = db_manager.return_assignment_for_review(assignment_id, reason, returned_by)
        if not ok:
            raise HTTPException(status_code=404, detail="Assignment not found")
        return {"success": True, "assignment_id": assignment_id, "status": "returned_for_review"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error returning assignment for review: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/claim-details/{claim_id}")
async def get_claim_details_for_assessor(claim_id: str, assessor_id: str):
    """
    **Get full claim details for assigned assessor**
    
    Shows:
    - Member information
    - Incident details
    - Member's photos and narrative
    - Assignment details
    """
    try:
        # Verify assessor is assigned to this claim
        with db_manager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT * FROM claim_assignments
                WHERE claim_id = ? AND assessor_id = ?
            ''', (claim_id, assessor_id))
            
            assignment = cursor.fetchone()
            
            if not assignment:
                raise HTTPException(
                    status_code=403,
                    detail="You are not assigned to this claim"
                )
            
            # Get claim data
            claim_data = db_manager.get_claim(claim_id)
            
            if not claim_data:
                raise HTTPException(status_code=404, detail="Claim not found")
            
            # Get member info
            member_info = db_manager.get_member_info(claim_data.get('member_id', ''))
            
            # Get photos -- claim_photo_files (raw uploads) is the source of
            # truth for "what was uploaded", populated synchronously at
            # submission time. claim_photos (AI analysis results) only gets
            # a row once the background pipeline reaches that photo, which
            # can take minutes -- querying it here meant a freshly-uploaded
            # photo simply didn't appear until analysis finished.
            cursor.execute('''
                SELECT id, filename, party, file_size, content_type, uploaded_at
                FROM claim_photo_files
                WHERE claim_id = ?
                ORDER BY uploaded_at ASC
            ''', (claim_id,))

            photos = [dict(row) for row in cursor.fetchall()]

        # Supporting documents (police abstract, ID, garage quote) uploaded
        # by either party, with their OCR-extracted data -- the assessor has
        # a legitimate need to see e.g. the member's police abstract before
        # or during inspection. This endpoint carries no fraud/risk data, so
        # unlike the admin full-report it's safe to expose here.
        documents = db_manager.get_documents_by_claim(claim_id)

        return {
            "success": True,
            "claim_id": claim_id,
            "assignment": dict(assignment),
            "claim_details": claim_data,
            "member_info": member_info,
            "photos": photos,
            "documents": documents,
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/member-claim-details/{claim_id}")
async def get_claim_details_for_member(claim_id: str, member_id: str):
    """
    **Get claim details for the member who filed it**

    Same shape as the assessor's claim-details endpoint, scoped to the
    member's own claim instead of an assessor's assignment. Carries no
    fraud/risk data (that stays admin-only) -- shows the member their own
    submission and their own uploaded documents, so they can correct
    anything OCR got wrong.

    Documents are filtered to party='member' only -- the assessor's own
    uploads (garage quote, on-site ID capture, etc.) are deliberately not
    exposed here. The assessor's evidence is meant to be independent of what
    the member says; showing it back to the member would let a colluding
    member tailor their own story to match what the assessor found, which
    defeats the point of keeping the two parties' evidence separate.
    """
    try:
        claim_data = db_manager.get_claim(claim_id)
        if not claim_data:
            raise HTTPException(status_code=404, detail="Claim not found")

        if claim_data.get('member_id') != member_id:
            raise HTTPException(status_code=403, detail="This claim does not belong to you")

        member_info = db_manager.get_member_info(member_id)

        with db_manager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT id, filename, party, file_size, content_type, uploaded_at
                FROM claim_photo_files
                WHERE claim_id = ? AND party = 'member'
                ORDER BY uploaded_at ASC
            ''', (claim_id,))
            photos = [dict(row) for row in cursor.fetchall()]

        documents = db_manager.get_documents_by_claim(claim_id, party='member')

        return {
            "success": True,
            "claim_id": claim_id,
            "claim_details": claim_data,
            "member_info": member_info,
            "photos": photos,
            "documents": documents,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/schedule-inspection")
async def schedule_inspection(
    assignment_id: str = Form(...),
    inspection_date: str = Form(...),
    location: Optional[str] = Form(None),
    notes: Optional[str] = Form(None)
):
    """
    **Assessor schedules inspection**

    Updates assignment status to 'in_progress'
    """
    try:
        with db_manager.get_connection() as conn:
            db_manager.update_assignment_status(
                conn=conn,
                assignment_id=assignment_id,
                status='in_progress',
                inspection_date=inspection_date,
                notes=notes,
                inspection_location=location
            )

            cursor = conn.cursor()
            cursor.execute("SELECT claim_id FROM claim_assignments WHERE assignment_id = ?", (assignment_id,))
            row = cursor.fetchone()

        # Best-effort -- never blocks scheduling if email fails/isn't configured.
        try:
            if row and row["claim_id"]:
                claim = db_manager.get_claim(row["claim_id"])
                member = db_manager.get_member_info(claim.get("member_id")) if claim and claim.get("member_id") else None
                if member and member.get("email"):
                    email_service.send_inspection_scheduled_email(
                        to_email=member["email"],
                        member_name=member.get("name") or "there",
                        member_id=claim["member_id"],
                        claim_id=row["claim_id"],
                        inspection_date=inspection_date,
                        location=location,
                    )
        except Exception as e:
            logger.warning(f"Inspection-scheduled email failed for assignment {assignment_id}: {str(e)}")

        return {
            "success": True,
            "message": "Inspection scheduled successfully",
            "inspection_date": inspection_date
        }

    except Exception as e:
        logger.error(f"Error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

# ============================================================================
# CLAIM STATUS TRACKING
# ============================================================================

@app.get("/claim-status/{claim_id}")
async def get_claim_workflow_status(claim_id: str):
    """
    **Get complete claim status and history**
    
    Shows:
    - Current status
    - Who's assigned
    - What's been submitted
    - Full history timeline
    """
    try:
        with db_manager.get_connection() as conn:
            # Get claim
            claim = db_manager.get_claim(claim_id)
            if not claim:
                raise HTTPException(status_code=404, detail="Claim not found")
            
            # Get assignment
            cursor = conn.cursor()
            cursor.execute('''
                SELECT * FROM claim_assignments
                WHERE claim_id = ?
            ''', (claim_id,))
            assignment = cursor.fetchone()
            
            # Get history
            history = db_manager.get_claim_status_history(conn, claim_id)
        
        # Determine current phase
        parties = claim.get('parties_analyzed', {})
        if not parties.get('member'):
            current_phase = "awaiting_member_submission"
        elif not assignment:
            current_phase = "awaiting_assessor_assignment"
        elif assignment['status'] == 'pending':
            current_phase = "awaiting_assessor_inspection"
        elif not parties.get('assessor'):
            current_phase = "awaiting_assessor_report"
        elif not parties.get('repair_shop'):
            current_phase = "awaiting_repair_estimate"
        else:
            current_phase = "complete"
        
        return {
            "success": True,
            "claim_id": claim_id,
            "current_phase": current_phase,
            "fraud_risk_score": claim.get('fraud_risk_score'),
            "risk_level": claim.get('risk_level'),
            
            "parties_submitted": parties,
            
            "assessor_assignment": dict(assignment) if assignment else None,
            
            "status_history": history,
            
            "created_at": claim.get('timestamp')
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/create-claim")
async def create_claim_after_coverage(
    coverage_check_id: str = Form(..., description="ID from coverage check"),
    member_id: str = Form(...),
    policy_id: str = Form(...),
    incident_date: str = Form(...),
    incident_location: str = Form(...),
    brief_description: str = Form(...),
    claim_type: str = Form(...),  # motor/marine/domestic
    filed_by_analyst_id: Optional[str] = Form(None, description="Set when a claims analyst is filing this on the member's behalf"),
    filing_method: Optional[str] = Form(None, description="'phone' (default when filed_by_analyst_id is set) or 'form' -- an analyst transcribing a physical claim form"),
):
    """
    **STEP 2: Create claim AFTER coverage is confirmed**
    
    Prerequisites:
    - Coverage check must be COVERED
    - Must have coverage_check_id from Step 1
    
    Returns:
    - claim_id: New generated ID (CLM-2026-001234)
    - Auto-assigned assessor details
    
    **Example:**
    ```json
    {
      "coverage_check_id": "COV-ABC123DEF456",
      "member_id": "MEM0001",
      "policy_id": "MPOL0001",
      "incident_date": "2026-03-14",
      "incident_location": "Thika Road, Nairobi",
      "brief_description": "Rear-ended at traffic lights",
      "claim_type": "motor"
    }
    ```
    """
    try:
        logger.info(f"Creating claim for {member_id}")
        
        # Verify coverage check exists and is COVERED
        with db_manager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT coverage_decision, claim_id 
                FROM coverage_check_history 
                WHERE check_id = ?
            ''', (coverage_check_id,))
            
            check = cursor.fetchone()
            
            if not check:
                raise HTTPException(
                    status_code=404,
                    detail="Coverage check not found. Run coverage check first!"
                )
            
            if check['coverage_decision'] != 'COVERED':
                raise HTTPException(
                    status_code=400,
                    detail="Member is NOT covered. Cannot create claim."
                )
            
            if check['claim_id']:
                raise HTTPException(
                    status_code=400,
                    detail=f"Claim already created: {check['claim_id']}"
                )
            
            # Generate claim ID
            claim_id = db_manager.generate_claim_id(conn)
            
            # Create basic claim record
            cursor.execute('''
                INSERT INTO claims (
                    claim_id, member_id, policy_id, narrative,
                    estimated_cost, location, accident_time,
                    fraud_risk_score, risk_level, processing_time_ms, analysis_result, created_at,
                    filed_by_analyst_id, filed_via
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, ?, ?)
            ''', (
                claim_id,
                member_id,
                policy_id,
                brief_description,
                0,          # Will be updated after assessment
                incident_location,
                incident_date,
                0,          # Will be calculated after submissions
                'pending',
                0,          # processing_time_ms - updated after AI analysis
                '{}',       # analysis_result - populated after full analysis
                filed_by_analyst_id,
                ('analyst_form' if filing_method == 'form' else 'analyst_phone') if filed_by_analyst_id else 'member_self',
            ))
            
            # Link coverage check to claim
            db_manager.link_coverage_to_claim(conn, coverage_check_id, claim_id)
            
            # Add status
            db_manager.add_claim_status(conn, claim_id, 'claim_created', 'agent',
                           f"Created from coverage check {coverage_check_id}")
            
            # Auto-assign assessor
            assignment = db_manager.assign_assessor_to_claim(
                conn=conn,
                claim_id=claim_id,
                incident_location=incident_location,
                claim_type=claim_type
            )
            
            conn.commit()

        logger.info(f"Claim created: {claim_id}")

        # Best-effort -- confirms to the member (self-filed or analyst-filed
        # alike) that their claim exists and is on an assessor's dashboard.
        # Never blocks claim creation if email fails/isn't configured.
        try:
            member = db_manager.get_member_info(member_id)
            if member and member.get("email"):
                email_service.send_claim_created_email(
                    to_email=member["email"],
                    member_name=member.get("name") or "there",
                    member_id=member_id,
                    claim_id=claim_id,
                    assessor_name=assignment.get("assessor_name"),
                )
        except Exception as e:
            logger.warning(f"Claim-created email failed for {claim_id}: {str(e)}")

        return {
            "success": True,
            "claim_id": claim_id,
            "status": "claim_created",
            "message": f"Claim {claim_id} created successfully",
            
            "assessor_assignment": assignment,
            
            "next_steps": [
                "1. Member will submit photos and detailed narrative",
                f"2. Assessor {assignment.get('assessor_name', 'TBD')} will inspect vehicle",
                "3. Repair shop will provide estimate",
                "4. System will make final decision"
            ]
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Claim creation error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
    
@app.get("/api/member/{member_id}/policies")
async def get_member_policies(member_id: str):
    """Get all policies for a member"""
    try:
        policies = db_manager.get_member_policies(member_id)
        return {"success": True, "data": policies}
    except Exception as e:
        return {"success": False, "error": str(e)}

@app.get("/api/member/{member_id}/claims")
async def get_member_claims(member_id: str):
    """Get all claims for a member"""
    try:
        with db_manager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT claim_id, policy_id, narrative, estimated_cost, 
                       location, accident_time, risk_level, created_at
                FROM claims 
                WHERE member_id = ?
                ORDER BY created_at DESC
            ''', (member_id,))
            
            rows = cursor.fetchall()
            claims = [dict(row) for row in rows]
            
        return {"success": True, "data": claims, "total": len(claims)}
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.get("/api/member/{member_id}/stats")
async def get_member_stats(member_id: str):
    """Get dashboard stats for a member"""
    try:
        with db_manager.get_connection() as conn:
            cursor = conn.cursor()
            
            # Policies stats
            cursor.execute('''
                SELECT COUNT(*) as total,
                       SUM(CASE WHEN status = 'ACTIVE' THEN 1 ELSE 0 END) as active
                FROM policies 
                WHERE member_id = ?
            ''', (member_id,))
            pol = cursor.fetchone()
            
            # Claims stats
            cursor.execute('''
                SELECT COUNT(*) as total,
                       SUM(CASE WHEN risk_level = 'high' THEN 1 ELSE 0 END) as high_risk
                FROM claims 
                WHERE member_id = ?
            ''', (member_id,))
            clm = cursor.fetchone()
            
        return {
            "success": True,
            "data": {
                "total_policies": pol["total"] or 0,
                "active_policies": pol["active"] or 0,
                "total_claims": clm["total"] or 0,
                "high_risk_claims": clm["high_risk"] or 0
            }
        }
    except Exception as e:
        return {"success": False, "error": str(e)}
   
@app.get("/api/members/search")
async def search_members(q: str = Query(..., min_length=2, description="Name, phone, email, or member ID")):
    """
    **Look up a member for a staff-filed (phoned-in) claim**

    A member self-filing is already identified by their own login session
    -- an analyst taking a call has none of that, only whatever the caller
    tells them (their name, phone number, or member ID if they have it
    handy). This is a simple LIKE search across those fields.
    """
    try:
        results = db_manager.search_members(q)
        return {"success": True, "results": results}
    except Exception as e:
        logger.error(f"Member search error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/members/create")
async def create_member(
    name: str = Form(...),
    email: Optional[str] = Form(None),
    phone: Optional[str] = Form(None),
):
    """
    **Create a member record on the fly.**

    For the "upload a physical claim form" analyst flow: the insured named
    on the form frequently isn't already in the system (a genuinely new
    policyholder, or one who's never had a claim before). Coverage checking
    and claim creation both require an existing `member_id`, so this exists
    to unblock that case rather than forcing the analyst to abandon the
    claim form just because the person isn't on file yet.

    The `members` table has no NOT NULL constraints beyond the primary key,
    so this intentionally accepts a bare minimum (a name) -- email/phone
    are for contactability, not correctness.
    """
    try:
        from utils import generate_unique_id
        member_id = generate_unique_id("MEM")
        with db_manager.get_connection() as conn:
            conn.execute(
                "INSERT INTO members (member_id, name, email, phone) VALUES (?, ?, ?, ?)",
                (member_id, name.strip(), (email or "").strip() or None, (phone or "").strip() or None),
            )
            conn.commit()
        return {"success": True, "member_id": member_id}
    except Exception as e:
        logger.error(f"Error creating member: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/policies/create")
async def create_policy(
    member_id: str = Form(...),
    policy_number: Optional[str] = Form(None),
    cover_type: str = Form("Comprehensive"),
    sum_insured: float = Form(...),
    excess: float = Form(0),
    start_date: str = Form(..., description="YYYY-MM-DD"),
    end_date: str = Form(..., description="YYYY-MM-DD"),
    vehicle_make: Optional[str] = Form(None),
    vehicle_model: Optional[str] = Form(None),
    vehicle_year: Optional[int] = Form(None),
    vehicle_reg_no: Optional[str] = Form(None),
):
    """
    **Create a motor policy on the fly, for a member with no policy on file.**

    Coverage checking (see PolicyCoverageChecker._get_member_policy in
    agents.py) requires a `policies` row with policy_type='motor' whose
    [start_date, end_date] window covers today -- there's no path around
    that requirement, so this is the minimum needed to let a
    newly-created member's claim actually proceed through /check-coverage.

    Not a substitute for real underwriting -- this is a claims-intake
    convenience for the analyst/claim-form flow, recording what the paper
    form and analyst say the policy terms are.
    """
    try:
        from utils import generate_unique_id
        policy_id = generate_unique_id("MPOL")
        with db_manager.get_connection() as conn:
            conn.execute(
                '''INSERT INTO policies
                   (policy_id, member_id, policy_type, policy_number, cover_type, sum_insured, excess, start_date, end_date)
                   VALUES (?, ?, 'motor', ?, ?, ?, ?, ?, ?)''',
                (policy_id, member_id, (policy_number or "").strip() or policy_id, cover_type, sum_insured, excess, start_date, end_date),
            )
            # Always insert a motor_policy_details row, even with no vehicle
            # info given -- coverage-check (agents.py PolicyCoverageChecker,
            # ~line 269) reads authorised_drivers unconditionally for every
            # motor claim via policy.get('authorised_drivers', '[]'), and
            # that default only applies when the KEY is absent, not when its
            # value is None/NULL -- a NULL column crashes with
            # "'NoneType' object has no attribute 'strip'" (confirmed live).
            # '[]' means "no restriction, any driver authorised", which is
            # the correct default for a policy created from a claim form
            # that doesn't list authorised drivers.
            # class_of_use also has to be non-NULL for the same reason --
            # coverage-check does policy.get('class_of_use', 'private').lower()
            # unconditionally, same None-default pitfall as authorised_drivers.
            # 'private' matches the same default check-coverage assumes for
            # incident_details['vehicle_use'] when the caller doesn't say
            # otherwise, so the two agree unless corrected later.
            conn.execute(
                '''INSERT INTO motor_policy_details
                   (policy_id, vehicle_make, vehicle_model, vehicle_year, registration_number, authorised_drivers, class_of_use)
                   VALUES (?, ?, ?, ?, ?, '[]', 'private')''',
                (policy_id, vehicle_make, vehicle_model, vehicle_year, vehicle_reg_no),
            )
            conn.commit()
        return {"success": True, "policy_id": policy_id}
    except Exception as e:
        logger.error(f"Error creating policy: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/member/{member_id}")
async def get_member_info(member_id: str):
    """Get member information with policies, claims and stats"""
    try:
        member = db_manager.get_member_info(member_id)
        if not member:
            return {"success": False, "error": "Member not found"}
        
        with db_manager.get_connection() as conn:
            cursor = conn.cursor()
            
            # Get all policies
            cursor.execute('''
                SELECT policy_id, policy_number, policy_type, cover_type, 
                       sum_insured, start_date, end_date,
                       CASE WHEN end_date >= DATE('now') THEN 'Active' ELSE 'Expired' END as policy_status
                FROM policies WHERE member_id = ?
            ''', (member_id,))
            policies = [dict(row) for row in cursor.fetchall()]
            
            # Get all claims
            cursor.execute('''
                SELECT claim_id, policy_id, narrative, estimated_cost,
                       location, accident_time, risk_level, fraud_risk_score, created_at
                FROM claims WHERE member_id = ?
                ORDER BY created_at DESC
            ''', (member_id,))
            claims = [dict(row) for row in cursor.fetchall()]
            
            # Stats
            total_policies = len(policies)
            active_policies = len([p for p in policies if p['policy_status'] == 'Active'])
            total_claims = len(claims)
            high_risk_claims = len([c for c in claims if c['risk_level'] == 'high'])
            pending_claims = len([c for c in claims if c['risk_level'] == 'pending'])
        
        return {
            "success": True,
            "data": member,
            "policies": policies,
            "claims": claims,
            "statistics": {
                "total_policies": total_policies,
                "active_policies": active_policies,
                "total_claims": total_claims,
                "high_risk_claims": high_risk_claims,
                "pending_claims": pending_claims
            }
        }
    except Exception as e:
        return {"success": False, "error": str(e)}
    
# Mount static files
if os.path.exists("static"):
    app.mount("/static", StaticFiles(directory="static"), name="static")

# Startup event
@app.on_event("startup")
async def startup_event():
    """Application startup event"""
    logger.info("Starting Old Mutual Motor Underwriting AI")
    logger.info("AI-powered fraud detection system initializing...")
    logger.info("Kenya-specific integrations ready")
    logger.info("System startup complete")

# Shutdown event
@app.on_event("shutdown")
async def shutdown_event():
    """Application shutdown event"""
    logger.info("Shutting down Old Mutual Motor Underwriting AI")
    logger.info("Saving system state...")
    logger.info("Shutdown complete")


# Health check endpoint
@app.get("/health")
async def health_simple():
    """Simple health check"""
    return {
        "status": "healthy", 
        "timestamp": datetime.now().isoformat(),
        "system": "Old Mutual Motor Underwriting AI"
    }

# Custom exception handlers
from fastapi.responses import JSONResponse

@app.exception_handler(ValueError)
async def value_error_handler(request: Request, exc: ValueError):
    """Handle validation errors"""
    return JSONResponse(
        status_code=400,
        content={
            "error": "validation_error",
            "message": str(exc)
        }
    )

@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    """Handle general exceptions"""
    logger.error(f"Unexpected error: {str(exc)}")
    return JSONResponse(
        status_code=500,
        content={
            "error": "internal_server_error",
            "message": "An unexpected error occurred"
        }
    )

# Development server
if __name__ == "__main__":
    import uvicorn
    
    print("Old Mutual Motor Underwriting AI - Demo Server")
    print("AI-powered fraud detection for Kenyan motor insurance")
    print("Starting server on http://localhost:8000")
    print("")
    print("Key Features:")
    print("   • Photo Intelligence: Duplicate detection, manipulation analysis")
    print("   • Narrative AI: LLM-powered contradiction detection")
    print("   • Risk Scoring: ML-based fraud prediction")
    print("   • Kenya Integration: NTSA, AKI, KRA, MPesa ready")
    print("")
    print("API Documentation: http://localhost:8000/docs")
    print("System Health: http://localhost:8000/health")
    print("System Stats: http://localhost:8000/api/system/stats")
    print("")
    print("Built by Grace Wanjiru @ XE.AI for Old Mutual Kenya")
    print("Press Ctrl+C to stop the server")
    print("=" * 60)
    
    uvicorn.run(
        "main:app", 
        host="0.0.0.0", 
        port=8000, 
        reload=True,
        log_level="info"
    )