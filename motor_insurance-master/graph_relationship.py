"""
Graph-based relationship analysis (Appendix C, §C.2).

Deliberately NOT a graph database or network-analysis library -- a handful
of shared-field lookups across claims/members/repair-shops is enough to
surface the "hidden relationship" patterns this capability is meant to
catch (same phone number, bank account, or repair shop appearing across
policyholders who should otherwise be unrelated). A real graph engine would
be the natural next step once there's enough relationship data to make
traversal-depth-2+ queries worthwhile; this is the honest v1.
"""

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


@dataclass
class RelationshipFinding:
    relationship_type: str  # shared_phone / shared_bank_account / shared_repair_shop
    severity: str
    description: str
    related_member_ids: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.relationship_type,
            "severity": self.severity,
            "description": self.description,
            "related_member_ids": self.related_member_ids,
        }


@dataclass
class RelationshipResult:
    findings: List[RelationshipFinding] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {"findings": [f.to_dict() for f in self.findings]}

    @property
    def observation_text(self) -> str:
        if not self.findings:
            return "None"
        return "\n".join(f"- [{f.severity.upper()}] {f.description}" for f in self.findings)


def analyze_relationships(
    db_manager, claim_id: str, member_id: str, repair_shop_id: str = None,
    surveyor_contact: str = None,
) -> RelationshipResult:
    result = RelationshipResult()

    member = db_manager.get_member_info(member_id) or {}

    # Shared phone number across different policyholders.
    phone = member.get("phone")
    if phone:
        try:
            with db_manager.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT DISTINCT c.member_id, m.name FROM claims c
                    JOIN members m ON m.member_id = c.member_id
                    WHERE m.phone = ? AND c.member_id != ? AND c.claim_id != ?
                    """,
                    (phone, member_id, claim_id),
                )
                others = [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            logger.warning(f"graph_relationship phone lookup failed: {e}")
            others = []
        if others:
            names = ", ".join(f"{o['name']} ({o['member_id']})" for o in others)
            result.findings.append(RelationshipFinding(
                "shared_phone", "high",
                f"This claim's phone number is also linked to {len(others)} other claimant(s) — {names} — "
                "worth checking whether these are genuinely separate people.",
                [o["member_id"] for o in others],
            ))

    # Shared bank account across different policyholders.
    bank_account = member.get("bank_account")
    if bank_account:
        others = db_manager.find_members_sharing_bank_account(bank_account, member_id)
        if others:
            names = ", ".join(f"{o['name']} ({o['member_id']})" for o in others)
            result.findings.append(RelationshipFinding(
                "shared_bank_account", "critical",
                f"This claimant's payout bank account is also registered to {len(others)} other "
                f"policyholder(s) — {names} — a strong indicator these claims may be connected.",
                [o["member_id"] for o in others],
            ))

    # Shared repair shop with an elevated fraud-flag history.
    if repair_shop_id:
        try:
            with db_manager.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT name, fraud_flag_count, total_claims FROM repair_shops WHERE shop_id = ?",
                    (repair_shop_id,),
                )
                row = cursor.fetchone()
        except Exception as e:
            logger.warning(f"graph_relationship repair-shop lookup failed: {e}")
            row = None
        if row and row["total_claims"] and (row["fraud_flag_count"] or 0) / row["total_claims"] >= 0.15:
            result.findings.append(RelationshipFinding(
                "shared_repair_shop", "medium",
                f"This claim's repair shop ({row['name']}) has a fraud-flag rate of "
                f"{row['fraud_flag_count']}/{row['total_claims']} claims — above what's typical, "
                "worth factoring in alongside this claim's own signals.",
                [],
            ))

    # Marine: shared surveyor contact or bank token across otherwise-
    # unrelated cargo/hull claims (developer guide's M01 illustration).
    # No dedicated surveyors table exists yet -- this searches
    # claim_documents' own OCR-extracted parsed_fields for a matching
    # contact, the same "handful of shared-field lookups" spirit as the
    # phone/bank checks above rather than a new graph schema.
    if surveyor_contact:
        try:
            with db_manager.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT DISTINCT claim_id, parsed_fields FROM claim_documents "
                    "WHERE claim_id != ? AND parsed_fields LIKE ?",
                    (claim_id, f"%{surveyor_contact}%"),
                )
                other_claim_ids = sorted({row["claim_id"] for row in cursor.fetchall()})
        except Exception as e:
            logger.warning(f"graph_relationship surveyor-contact lookup failed: {e}")
            other_claim_ids = []
        if other_claim_ids:
            result.findings.append(RelationshipFinding(
                "shared_surveyor_contact", "medium",
                f"The surveyor/contact on this claim also appears on {len(other_claim_ids)} other "
                f"claim(s) — {', '.join(other_claim_ids)} — this requires verification, not a finding "
                "of collusion on its own.",
                [],
            ))

    return result
