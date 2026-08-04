"""
AI Governance & Regulatory Layer (AI-ROL).

Per the PoC Blueprint, AI-ROL underpins every AI capability (Narrative
Intelligence, Computer Vision, Physics & Mathematical Consistency,
Cross-validation & Risk Intelligence, AI Advisory) by recording every
recommendation, its confidence, its supporting evidence, and any claims
handler action taken in response -- providing a single explainable,
traceable, auditable record per claim. It does not perform analysis
itself; capabilities call record_recommendation() after they produce a
result, and the claims handler UI calls record_handler_action() when a
recommendation is proceeded, clarified, escalated or overridden.
"""

import json
import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from database import db_manager

logger = logging.getLogger(__name__)

VALID_CAPABILITIES = {
    "business_rules",
    "narrative_intelligence",
    "computer_vision",
    "physics_consistency",
    "cross_validation",
    "ai_advisory",
}

VALID_ACTIONS = {"proceed", "clarify", "escalate", "override"}


def record_recommendation(
    claim_id: str,
    capability: str,
    recommendation: str,
    confidence: Optional[float] = None,
    evidence: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Record a single AI-generated recommendation for a claim. Called by each
    capability immediately after it produces a result. Never raises --
    a governance-logging failure should not break claim analysis.
    """
    if capability not in VALID_CAPABILITIES:
        logger.warning(f"AI-ROL: unknown capability '{capability}' for {claim_id} -- recording anyway")

    record_id = f"ROL-{uuid.uuid4().hex[:12].upper()}"
    try:
        with db_manager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO ai_rol_records
                    (record_id, claim_id, capability, recommendation, confidence, evidence)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    record_id,
                    claim_id,
                    capability,
                    recommendation,
                    confidence,
                    json.dumps(evidence) if evidence is not None else None,
                ),
            )
            conn.commit()
        logger.info(f"AI-ROL recorded: {capability} for {claim_id} (confidence={confidence})")
    except Exception as e:
        logger.error(f"AI-ROL failed to record {capability} for {claim_id}: {e}")

    return record_id


def record_handler_action(
    claim_id: str,
    handler_id: str,
    action: str,
    capability: str = "ai_advisory",
    reason: Optional[str] = None,
) -> bool:
    """
    Record the claims handler's decision on the most recent un-actioned
    recommendation for the given capability (defaults to ai_advisory, since
    that's the consolidated recommendation the handler actually reviews).

    An 'override' without a reason is rejected -- AIROL-007 requires the
    override reason to be captured, not merely permitted.
    """
    if action not in VALID_ACTIONS:
        raise ValueError(f"Invalid handler action '{action}' -- must be one of {VALID_ACTIONS}")
    if action == "override" and not reason:
        raise ValueError("An override reason is required when action='override'")

    with db_manager.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT record_id FROM ai_rol_records
            WHERE claim_id = ? AND capability = ? AND handler_action IS NULL
            ORDER BY created_at DESC LIMIT 1
            """,
            (claim_id, capability),
        )
        row = cursor.fetchone()

        if row:
            cursor.execute(
                """
                UPDATE ai_rol_records
                SET handler_action = ?, handler_id = ?, override_reason = ?, decided_at = ?
                WHERE record_id = ?
                """,
                (action, handler_id, reason, datetime.utcnow().isoformat(), row["record_id"]),
            )
        else:
            # No matching AI recommendation exists yet (e.g. handler acted
            # before analysis completed) -- still record the action so the
            # audit trail has no silent gaps.
            record_id = f"ROL-{uuid.uuid4().hex[:12].upper()}"
            cursor.execute(
                """
                INSERT INTO ai_rol_records
                    (record_id, claim_id, capability, recommendation, handler_action,
                     handler_id, override_reason, decided_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record_id, claim_id, capability,
                    "(handler action recorded with no prior AI recommendation on file)",
                    action, handler_id, reason, datetime.utcnow().isoformat(),
                ),
            )
        conn.commit()

    logger.info(f"AI-ROL handler action recorded: {claim_id}/{capability} -> {action} by {handler_id}")
    return True


def get_audit_trail(claim_id: str) -> List[Dict[str, Any]]:
    """Full chronological AI-ROL audit trail for a claim."""
    with db_manager.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT record_id, claim_id, capability, recommendation, confidence,
                   evidence, created_at, handler_action, handler_id, override_reason, decided_at
            FROM ai_rol_records
            WHERE claim_id = ?
            ORDER BY created_at ASC
            """,
            (claim_id,),
        )
        rows = cursor.fetchall()

    trail = []
    for row in rows:
        entry = dict(row)
        if entry.get("evidence"):
            try:
                entry["evidence"] = json.loads(entry["evidence"])
            except (json.JSONDecodeError, TypeError):
                pass
        trail.append(entry)
    return trail
