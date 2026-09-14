import asyncio
import logging
import os
from typing import Dict, List, Any, Optional
from datetime import datetime
import json
import time

from ollama_client import generate, generate_json, OllamaError

from database import db_manager
from service import log_performance

logger = logging.getLogger(__name__)

# PolicyCoverageChecker does general-purpose policy-wording reasoning -- a
# different task from claims-advisory-v1's fine-tune (which is narrowly
# specialized on synthesizing narrative/CV/physics/cross-party signals into
# the AI Advisory JSON schema, via a specific system+user prompt format this
# call doesn't even use). Pinning this to the base model explicitly, rather
# than inheriting whatever OLLAMA_MODEL happens to default to, so a future
# change to that default can't silently regress coverage-check decisions
# the way it did when OLLAMA_MODEL became claims-advisory-v1 (see CLM
# coverage checks going NOT_COVERED for cases the base model handled fine).
COVERAGE_CHECK_MODEL = os.environ.get("OLLAMA_COVERAGE_MODEL", "gemma4:26b")


class PolicyCoverageChecker:
    """Checks coverage eligibility by reasoning over policy terms, and answers
    free-text member questions about their policy. Backed by a self-hosted
    Ollama model (see ollama_client.py) rather than a paid third-party API."""

    def __init__(self):
        # Load policy wordings (these are static - just store as dict/files)
        self.policy_wordings = self._load_policy_wordings()

        logger.info("PolicyCoverageChecker initialized (Ollama-backed)")
    
    def _load_policy_wordings(self) -> Dict[str, str]:
        """Load the specimen policy wordings - for now using hardcoded strings"""
        
        # TODO: Load these from files in /mnt/skills/private/ or a policy_wordings/ folder
        # For now, we'll return empty strings - you'll add the full policy text later
        
        return {
            "motor": """
            MOTOR VEHICLE INSURANCE POLICY - Specimen Policy Wording
            
            SECTION 2 – TYPES OF COVER
            
            THIRD PARTY ONLY (TPO)
            The Insurer will indemnify the Insured against legal liability for:
            • death or bodily injury to third parties
            • damage to third party property
            This cover does not include damage to the insured vehicle.
            
            THIRD PARTY FIRE AND THEFT (TPF&T)
            Includes all cover under Third Party Only and additionally covers:
            • loss of or damage to the insured vehicle caused by fire
            • loss of the vehicle caused by theft or attempted theft.
            
            COMPREHENSIVE COVER
            Includes all cover under Third Party Fire & Theft and additionally covers:
            • accidental damage to the insured vehicle
            • malicious damage
            • damage caused by collision or overturning.
            
            SECTION 10 – AUTHORISED DRIVER CLAUSE
            The vehicle may only be driven by:
            • the Insured; or
            • any other person authorised in the Schedule
            The Insurer shall not be liable if the vehicle is driven by a person not authorised.
            
            SECTION 11 – CLASS OF USE CLAUSE
            The vehicle may only be used for the purposes specified in the Schedule.
            The Insurer shall not be liable for loss or damage arising while the vehicle is used 
            for a purpose not permitted by the policy.
            
            GENERAL EXCLUSIONS
            • Driving Without a Licence
            • Driving Under the Influence
            • Mechanical Breakdown
            • Use Outside Policy Conditions
            """,
            
            "marine": """
            MARINE CARGO INSURANCE POLICY - Specimen Policy Wording
            
            SECTION 3 – INSURED TRANSIT
            Cover attaches from the time the cargo leaves the warehouse at origin 
            and continues during ordinary course of transit until delivery to final warehouse.
            
            SECTION 10 – EXCLUSIONS
            • Inherent Vice - Loss caused by natural characteristics of cargo
            • Delay - Loss caused by delay in transit
            • Poor Packing - Loss caused by inadequate packing
            • Ordinary Leakage or Loss of Weight
            
            SECTION 11 – PACKING WARRANTY
            Cargo shall be properly packed and prepared for transit.
            Failure to comply may invalidate a claim.
            """,
            
            "domestic": """
            DOMESTIC PACKAGE INSURANCE POLICY - Specimen Policy Wording
            
            SECTION 3 – CONTENTS COVER
            Loss or damage to contents within premises caused by:
            • Fire and allied perils
            • Theft following forcible entry
            • Water damage from burst pipes
            
            SECTION 6 – THEFT / BURGLARY
            Theft involving forcible and violent entry to or exit from premises.
            Not liable for:
            • theft where there is no evidence of forcible entry
            • unexplained disappearance
            
            WARRANTIES
            • Security Warranty - Premises must be protected by security measures
            • Alarm Warranty - Alarm must be operational when unattended
            • Occupancy Warranty - Notify if unoccupied for more than 30 days
            """
        }
    
    @log_performance
    async def check_coverage(
        self,
        member_id: str,
        claim_type: str,
        incident_details: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Check if member's policy covers the reported incident
        
        Args:
            member_id: Member's unique identifier
            claim_type: 'motor', 'marine', or 'domestic'
            incident_details: Dict with incident-specific fields
            
        Returns:
            Coverage analysis with decision and reasoning
        """
        
        logger.info(f"Checking coverage for member {member_id}, claim type: {claim_type}")
        
        # 1. Retrieve member's active policy from DB
        policy = self._get_member_policy(member_id, claim_type)
        
        if not policy:
            return {
                "covered": False,
                "coverage_decision": "NOT_COVERED",
                "coverage_percentage": 0,
                "reason": f"No active {claim_type} policy found for member {member_id}",
                "recommendation": "Please verify your policy details or contact support",
                "member_id": member_id,
                "claim_type": claim_type
            }
        
        # 2. Get relevant policy wording
        policy_wording = self.policy_wordings.get(claim_type, "")
        
        # 3. Check basic policy conditions (DB-level checks)
        db_checks = self._check_policy_conditions_db(policy, incident_details, claim_type)
        
        # 4. Use the local LLM to reason about coverage
        coverage_analysis = await self._analyze_coverage_with_llm(
            policy=policy,
            policy_wording=policy_wording,
            incident_details=incident_details,
            db_checks=db_checks,
            claim_type=claim_type
        )
        
        # 5. Store coverage check for audit
        db_manager.store_coverage_check(coverage_analysis)
        
        logger.info(f"Coverage check completed - Decision: {coverage_analysis.get('coverage_decision')}")
        
        return coverage_analysis
    
    def _get_member_policy(self, member_id: str, claim_type: str) -> Optional[Dict]:
        """Retrieve member's active policy from database"""
        
        try:
            with db_manager.get_connection() as conn:
                cursor = conn.cursor()
                
                # Get active policy
                cursor.execute('''
                    SELECT 
                        p.policy_id,
                        p.policy_number,
                        p.policy_type,
                        p.cover_type,
                        p.sum_insured,
                        p.excess,
                        p.start_date,
                        p.end_date,
                        p.member_id
                    FROM policies p
                    WHERE p.member_id = ?
                    AND p.policy_type = ?
                    AND DATE('now') BETWEEN p.start_date AND p.end_date
                    ORDER BY p.start_date DESC
                    LIMIT 1
                ''', (member_id, claim_type))
                
                policy_row = cursor.fetchone()
                
                if not policy_row:
                    logger.warning(f"No active {claim_type} policy found for member {member_id}")
                    return None
                
                policy = dict(policy_row)
                
                # Get claim-type specific details
                if claim_type == 'motor':
                    cursor.execute('''
                        SELECT * FROM motor_policy_details
                        WHERE policy_id = ?
                    ''', (policy['policy_id'],))
                    motor_details = cursor.fetchone()
                    if motor_details:
                        policy.update(dict(motor_details))
                
                elif claim_type == 'marine':
                    cursor.execute('''
                        SELECT * FROM marine_policy_details
                        WHERE policy_id = ?
                    ''', (policy['policy_id'],))
                    marine_details = cursor.fetchone()
                    if marine_details:
                        policy.update(dict(marine_details))
                
                elif claim_type == 'domestic':
                    cursor.execute('''
                        SELECT * FROM domestic_policy_details
                        WHERE policy_id = ?
                    ''', (policy['policy_id'],))
                    domestic_details = cursor.fetchone()
                    if domestic_details:
                        policy.update(dict(domestic_details))
                
                logger.info(f"Found policy: {policy['policy_number']} - Cover: {policy.get('cover_type','N/A')}")
                return policy
                
        except Exception as e:
            logger.error(f"Error retrieving policy: {str(e)}")
            return None
    
    def _check_policy_conditions_db(
        self,
        policy: Dict,
        incident_details: Dict,
        claim_type: str
    ) -> Dict[str, Any]:
        """Check basic policy conditions at DB level before AI analysis"""
        
        checks = {
            "policy_active": True,
            "exclusions_triggered": [],
            "warnings": []
        }
        
        if claim_type == 'motor':
            # Check driver authorization
            # `dict.get(key, default)` only substitutes `default` when the
            # KEY is absent -- a NULL DB column (or a caller that stores an
            # explicit None) still returns None here, which crashes every
            # `.strip()`/`.lower()` below with "'NoneType' object has no
            # attribute ...". Confirmed live: a policy row created before a
            # class_of_use fix landed hit exactly this on real traffic (a
            # 500 that surfaced to the user as a misleading "not covered").
            # `or` catches both cases -- missing key AND explicit None/empty.
            driver = (incident_details.get('driver_name') or '').strip()
            authorised_drivers_json = policy.get('authorised_drivers') or '[]'
            
            # Parse authorized drivers list
            try:
                if isinstance(authorised_drivers_json, list):
                    authorised_drivers = authorised_drivers_json
                elif isinstance(authorised_drivers_json, str):
                    stripped = authorised_drivers_json.strip()
                    if stripped.startswith('['):
                        # Proper JSON array
                        authorised_drivers = json.loads(stripped)
                    elif any(phrase in stripped.lower() for phrase in [
                        "policy holder", "policyholder", "spouse",
                        "any driver", "all drivers", "any licensed"
                    ]):
                        # Plain text open authorization
                        logger.info(f"Open authorization detected:'{stripped}'— all drivers permitted")
                        authorised_drivers = ["__open__"]
                    elif stripped in ("", "[]"):
                        authorised_drivers = []
                    else:
                        # Unknown format — benefit of doubt
                        logger.warning(f"Unknown authorised_drivers format:'{stripped}'— defaulting to open")
                        authorised_drivers = ["__open__"]
                else:
                    authorised_drivers = []
            except (json.JSONDecodeError, TypeError) as e:
                logger.warning(f"Failed to parse authorised_drivers:'{authorised_drivers_json}'— {str(e)}")
                authorised_drivers = ["__open__"]

            # Normalize driver name
            normalized_driver = driver.strip()

            # Debug logging
            logger.info(f"Checking driver authorization:")
            logger.info(f"   Driver from form: '{normalized_driver}'")
            logger.info(f"   Authorized drivers: {authorised_drivers}")

            # Check if driver is authorized
            if "__open__" in authorised_drivers:
                driver_authorized = True
                logger.info(f"Driver'{normalized_driver}'authorized — open policy")
            elif not authorised_drivers:
                driver_authorized = True
                logger.info(f"Driver'{normalized_driver}'authorized — no restrictions on policy")
            else:
                driver_authorized = (
                    any(normalized_driver.lower() == auth.strip().lower() for auth in authorised_drivers)
                    or incident_details.get('driver_is_insured', False)
                )

            logger.info(f"   Driver authorized: {driver_authorized}")

            checks['driver_authorized'] = driver_authorized
            if not driver_authorized:
                checks['exclusions_triggered'].append("Driver not authorized under policy")
                logger.warning(f"Driver'{driver}'not found in authorized list: {authorised_drivers}")
            else:
                logger.info(f"Driver'{driver}'is authorized")

            # Check class of use
            incident_use = (incident_details.get('vehicle_use') or 'private').lower()
            policy_use = (policy.get('class_of_use') or 'private').lower()

            checks['class_of_use_compliant'] = incident_use == policy_use
            if incident_use != policy_use:
                checks['warnings'].append(f"Vehicle used for {incident_use} but policy covers {policy_use}")

            # Check if own damage is covered
            cover_type = (policy.get('cover_type') or '').upper()

            if incident_details.get('damage_type') in ['collision', 'own_damage']:
                checks['covers_own_damage'] = cover_type == 'COMPREHENSIVE'
                if cover_type == 'TPO':
                    checks['exclusions_triggered'].append("Third Party Only policy does not cover own vehicle damage")

        logger.debug(f"DB checks completed: {checks}")
        return checks

    async def _analyze_coverage_with_llm(
        self,
        policy: Dict,
        policy_wording: str,
        incident_details: Dict,
        db_checks: Dict,
        claim_type: str
    ) -> Dict[str, Any]:
        """Use the local Ollama model to analyze coverage with full context"""

        logger.info(f"Starting Ollama coverage analysis for {claim_type} claim")

        # Build comprehensive prompt
        prompt = f"""
You are an expert insurance claims analyst for a Kenyan insurance company.
Analyze whether this {claim_type} insurance claim is covered under the policy.

=== POLICY DETAILS ===
Policy Number: {policy.get('policy_number', 'N/A')}
Policy Type: {claim_type.upper()}
Cover Type: {policy.get('cover_type', 'N/A')}
Sum Insured: KES {policy.get('sum_insured', 0):,.0f}
Policy Excess: KES {policy.get('excess', 0):,.0f}

=== INCIDENT DETAILS ===
{json.dumps(incident_details, indent=2)}

=== DATABASE VALIDATION CHECKS ===
{json.dumps(db_checks, indent=2)}

=== POLICY WORDING (REFERENCE) ===
{policy_wording}

=== KEY COVERAGE PRINCIPLE — READ BEFORE DECIDING ===
Cover tiers are cumulative: Comprehensive includes everything in Third Party Fire &
Theft (TPF&T), which includes everything in Third Party Only (TPO). Third-party
liability cover — death/injury to third parties, or damage to THIRD PARTY property —
is included at EVERY tier (TPO, TPF&T, and Comprehensive alike), regardless of the
mechanism of loss (collision, fire, etc.). It does NOT require Comprehensive cover.
The "accidental damage / collision" clause under Comprehensive applies ONLY to damage
to the INSURED'S OWN vehicle — it does not restrict or gate third-party property
damage claims, which are already covered at every tier.

Before applying any exclusion, first determine WHOSE vehicle or property was actually
damaged in this incident: the insured's own vehicle, or a third party's. That
determines which policy section actually governs the decision — do not decline a
third-party property damage claim just because the incident involved a collision.

TPF&T grants fire and theft cover for the INSURED'S OWN vehicle as its own
independent benefit — separate from, and not dependent on, general accidental
"Own Damage"/Comprehensive cover. A "Covers Own Damage: false" database flag means
the policy lacks the broader Comprehensive accidental-damage benefit; it does NOT
cancel TPF&T's own fire or theft cover, which applies regardless of that flag.

Distinguish the INSURED PERIL from its underlying CAUSE. If the claimed loss is fire
damage, that is fire cover — it applies even if an electrical or mechanical fault was
what ignited the fire. The "Mechanical Breakdown" exclusion applies only when the
claim IS a mechanical failure itself (e.g. engine seizure, transmission failure) with
no fire or collision loss — not when a mechanical/electrical fault merely happened to
be the ignition source of a fire that is otherwise covered under this policy's fire
peril.

Respond with ONLY a JSON object in this exact shape:
{{
    "covered": true/false,
    "coverage_percentage": 0-100,
    "coverage_decision": "COVERED" / "NOT_COVERED" / "REQUIRES_INVESTIGATION",
    "applicable_excess": <amount in KES>,
    "estimated_payout": <amount in KES or null>,
    "reasons_for_decision": ["reason 1", "reason 2"],
    "exclusions_triggered": ["exclusion 1"],
    "conditions_to_verify": ["condition 1"],
    "recommendation": "APPROVE_CLAIM / DECLINE_CLAIM / REQUEST_ASSESSOR / REQUEST_MORE_INFO"
}}
"""

        try:
            # Run in a worker thread — generate_json() uses blocking `requests`
            # calls, which would otherwise freeze the whole asyncio event loop
            # (every request on the server, not just this one) for as long as
            # Ollama takes to respond.
            result = await asyncio.to_thread(generate_json, prompt, model=COVERAGE_CHECK_MODEL, retries=2, timeout=120)

            # Add metadata
            result['member_id'] = policy.get('member_id')
            result['policy_id'] = policy.get('policy_id')
            result['policy_number'] = policy.get('policy_number')
            result['claim_type'] = claim_type
            result['analysis_timestamp'] = datetime.now().isoformat()

            return result

        except OllamaError as e:
            logger.error(f"Ollama coverage analysis failed: {str(e)}")

            return {
                "covered": None,
                "coverage_decision": "REQUIRES_MANUAL_REVIEW",
                "coverage_percentage": 50,
                "applicable_excess": policy.get('excess', 0),
                "reasons_for_decision": [f"Automated analysis failed: {str(e)}"],
                "recommendation": "REQUEST_MANUAL_REVIEW",
                "error": str(e)
            }

    async def answer_policy_question(
        self,
        member_id: str,
        claim_type: str,
        question: str
    ) -> Dict[str, Any]:
        """
        Answer a free-text question a member asks about their own policy
        (e.g. "what's my excess?", "am I covered for theft?"). Unlike
        check_coverage(), this isn't tied to filing a claim — it's general
        Q&A grounded in the member's actual policy record and wording.
        """

        logger.info(f"Policy Q&A for member {member_id} ({claim_type}): {question}")

        policy = self._get_member_policy(member_id, claim_type)
        if not policy:
            return {
                "answer": (
                    f"I couldn't find an active {claim_type} policy for this account. "
                    "Please check your policy details or contact support."
                ),
                "policy_number": None,
                "member_id": member_id,
                "claim_type": claim_type,
            }

        policy_wording = self.policy_wordings.get(claim_type, "")

        prompt = f"""
You are a helpful insurance assistant for a Kenyan insurance company. Answer
the member's question using ONLY the policy details and policy wording below.
Be concise and plain-language, and cite real figures from their policy
(sum insured, excess, dates, etc.) where relevant. If the question can't be
answered from the information given, say so honestly instead of guessing.

=== MEMBER'S POLICY ===
Policy Number: {policy.get('policy_number', 'N/A')}
Cover Type: {policy.get('cover_type', 'N/A')}
Sum Insured: KES {policy.get('sum_insured', 0):,.0f}
Excess: KES {policy.get('excess', 0):,.0f}
Start Date: {policy.get('start_date', 'N/A')}
End Date: {policy.get('end_date', 'N/A')}

=== POLICY WORDING (REFERENCE) ===
{policy_wording}

=== MEMBER'S QUESTION ===
{question}

Answer in plain text, 2-4 sentences.
"""

        try:
            # Run in a worker thread — see comment in _analyze_coverage_with_llm.
            answer = await asyncio.to_thread(generate, prompt, timeout=120)
        except OllamaError as e:
            logger.error(f"Ollama policy Q&A failed: {str(e)}")
            answer = (
                "Sorry, I'm unable to answer that right now. Please contact "
                "support or try again shortly."
            )

        return {
            "answer": answer.strip(),
            "policy_number": policy.get('policy_number'),
            "member_id": member_id,
            "claim_type": claim_type,
        }