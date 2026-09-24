"""
OCR + structured field extraction for claim-supporting documents -- police
abstracts, ID documents and garage quotes (Motor), and invoices, packing
lists, bills of lading, delivery notes, survey reports and master statements
(Marine Cargo/Hull/Goods in Transit) -- uploaded by either the member/
claimant or the assessor/analyst.

Uses the same vision model already relied on for damage-photo analysis
(service.py's PhotoAnalysisService, via ollama_client.py -> the XeAI
Gateway) instead of a dedicated OCR engine like Tesseract -- the devserver
has no sudo access and the Tesseract binary isn't guaranteed to exist
everywhere this runs, whereas the vision model is already a hard dependency
of the rest of the pipeline. A vision LLM reads structured documents
(printed forms, ID cards, quotes, shipping documents) well enough for this
PoC and gives one consistent code path across environments.
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
    "invoice": """
You are extracting data from a photo of a commercial invoice submitted as part
of a Marine Cargo or Goods in Transit insurance claim.

Return ONLY valid JSON -- no text outside the JSON block.

{{
    "raw_text": "the full text visible on the document, transcribed as accurately as possible",
    "parsed_fields": {{
        "invoice_number": "the invoice number, or null",
        "invoice_date": "YYYY-MM-DD if determinable, or null",
        "seller_name": "the seller/exporter name, or null",
        "buyer_name": "the buyer/consignee name, or null",
        "commodity_description": "description of the goods, or null",
        "quantity": "the total quantity of units/cartons/items invoiced, as a plain number (no units), or null",
        "quantity_unit": "the unit the quantity is counted in (e.g. cartons, units, kg), or null",
        "total_amount": "the total invoiced value as a number, or null",
        "currency": "the currency code/symbol shown (e.g. USD, KES), or null"
    }},
    "extraction_confidence": 0-100,
    "document_appears_genuine": true/false,
    "quality_notes": "one sentence on legibility/completeness issues, empty string if none"
}}
""",
    "packing_list": """
You are extracting data from a photo of a packing list submitted as part of a
Marine Cargo or Goods in Transit insurance claim -- it itemizes exactly what
was packed for shipment, separate from the commercial invoice's pricing.

Return ONLY valid JSON -- no text outside the JSON block.

{{
    "raw_text": "the full text visible on the document, transcribed as accurately as possible",
    "parsed_fields": {{
        "reference_number": "the packing list's own reference/number, or null",
        "commodity_description": "description of the goods, or null",
        "quantity": "the total quantity of units/cartons/items listed, as a plain number (no units), or null",
        "quantity_unit": "the unit the quantity is counted in (e.g. cartons, units, kg), or null",
        "container_number": "the shipping container number, or null",
        "seal_number": "the container seal number shown, or null",
        "gross_weight_kg": "total gross weight in kg as a number, or null"
    }},
    "extraction_confidence": 0-100,
    "document_appears_genuine": true/false,
    "quality_notes": "one sentence on legibility/completeness issues, empty string if none"
}}
""",
    "bill_of_lading": """
You are extracting data from a photo of a bill of lading or air waybill
submitted as part of a Marine Cargo insurance claim -- the transport
document/contract of carriage.

Return ONLY valid JSON -- no text outside the JSON block.

{{
    "raw_text": "the full text visible on the document, transcribed as accurately as possible",
    "parsed_fields": {{
        "bl_number": "the bill of lading / waybill number, or null",
        "shipper_name": "the shipper/exporter name, or null",
        "consignee_name": "the consignee name, or null",
        "vessel_name": "the carrying vessel or flight/conveyance name, or null",
        "port_of_loading": "the origin port/airport, or null",
        "port_of_discharge": "the destination port/airport, or null",
        "container_number": "the shipping container number, or null",
        "seal_number": "the container seal number shown, or null",
        "quantity": "the total quantity of units/cartons/items shown, as a plain number (no units), or null",
        "quantity_unit": "the unit the quantity is counted in (e.g. cartons, units, kg), or null",
        "issue_date": "YYYY-MM-DD if determinable, or null"
    }},
    "extraction_confidence": 0-100,
    "document_appears_genuine": true/false,
    "quality_notes": "one sentence on legibility/completeness issues, empty string if none"
}}
""",
    "delivery_note": """
You are extracting data from a photo of a delivery note, gate pass, tally
sheet or weighbridge record submitted as part of a Marine Cargo or Goods in
Transit insurance claim -- it records what was actually handed over/received
at a handover point, which is what gets compared against the invoice/packing
list to find a shortage.

Return ONLY valid JSON -- no text outside the JSON block.

{{
    "raw_text": "the full text visible on the document, transcribed as accurately as possible",
    "parsed_fields": {{
        "reference_number": "the delivery note / gate pass number, or null",
        "delivery_date": "YYYY-MM-DD if determinable, or null",
        "quantity": "the quantity actually delivered/received/tallied, as a plain number (no units), or null",
        "quantity_unit": "the unit the quantity is counted in (e.g. cartons, units, kg), or null",
        "container_number": "the shipping container number, or null",
        "seal_number": "the container seal number recorded AT THIS handover point, or null",
        "seal_intact": "true if the record states the seal was intact/unbroken at this point, false if broken/tampered, null if not stated",
        "received_by": "name of the person who signed for/received the goods, or null",
        "delivered_by": "name of the driver/transporter who delivered, or null"
    }},
    "extraction_confidence": 0-100,
    "document_appears_genuine": true/false,
    "quality_notes": "one sentence on legibility/completeness issues, empty string if none"
}}
""",
    "survey_report": """
You are extracting data from a photo of a marine surveyor's report submitted
as part of a Marine Hull or Marine Cargo insurance claim -- an independent
inspection of the vessel or cargo condition/damage.

Return ONLY valid JSON -- no text outside the JSON block.

{{
    "raw_text": "the full text visible on the document, transcribed as accurately as possible",
    "parsed_fields": {{
        "surveyor_name": "the surveyor's name, or null",
        "surveyor_contact": "a phone number or other contact detail for the surveyor, or null",
        "survey_date": "YYYY-MM-DD if determinable, or null",
        "vessel_or_shipment_reference": "the vessel ID/name or shipment reference the survey concerns, or null",
        "findings_summary": "one or two sentences summarizing what the surveyor found (damage/shortage/condition), or null",
        "quantity_shortage": "if the survey states a shortage quantity, that number, or null",
        "cause_stated": "the cause the surveyor attributes the loss to, if stated -- report exactly what is written, do not infer, or null"
    }},
    "extraction_confidence": 0-100,
    "document_appears_genuine": true/false,
    "quality_notes": "one sentence on legibility/completeness issues, empty string if none"
}}
""",
    "master_statement": """
You are extracting data from a photo of a vessel master's or ship operator's
written statement submitted as part of a Marine Hull insurance claim,
describing what happened during the incident.

Return ONLY valid JSON -- no text outside the JSON block.

{{
    "raw_text": "the full text visible on the document, transcribed as accurately as possible",
    "parsed_fields": {{
        "master_name": "the master/operator's name, or null",
        "vessel_reference": "the vessel name/ID referenced, or null",
        "statement_date": "YYYY-MM-DD if determinable, or null",
        "incident_summary": "one or two sentences summarizing what the master says happened, or null",
        "reported_cause": "the cause the master attributes the incident to, as written -- do not infer beyond what's stated, or null",
        "actions_taken": "any actions the master says were taken (e.g. moved to safe anchorage), or null"
    }},
    "extraction_confidence": 0-100,
    "document_appears_genuine": true/false,
    "quality_notes": "one sentence on legibility/completeness issues, empty string if none"
}}
""",
    "other": """
You are extracting data from a photo of a supporting document submitted as
part of an insurance claim. The document type is not known in advance.

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


_GATEWAY_MAX_IMAGES_PER_CALL = 2  # qwen2.5-vl-7b's hard per-request limit, via the XeAI Gateway


def _merge_extractions(chunk_results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Combines per-batch extraction results (see extract_document_data) into
    one, for documents that needed more than one vision call because they
    have more pages than the gateway allows per request.

    - raw_text: concatenated in page order.
    - parsed_fields: list-valued fields (witnesses, third-party vehicles,
      etc.) are concatenated across batches, since different pages
      genuinely list different people/vehicles; scalar fields take the
      first non-empty value found, in page order (the form's own field
      order means the "real" answer for a given field almost always
      appears on one specific page, not several conflicting ones).
    - extraction_confidence: the minimum across batches -- a multi-page
      result is only as trustworthy as its worst-read page.
    - document_appears_genuine: False if any batch says False, True only if
      every batch says True, else None (inconclusive).
    - quality_notes: distinct non-empty notes joined together.
    """
    if len(chunk_results) == 1:
        return chunk_results[0]

    merged_fields: Dict[str, Any] = {}
    for cr in chunk_results:
        for k, v in (cr.get("parsed_fields") or {}).items():
            if isinstance(v, list):
                merged_fields.setdefault(k, [])
                merged_fields[k].extend(item for item in v if item not in merged_fields[k])
            elif k not in merged_fields or merged_fields[k] in (None, "", []):
                if v not in (None, "", []):
                    merged_fields[k] = v
                elif k not in merged_fields:
                    merged_fields[k] = v

    genuine_flags = [cr.get("document_appears_genuine") for cr in chunk_results]
    if any(g is False for g in genuine_flags):
        genuine = False
    elif all(g is True for g in genuine_flags):
        genuine = True
    else:
        genuine = None

    notes = [cr.get("quality_notes", "") for cr in chunk_results if cr.get("quality_notes")]

    return {
        "raw_text": "\n\n".join(cr.get("raw_text", "") for cr in chunk_results if cr.get("raw_text")),
        "parsed_fields": merged_fields,
        "extraction_confidence": min((cr.get("extraction_confidence", 0) for cr in chunk_results), default=0),
        "document_appears_genuine": genuine,
        "quality_notes": " / ".join(dict.fromkeys(notes)),
    }


async def extract_document_data(
    image_data: Union[bytes, List[bytes]], filename: str, document_type: str
) -> Dict[str, Any]:
    """
    OCR + structured extraction for a document. Accepts either a single
    image or a list of images (multi-page forms, e.g. the claim form, up to
    8 pages -- see routes.py's page-count check).

    The vision model behind this (qwen2.5-vl-7b, via the XeAI Gateway) caps
    at 2 images per request, unlike the old dedicated Ollama vision model
    this used to call -- a document with more pages than that is split into
    batches of 2, extracted separately, and merged (see _merge_extractions)
    rather than silently truncated to its first 2 pages.

    Never raises -- returns extraction_confidence=0 and an "error" key on
    failure, so a flaky gateway call degrades to "document stored with no
    extracted data" rather than blocking the claim submission it's
    attached to.
    """
    document_type = document_type if document_type in _PROMPTS else "other"
    prompt = _PROMPTS[document_type]
    images = image_data if isinstance(image_data, list) else [image_data]
    batches = [images[i:i + _GATEWAY_MAX_IMAGES_PER_CALL] for i in range(0, len(images), _GATEWAY_MAX_IMAGES_PER_CALL)] or [[]]

    try:
        from ollama_client import generate

        # The claim-form schema has ~30 fields plus nested arrays -- the
        # client's default 1024-token cap truncates it mid-JSON-string
        # (confirmed live: json.loads raised "Unterminated string" on a
        # real extraction attempt), so it gets a much larger budget than
        # the other, smaller document schemas need.
        num_predict = 4096 if document_type == "claim_form" else None

        chunk_results = []
        for batch in batches:
            response_text = await asyncio.to_thread(
                generate, prompt, model=OCR_VISION_MODEL, images=batch,
                json_mode=True, timeout=120, num_predict=num_predict,
            )
            parsed = json.loads(_strip_code_fence(response_text))
            chunk_results.append({
                "raw_text": parsed.get("raw_text", "") or "",
                "parsed_fields": parsed.get("parsed_fields", {}) or {},
                "extraction_confidence": parsed.get("extraction_confidence", 0),
                "document_appears_genuine": parsed.get("document_appears_genuine"),
                "quality_notes": parsed.get("quality_notes", ""),
            })

        merged = _merge_extractions(chunk_results)
        raw_text = merged["raw_text"]
        parsed_fields = merged["parsed_fields"]
        confidence = merged["extraction_confidence"]
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
            "document_appears_genuine": merged["document_appears_genuine"],
            "quality_notes": merged["quality_notes"],
            "extraction_method": "gateway_vision",
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
