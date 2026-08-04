"""
Builds a LoRA fine-tuning dataset for the AI Advisory / Narrative Intelligence
task from the synthetic Old Mutual Kenya claims data, following the Blueprint's
Section 5.8 / Appendix B.8 output schema.

Deliberately NOT trained on: raw narrative text as the primary learning signal.
Across all 50,000 claims there are only 623 unique narrative sentences (some
scenarios, e.g. NARRATIVE_IMAGE_INCONSISTENCY, have just 2), so fine-tuning on
narrative text alone would teach template memorization, not language
understanding -- and could bias the model against real Old Mutual narratives
later, which will look nothing like these short synthetic sentences.

What this dataset DOES teach: given the full multi-source structured claim
context (policy, vehicle, driver, business rule outcomes, repair estimate,
image/document evidence summary, historical claims, narrative text as one
signal among several), produce a correctly-formatted AI Advisory JSON object
matching the Blueprint's schema and reasoning pattern across all 21 scenario
"shapes" in the synthetic dataset. ScenarioType itself is a synthetic-data
label (how we generated the demo), not a real-world field, so it is excluded
from both input and target -- the model must infer everything from the same
claim facts a real deployment would have.

Phase 1 (this script, synthetic data): teaches output schema/format discipline
and multi-source reasoning patterns.
Phase 2 (later, real Old Mutual data): continue/replace this fine-tune with
real claimant narratives paired with real adjuster decisions, once available
-- that's where genuine narrative-understanding gets learned, since only real
data has the language diversity for it.

Output: nlp/data/claims_finetune_{train,valid,test}.jsonl
    Each line: {"messages": [{"role": "system", ...}, {"role": "user", ...},
                              {"role": "assistant", ...}]}

Usage:
    venv/Scripts/python.exe nlp/scripts/build_finetune_dataset.py
"""

import json
import random
from collections import defaultdict
from pathlib import Path

import openpyxl

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
EXCEL_PATH = REPO_ROOT / "Old_Mutual_Kenya_Motor_Claims_Synthetic_Data_1.xlsx"
OUT_DIR = REPO_ROOT / "nlp" / "data"
PARAPHRASES_PATH = OUT_DIR / "narrative_paraphrases.json"

RNG_SEED = 42
SPLIT = {"train": 0.90, "valid": 0.05, "test": 0.05}

# If narrative_paraphrases.json exists (produced by paraphrase_narratives.py
# on a box with live Ollama access), each claim's narrative is replaced with a
# randomly chosen paraphrase of its original template instead of the literal
# template text -- reduces the 623-template memorization problem without
# touching the target labels (which were written against the original
# template's facts, and every paraphrase is fact-preserving by construction).
# Falls back to the original template if the file's missing, so this script
# still runs standalone with no dependency on Ollama being reachable.
def load_paraphrases():
    if not PARAPHRASES_PATH.exists():
        print(f"No {PARAPHRASES_PATH.name} found -- using original narrative templates as-is "
              f"(run paraphrase_narratives.py on a box with Ollama access to enable this).")
        return {}
    data = json.loads(PARAPHRASES_PATH.read_text(encoding="utf-8"))
    print(f"Loaded paraphrases for {len(data)} templates from {PARAPHRASES_PATH.name}")
    return data

SYSTEM_PROMPT = (
    "You are the Xenova AI Advisory capability for Old Mutual Kenya motor claims. "
    "Given structured claim context (policy, vehicle, driver, business rule outcomes, "
    "repair estimate, evidence summary, historical claims, and claimant narrative), "
    "produce a single JSON object with exactly these fields: "
    "narrative_intelligence_observation, computer_vision_observation, "
    "physics_math_consistency_observation, cross_validation_risk_observation, "
    "early_risk_indicator, confidence (0.0-1.0), recommended_action, explanation. "
    "Use \"None\" (string) for any field with no observation to report. "
    "Your output is advisory only -- a human claims handler makes the final decision. "
    "Respond with ONLY the JSON object, no other text."
)


def load_sheet_rows(wb, sheet_name):
    ws = wb[sheet_name]
    it = iter(ws.iter_rows(values_only=True))
    header = next(it)
    return header, list(it)


def index_by(header, rows, key_col):
    key_i = header.index(key_col)
    return {row[key_i]: row for row in rows}


def group_by(header, rows, key_col):
    key_i = header.index(key_col)
    out = defaultdict(list)
    for row in rows:
        out[row[key_i]].append(row)
    return out


def row_dict(header, row):
    return dict(zip(header, row))


def fmt_date(v):
    if v is None:
        return "N/A"
    return str(v)[:10] if hasattr(v, "isoformat") or isinstance(v, str) else str(v)


def build_input_text(ctx):
    claim = ctx["claim"]
    policy = ctx["policy"]
    fnol = ctx["fnol"]
    vehicle = ctx["vehicle"]
    driver = ctx["driver"]
    narrative = ctx["narrative"]
    rule_outcomes = ctx["rule_outcomes"]
    repair = ctx["repair"]
    images = ctx["images"]
    documents = ctx["documents"]
    historical = ctx["historical"]

    lines = []
    lines.append(f"CLAIM {claim['ClaimID']}")
    lines.append(
        f"Policy: {policy['Product']} | Status={policy['PolicyStatus']} | "
        f"Effective={fmt_date(policy['EffectiveDate'])} | Expiry={fmt_date(policy['ExpiryDate'])} | "
        f"Cover={policy['CoverType']} | SumInsured={policy['SumInsured']} | "
        f"Excess={policy['Excess']} | ExcludedPerils={policy['ExcludedPerils']}"
    )
    lines.append(
        f"Incident: {fmt_date(fnol['IncidentDateTime'])} at {fnol['IncidentLocation']} | "
        f"LossType={fnol['LossType']} | Channel={fnol['SubmissionChannel']} | "
        f"Reported={fmt_date(fnol['SubmissionTimestamp'])}"
    )
    lines.append(
        f"Vehicle: {vehicle['Year']} {vehicle['Make']} {vehicle['Model']} | "
        f"Usage={vehicle['VehicleUsage']} | InsuredValue={vehicle['InsuredValue']}"
    )
    lines.append(
        f"Driver: {driver['RelationshipToPolicyholder']} | Age={driver['DriverAge']} | "
        f"LicenceClass={driver['LicenceClass']} | YearsLicensed={driver['YearsLicensed']}"
    )
    lines.append(f"Claimant narrative: \"{narrative}\"")

    if rule_outcomes:
        rules_str = "; ".join(f"{r['RuleName']} -> {r['RuleOutcome']} ({r['Severity']})" for r in rule_outcomes)
        lines.append(f"Business rule outcomes: {rules_str}")
    else:
        lines.append("Business rule outcomes: none triggered")

    if repair:
        lines.append(
            f"Repair estimate: cost={repair['EstimatedRepairCost']} | "
            f"severity={repair['EstimatedDamageSeverity']} | days={repair['EstimatedRepairDays']}"
        )

    if images:
        by_cat = defaultdict(int)
        n_dup = n_manip = 0
        for img in images:
            by_cat[img["ImageCategory"]] += 1
            if img["FlaggedDuplicate"]:
                n_dup += 1
            if img["FlaggedPotentialManipulation"]:
                n_manip += 1
        cat_str = ", ".join(f"{k}={v}" for k, v in sorted(by_cat.items()))
        lines.append(
            f"Evidence images ({len(images)} total): {cat_str} | "
            f"flagged_duplicate={n_dup} | flagged_manipulation={n_manip}"
        )
    else:
        lines.append("Evidence images: none submitted")

    if documents:
        avg_ocr = sum(d["OCRConfidence"] or 0 for d in documents) / len(documents)
        lines.append(f"Documents: {len(documents)} submitted, avg OCR confidence={avg_ocr:.2f}")

    if historical:
        n_fraud = sum(1 for h in historical if h["ConfirmedFraud"] == "Yes")
        lines.append(
            f"Historical claims for this customer/policy: {len(historical)} prior claims, "
            f"{n_fraud} with confirmed fraud"
        )
    else:
        lines.append("Historical claims: none on record")

    return "\n".join(lines)


def build_target_json(advisory_row):
    a = advisory_row
    return {
        "narrative_intelligence_observation": a["NarrativeIntelligenceObservation"],
        "computer_vision_observation": a["ComputerVisionObservation"],
        "physics_math_consistency_observation": a["PhysicsMathConsistencyObservation"],
        "cross_validation_risk_observation": a["CrossValidationRiskObservation"],
        "early_risk_indicator": a["EarlyRiskIndicator"],
        "confidence": a["AIAdvisoryConfidence"],
        "recommended_action": a["AIAdvisoryRecommendedAction"],
        "explanation": a["AIAdvisoryExplanation"],
    }


def main():
    paraphrases = load_paraphrases()
    rng_narrative = random.Random(RNG_SEED + 1)  # separate stream from the split shuffler

    print("Loading workbook...")
    wb = openpyxl.load_workbook(EXCEL_PATH, read_only=True, data_only=True)

    claim_h, claim_rows = load_sheet_rows(wb, "Claim")
    policy_h, policy_rows = load_sheet_rows(wb, "Policy")
    fnol_h, fnol_rows = load_sheet_rows(wb, "FNOL")
    vehicle_h, vehicle_rows = load_sheet_rows(wb, "Vehicle")
    driver_h, driver_rows = load_sheet_rows(wb, "Driver")
    narrative_h, narrative_rows = load_sheet_rows(wb, "ClaimNarrative")
    rule_out_h, rule_out_rows = load_sheet_rows(wb, "BusinessRuleOutcomes")
    repair_h, repair_rows = load_sheet_rows(wb, "RepairEstimate")
    images_h, images_rows = load_sheet_rows(wb, "Images")
    documents_h, documents_rows = load_sheet_rows(wb, "Documents")
    historical_h, historical_rows = load_sheet_rows(wb, "HistoricalClaims")
    advisory_h, advisory_rows = load_sheet_rows(wb, "AIAdvisory")

    print("Indexing sheets...")
    policy_by_num = index_by(policy_h, policy_rows, "PolicyNumber")
    fnol_by_claimref = {}
    fnol_by_fnolid = index_by(fnol_h, fnol_rows, "FNOLID")
    vehicle_by_reg = index_by(vehicle_h, vehicle_rows, "RegistrationNumber")
    driver_by_id = index_by(driver_h, driver_rows, "DriverID")
    narrative_by_claim = index_by(narrative_h, narrative_rows, "ClaimID")
    rule_out_by_claim = group_by(rule_out_h, rule_out_rows, "ClaimID")
    repair_by_claim = index_by(repair_h, repair_rows, "ClaimID")
    images_by_claim = group_by(images_h, images_rows, "ClaimID")
    documents_by_claim = group_by(documents_h, documents_rows, "ClaimID")
    historical_by_policy = group_by(historical_h, historical_rows, "PolicyNumber")
    advisory_by_claim = index_by(advisory_h, advisory_rows, "ClaimID")

    fnol_idx = fnol_h.index("FNOLID")
    claim_fnolid_i = claim_h.index("FNOLID")
    claim_id_i = claim_h.index("ClaimID")
    claim_policynum_i = claim_h.index("PolicyNumber")
    fnol_vehreg_i = fnol_h.index("VehicleRegistration")
    fnol_driverid_i = fnol_h.index("DriverID")
    scenario_i = claim_h.index("ScenarioType")

    print("Building examples...")
    examples = []
    skipped = 0
    for claim_row in claim_rows:
        claim = row_dict(claim_h, claim_row)
        claim_id = claim_row[claim_id_i]

        fnol_row = fnol_by_fnolid.get(claim_row[claim_fnolid_i])
        advisory_row_raw = advisory_by_claim.get(claim_id)
        if fnol_row is None or advisory_row_raw is None:
            skipped += 1
            continue
        fnol = row_dict(fnol_h, fnol_row)

        policy_row = policy_by_num.get(claim_row[claim_policynum_i])
        vehicle_row = vehicle_by_reg.get(fnol_row[fnol_vehreg_i])
        driver_row = driver_by_id.get(fnol_row[fnol_driverid_i])
        if policy_row is None or vehicle_row is None or driver_row is None:
            skipped += 1
            continue

        original_narrative = (row_dict(narrative_h, narrative_by_claim[claim_id])["NarrativeText"]
                              if claim_id in narrative_by_claim else "No narrative provided.")
        # 50/50 original-vs-paraphrase mix: keeps some literal template
        # coverage (in case exact wording matters for some downstream check)
        # while still diluting the memorization risk from 623 fixed sentences.
        variants = paraphrases.get(original_narrative)
        if variants and rng_narrative.random() < 0.5:
            narrative_text = rng_narrative.choice(variants)
        else:
            narrative_text = original_narrative

        ctx = {
            "claim": claim,
            "policy": row_dict(policy_h, policy_row),
            "fnol": fnol,
            "vehicle": row_dict(vehicle_h, vehicle_row),
            "driver": row_dict(driver_h, driver_row),
            "narrative": narrative_text,
            "rule_outcomes": [row_dict(rule_out_h, r) for r in rule_out_by_claim.get(claim_id, [])],
            "repair": row_dict(repair_h, repair_by_claim[claim_id]) if claim_id in repair_by_claim else None,
            "images": [row_dict(images_h, r) for r in images_by_claim.get(claim_id, [])],
            "documents": [row_dict(documents_h, r) for r in documents_by_claim.get(claim_id, [])],
            "historical": [row_dict(historical_h, r) for r in historical_by_policy.get(claim_row[claim_policynum_i], [])],
        }

        input_text = build_input_text(ctx)
        target = build_target_json(row_dict(advisory_h, advisory_row_raw))

        examples.append({
            "claim_id": claim_id,
            "scenario_type": claim_row[scenario_i],  # kept for stratified split only, stripped before writing
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": input_text},
                {"role": "assistant", "content": json.dumps(target, ensure_ascii=False)},
            ],
        })

    print(f"Built {len(examples)} examples, skipped {skipped} (missing joins)")

    print("Stratified split by scenario type...")
    rng = random.Random(RNG_SEED)
    by_scenario = defaultdict(list)
    for ex in examples:
        by_scenario[ex["scenario_type"]].append(ex)

    splits = {"train": [], "valid": [], "test": []}
    for scenario, exs in by_scenario.items():
        rng.shuffle(exs)
        n = len(exs)
        n_valid = max(1, int(n * SPLIT["valid"]))
        n_test = max(1, int(n * SPLIT["test"]))
        splits["valid"].extend(exs[:n_valid])
        splits["test"].extend(exs[n_valid:n_valid + n_test])
        splits["train"].extend(exs[n_valid + n_test:])

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for split_name, exs in splits.items():
        rng.shuffle(exs)
        out_path = OUT_DIR / f"claims_finetune_{split_name}.jsonl"
        with out_path.open("w", encoding="utf-8") as f:
            for ex in exs:
                f.write(json.dumps({"messages": ex["messages"]}, ensure_ascii=False) + "\n")
        print(f"  {split_name}: {len(exs)} examples -> {out_path}")


if __name__ == "__main__":
    main()
