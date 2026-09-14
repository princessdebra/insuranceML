"""
OCR + structured field extraction for claim-supporting documents (police
abstracts, ID documents, garage quotes) uploaded by either the member or the
assessor.

Uses the same Ollama vision model already relied on for damage-photo
analysis (service.py's PhotoAnalysisService) instead of a dedicated OCR
engine like Tesseract -- the devserver has no sudo access and the Tesseract
binary isn't guaranteed to exist everywhere this runs, whereas the vision
model is already a hard dependency of the rest of the pipeline. A vision LLM
reads structured documents (printed forms, ID cards, quotes) well enough for
this PoC and gives one consistent code path across environments.
"""
import asyncio
import json
import logging
import os
import re
from typing import Any, Dict, List, Union

logger = logging.getLogger(__name__)

OCR_VISION_MODEL = os.environ.get("OLLAMA_VISION_MODEL", "gemma4:26b")

_PROMPTS = {
    "police_abstract": """
You are extracting data from a photo of a Kenyan police abstract / occurrence
book (OB) report related to a motor accident claim.

Return ONLY valid JSON -- no text outside the JSON block.

{{
    "raw_text": "the full text visible on the document, transcribed as accurately as possible",
    "parsed_fields": {{
        "ob_number": "the OB/abstract number, or null if not visible",
        "police_station": "the station name, or null",
        "date_reported": "date reported to police (YYYY-MM-DD if determinable), or null",
        "incident_date": "date of the incident itself if stated separately, or null",
        "complainant_name": "name of the person who reported, or null",
        "incident_summary": "one sentence summarizing what the document says happened, or null"
    }},
    "extraction_confidence": 0-100,
    "document_appears_genuine": true/false,
    "quality_notes": "one sentence on legibility/completeness issues, empty string if none"
}}
""",
    "id_document": """
You are extracting data from a photo of a Kenyan national ID card or driving
licence submitted as identity verification for a motor insurance claim.

Return ONLY valid JSON -- no text outside the JSON block.

{{
    "raw_text": "the full text visible on the document, transcribed as accurately as possible",
    "parsed_fields": {{
        "full_name": "the name printed on the document, or null",
        "id_number": "the ID/licence number, or null",
        "date_of_birth": "YYYY-MM-DD if determinable, or null",
        "document_type_detected": "national_id / driving_licence / other"
    }},
    "extraction_confidence": 0-100,
    "document_appears_genuine": true/false,
    "quality_notes": "one sentence on legibility/completeness issues, empty string if none"
}}
""",
    "garage_quote": """
You are extracting data from a photo of a garage/repair shop's repair quote
or invoice submitted as part of a motor insurance claim.

Return ONLY valid JSON -- no text outside the JSON block.

{{
    "raw_text": "the full text visible on the document, transcribed as accurately as possible",
    "parsed_fields": {{
        "garage_name": "the garage/shop name, or null",
        "total_amount": "the total quoted amount as a number (KES), or null",
        "parts_and_labour_items": ["short description of each line item listed"],
        "quote_date": "YYYY-MM-DD if determinable, or null"
    }},
    "extraction_confidence": 0-100,
    "document_appears_genuine": true/false,
    "quality_notes": "one sentence on legibility/completeness issues, empty string if none"
}}
""",
    "claim_form": """
You are extracting data from photo(s) of a filled-in Old Mutual "Motor Accident Claim Form" -- a
multi-section paper form a policyholder completes by hand and hands (or a claims analyst
receives) to start a motor insurance claim. You may be given multiple page images of the same
form -- combine everything you read across all of them into one JSON result.

The form has four sections. Only extract what is actually filled in -- most forms leave many
fields blank, and a blank field must be null (or an empty array), never guessed or invented.

Return ONLY valid JSON -- no text outside the JSON block.

{{
    "raw_text": "the full text visible across all pages, transcribed as accurately as possible",
    "parsed_fields": {{
        "policy_no": "policy number from Section A, or null",
        "branch": "branch name, or null",
        "cover_type": "Comprehensive / TPF&T / TPO -- whichever box is ticked, or null",
        "insured_full_name": "the insured's full name (combine surname/middle/first, or the registered company name), or null",
        "insured_id_no": "ID or passport number, or null",
        "insured_phone": "best phone number given (mobile preferred over office/residential), or null",
        "insured_email": "email address, or null",
        "insured_address": "postal or physical address, or null",
        "vehicle_make": "vehicle make, or null",
        "vehicle_model": "vehicle model, or null",
        "vehicle_year": "year of manufacture, or null",
        "vehicle_reg_no": "registration number, or null",
        "registered_owner_name": "name and address of registered owner if different from the insured, or null",
        "accident_date": "date of the accident, YYYY-MM-DD if determinable, or null",
        "accident_time": "time of the accident as written (include am/pm), or null",
        "accident_place": "place the accident occurred, or null",
        "road_surface": "type of road surface, or null",
        "weather_condition": "wet or dry / weather described, or null",
        "damage_description": "the 'state briefly apparent damage' free-text answer, or null",
        "repairer_name": "repairer's name, or null",
        "repairer_address": "repairer's address, or null",
        "repairer_phone": "repairer's phone number, or null",
        "vehicle_still_in_use": true/false/null,
        "police_involved": true/false/null,
        "police_station": "police station named, or null",
        "police_constable_number": "constable number given, or null",
        "third_party_vehicles": [
            {{"owner_name": "...", "reg_no": "...", "insurer": "..."}}
        ],
        "third_party_property_damaged": [
            {{"owner_name": "...", "property_damaged": "..."}}
        ],
        "persons_injured": [
            {{"name": "...", "relationship_to_insured": "...", "apparent_injuries": "..."}}
        ],
        "witnesses": [
            {{"name": "...", "address": "..."}}
        ],
        "driver_name": "the driver's name from Section D (may be the same as the insured), or null",
        "driver_relationship_to_insured": "e.g. 'self', 'employee', 'family member', or null",
        "driver_employed_by_insured": true/false/null,
        "driver_had_permission": true/false/null,
        "driver_to_blame": true/false/null,
        "driver_admitted_liability": true/false/null,
        "driver_licence_number": "driver's licence number, or null",
        "driver_statement": "the free-text 'STATEMENT BY DRIVER' narrative from Section B, or null",
        "owner_statement": "the free-text 'STATEMENT BY OWNER/INSURED' narrative from Section D, or null",
        "declaration_date": "the date the form was signed, YYYY-MM-DD if determinable, or null"
    }},
    "extraction_confidence": 0-100,
    "document_appears_genuine": true/false,
    "quality_notes": "one sentence on legibility/completeness/handwriting issues, empty string if none"
}}
""",
    "other": """
You are extracting data from a photo of a supporting document submitted as
part of a motor insurance claim. The document type is not known in advance.

Return ONLY valid JSON -- no text outside the JSON block.

{{
    "raw_text": "the full text visible on the document, transcribed as accurately as possible",
    "parsed_fields": {{
        "apparent_document_type": "your best guess at what kind of document this is",
        "key_details": ["short bullet points of any names, numbers, dates, or amounts visible"]
    }},
    "extraction_confidence": 0-100,
    "document_appears_genuine": true/false,
    "quality_notes": "one sentence on legibility/completeness issues, empty string if none"
}}
""",
}


def _strip_code_fence(text: str) -> str:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    return match.group(0) if match else text


async def extract_document_data(
    image_data: Union[bytes, List[bytes]], filename: str, document_type: str
) -> Dict[str, Any]:
    """
    OCR + structured extraction for a document. Accepts either a single
    image or a list of images (multi-page forms, e.g. the 4-page claim
    form -- the vision model reads all pages in one call and merges what it
    finds across them into one JSON result, rather than needing a separate
    extraction + manual merge per page).
    Never raises -- returns extraction_confidence=0 and an "error" key on
    failure, so a flaky Ollama connection degrades to "document stored with
    no extracted data" rather than blocking the claim submission it's
    attached to.
    """
    document_type = document_type if document_type in _PROMPTS else "other"
    prompt = _PROMPTS[document_type]
    images = image_data if isinstance(image_data, list) else [image_data]

    try:
        from ollama_client import generate, OllamaError

        # The claim-form schema has ~30 fields plus nested arrays -- the
        # client's default 1024-token cap truncates it mid-JSON-string
        # (confirmed live: json.loads raised "Unterminated string" on a
        # real extraction attempt), so it gets a much larger budget than
        # the other, smaller document schemas need.
        num_predict = 4096 if document_type == "claim_form" else None

        response_text = await asyncio.to_thread(
            generate, prompt, model=OCR_VISION_MODEL, images=images,
            json_mode=True, timeout=120, num_predict=num_predict,
        )
        parsed = json.loads(_strip_code_fence(response_text))

        raw_text = parsed.get("raw_text", "") or ""
        parsed_fields = parsed.get("parsed_fields", {}) or {}
        confidence = parsed.get("extraction_confidence", 0)
        fields_populated = any(v not in (None, "", [], {}) for v in parsed_fields.values())

        # Sanity guard: a blank/unreadable image can make the vision model
        # echo its own prompt instructions back as "raw_text" while still
        # self-reporting high confidence (seen in testing with a blank
        # image). Don't trust the model's own confidence score when it
        # extracted nothing structured -- and don't pass prompt-echo through
        # as if it were real document text, since that's actively misleading
        # to a reviewer who trusts a "100% confidence" result.
        if not fields_populated:
            confidence = 0
            if prompt.strip()[:80] in raw_text:
                raw_text = ""

        return {
            "document_type": document_type,
            "raw_text": raw_text,
            "parsed_fields": parsed_fields,
            "extraction_confidence": confidence,
            "document_appears_genuine": parsed.get("document_appears_genuine"),
            "quality_notes": parsed.get("quality_notes", ""),
            "extraction_method": "ollama_vision",
        }
    except Exception as e:
        logger.warning(f"OCR extraction failed for {filename} ({document_type}): {e}")
        return {
            "document_type": document_type,
            "raw_text": "",
            "parsed_fields": {},
            "extraction_confidence": 0,
            "document_appears_genuine": None,
            "quality_notes": "",
            "extraction_method": "failed",
            "error": str(e),
        }


def build_document_evidence_block(documents: list[Dict[str, Any]]) -> str:
    """
    Formats OCR'd document data into a clearly-labeled evidence block to
    prepend to a party's narrative -- same pattern as
    build_structured_intake_block / build_assessor_measurement_block, so
    downstream narrative analysis treats document-extracted facts as
    confirmed evidence rather than something it has to infer from prose.
    """
    if not documents:
        return ""

    lines = ["SUPPORTING DOCUMENTS (OCR-extracted, not narrative inference):"]
    for doc in documents:
        doc_type = doc.get("document_type", "document").replace("_", " ").title()
        fields = doc.get("parsed_fields") or {}
        if not fields and not doc.get("raw_text"):
            lines.append(f"- {doc_type}: uploaded, but text extraction failed or was empty.")
            continue
        field_str = ", ".join(
            f"{k.replace('_', ' ')}: {v}" for k, v in fields.items() if v not in (None, "", [])
        )
        confidence = doc.get("extraction_confidence", 0)
        lines.append(f"- {doc_type} (extraction confidence {confidence}%): {field_str or 'no structured fields extracted'}")

    return "\n".join(lines)
