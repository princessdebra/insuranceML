"""
Bridges the synthetic Old Mutual Kenya claims dataset (Excel) to the real,
annotated image pool in cv/data/merged-damage-dataset-v2, for the "Damage
Close-up" evidence photos referenced by the Images sheet.

Why a manifest and not real per-claim photos: the Images sheet has 37,261
Damage Close-up rows (one for close to every one of the 50,000 synthetic
claims) but the real annotated pool only has ~5,450 images (see
build_merged_dataset_v2.py). There's no way to give each synthetic row its
own distinct real photo without either fabricating images or reusing real
ones -- this script does the latter, honestly: it assigns each Damage
Close-up row a real image whose annotated classes plausibly match the
claim's reported severity, tracks every reuse explicitly (RealImageReused),
and preserves the real image's original train/valid/test split so nothing
leaks across a future model's evaluation split.

Output: cv/data/claims_image_manifest.csv
    ClaimID, ImageID, SyntheticFileName, ScenarioType, DamageSeverity,
    RealImagePath, RealLabelPath, RealImageClasses, RealSplit, RealImageReused

Usage:
    venv/Scripts/python.exe cv/scripts/build_claim_image_manifest.py
"""

import random
from collections import defaultdict
from pathlib import Path

import openpyxl
import yaml

CV_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = CV_ROOT.parent
MERGED_DIR = CV_ROOT / "data" / "merged-damage-dataset-v2"
EXCEL_PATH = REPO_ROOT / "Old_Mutual_Kenya_Motor_Claims_Synthetic_Data_1.xlsx"
OUT_PATH = CV_ROOT / "data" / "claims_image_manifest.csv"

RNG_SEED = 42

# ---------------------------------------------------------------------------
# 1. Severity bucket -> plausible class subset.
#    Total Loss leans on "wreck-total-loss" (Car Damage V5's "Wreck" class,
#    the only real-image signal for this bucket) with severe-deformation/
#    detachment as fallback if that pool runs short. Assignments are
#    judgment calls, documented here rather than left implicit.
# ---------------------------------------------------------------------------

SEVERITY_CLASS_MAP = {
    "Minor": [
        "scratches", "paint-chips", "minor-deformation", "car-part-crack",
        "lamp-damage-generic", "glass-crack-generic",
    ],
    "Moderate": [
        "moderate-deformation", "generic-dent", "flat-tire", "headlight-damage",
        "door-dent", "fender-dent", "bumper-dent-front", "bumper-dent-rear",
    ],
    "Severe": [
        "severe-deformation", "detachment", "generic-damage", "wheel-damage-generic",
        "trunk-damage", "roof-dent",
    ],
    "Total Loss": [
        "wreck-total-loss", "severe-deformation", "detachment",
    ],
}


def load_real_pool():
    """Index every labeled real image by (split, set-of-class-names-present)."""
    data_yaml = yaml.safe_load((MERGED_DIR / "data.yaml").read_text())
    names = data_yaml["names"]

    pool = []  # list of dicts: {split, img_path, lbl_path, classes:set[str]}
    for split in ["train", "valid", "test"]:
        lbl_dir = MERGED_DIR / split / "labels"
        img_dir = MERGED_DIR / split / "images"
        if not lbl_dir.exists():
            continue
        for lbl_path in sorted(lbl_dir.glob("*.txt")):
            img_candidates = list(img_dir.glob(lbl_path.stem + ".*"))
            if not img_candidates:
                continue
            cls_ids = set()
            for line in lbl_path.read_text().strip().splitlines():
                parts = line.split()
                if parts:
                    cls_ids.add(int(parts[0]))
            classes = {names[i] for i in cls_ids}
            pool.append({
                "split": split,
                "img_path": img_candidates[0],
                "lbl_path": lbl_path,
                "classes": classes,
            })
    return pool


def build_severity_indices(pool):
    """For each severity bucket, list of pool entries containing >=1 matching class."""
    idx = defaultdict(list)
    for severity, class_list in SEVERITY_CLASS_MAP.items():
        class_set = set(class_list)
        for entry in pool:
            if entry["classes"] & class_set:
                idx[severity].append(entry)
    return idx


def load_claim_severities():
    """ClaimID -> DamageSeverity, from the Claim sheet."""
    wb = openpyxl.load_workbook(EXCEL_PATH, read_only=True, data_only=True)
    ws = wb["Claim"]
    it = iter(ws.iter_rows(values_only=True))
    header = next(it)
    claim_id_i = header.index("ClaimID")
    severity_i = header.index("DamageSeverity")
    scenario_i = header.index("ScenarioType")
    out = {}
    for row in it:
        out[row[claim_id_i]] = (row[severity_i], row[scenario_i])
    return out


def load_damage_closeup_rows():
    """(ImageID, ClaimID, FileName) for every Images row tagged 'Damage Close-up'."""
    wb = openpyxl.load_workbook(EXCEL_PATH, read_only=True, data_only=True)
    ws = wb["Images"]
    it = iter(ws.iter_rows(values_only=True))
    header = next(it)
    img_id_i = header.index("ImageID")
    claim_id_i = header.index("ClaimID")
    fname_i = header.index("FileName")
    cat_i = header.index("ImageCategory")
    rows = []
    for row in it:
        if row[cat_i] == "Damage Close-up":
            rows.append((row[img_id_i], row[claim_id_i], row[fname_i]))
    return rows


def main():
    print("Loading real annotated image pool...")
    pool = load_real_pool()
    print(f"  {len(pool)} labeled real images available")

    severity_idx = build_severity_indices(pool)
    for sev, entries in severity_idx.items():
        print(f"  {sev}: {len(entries)} candidate real images")

    print("Loading synthetic claim severities...")
    claim_severity = load_claim_severities()

    print("Loading Damage Close-up rows from Images sheet...")
    damage_rows = load_damage_closeup_rows()
    print(f"  {len(damage_rows)} Damage Close-up rows to map")

    rng = random.Random(RNG_SEED)
    used_counts = defaultdict(int)  # id(entry) -> times assigned

    lines = ["ClaimID,ImageID,SyntheticFileName,ScenarioType,DamageSeverity,"
             "RealImagePath,RealLabelPath,RealImageClasses,RealSplit,RealImageReused"]

    skipped_no_severity = 0
    skipped_no_pool = 0

    for image_id, claim_id, fname in damage_rows:
        severity_info = claim_severity.get(claim_id)
        if severity_info is None:
            skipped_no_severity += 1
            continue
        severity, scenario = severity_info

        candidates = severity_idx.get(severity, [])
        if not candidates:
            skipped_no_pool += 1
            continue

        entry = rng.choice(candidates)
        key = str(entry["img_path"])
        used_counts[key] += 1
        reused = "TRUE" if used_counts[key] > 1 else "FALSE"

        rel_img = entry["img_path"].relative_to(REPO_ROOT)
        rel_lbl = entry["lbl_path"].relative_to(REPO_ROOT)
        classes_str = "|".join(sorted(entry["classes"]))

        lines.append(
            f'{claim_id},{image_id},{fname},{scenario},{severity},'
            f'{rel_img},{rel_lbl},{classes_str},{entry["split"]},{reused}'
        )

    OUT_PATH.write_text("\n".join(lines), encoding="utf-8")

    total_assigned = len(lines) - 1
    unique_real_images_used = len(used_counts)
    avg_reuse = total_assigned / unique_real_images_used if unique_real_images_used else 0

    print(f"\nWrote {OUT_PATH}")
    print(f"  Assigned: {total_assigned}")
    print(f"  Skipped (no matching claim severity): {skipped_no_severity}")
    print(f"  Skipped (no real images for that severity): {skipped_no_pool}")
    print(f"  Unique real images used: {unique_real_images_used}")
    print(f"  Average reuse per real image: {avg_reuse:.1f}x")


if __name__ == "__main__":
    main()
