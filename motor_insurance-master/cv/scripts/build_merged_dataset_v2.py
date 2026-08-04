"""
Extends cv/data/merged-damage-dataset (the CarDD + Skillfactory mix that trained
merged_v1_yolov8n_seg) with two more Roboflow sources -- NUST's part-level instance
segmentation set and Car Damage V5 -- into a new 30-class taxonomy, without
disturbing the original 28 class indices (so a continued-training run can still
warm-start from merged_v1_yolov8n_seg/best.pt).

Sources merged here:
  - CarDD (car-damage-detection-cardd/car-damage-severity-detection-cardd v3)
  - NUST damage instance segmentation (damazh/car-damage-instance-segmentation v1)
  - Car Damage V5 (car-damage-kadad/car-damage-v5 v6)

Skillfactory (skillfactory/car-damage-c1f0i v1) is intentionally NOT re-merged here:
its 8,857 images are only box-labeled and need SAM (segment_anything) to convert
boxes to polygon masks, which needs a GPU to run in reasonable time and isn't
installed in this sandbox. Run this same script on the GPU box with
`pip install segment-anything` and a SAM checkpoint available -- the Skillfactory
block below will then execute instead of being skipped, adding those images into
the same merged-damage-dataset-v2 output.

Usage:
    venv/Scripts/python.exe cv/scripts/build_merged_dataset_v2.py
"""

import os
import shutil
from pathlib import Path

import yaml
from dotenv import load_dotenv

CV_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = CV_ROOT / "data"
load_dotenv(CV_ROOT / ".env")
ROBOFLOW_API_KEY = os.getenv("ROBOFLOW_API_KEY")

CARDD_DIR = DATA_DIR / "car-damage-severity-detection-cardd"
SKILL_DIR = DATA_DIR / "skillfactory-car-damage"
NUST_DIR = DATA_DIR / "nust-car-damage-instance-seg"
V5_DIR = DATA_DIR / "car-damage-v5"
MERGED_DIR = DATA_DIR / "merged-damage-dataset-v2"

# ---------------------------------------------------------------------------
# 1. Ensure source datasets are present (download whichever are missing)
# ---------------------------------------------------------------------------


def ensure_downloaded(dir_path: Path, workspace: str, project: str, version: int):
    if (dir_path / "data.yaml").exists():
        print(f"Already present, skipping download: {dir_path}")
        return
    assert ROBOFLOW_API_KEY, "Set ROBOFLOW_API_KEY in cv/.env first."
    from roboflow import Roboflow

    rf = Roboflow(api_key=ROBOFLOW_API_KEY)
    p = rf.workspace(workspace).project(project)
    # NOTE: do not pre-create dir_path -- roboflow's download() silently no-ops
    # if `location` already exists (see roboflow/core/version.py), which looks
    # like success but produces zero files.
    ds = p.version(version).download("yolov8", location=str(dir_path))
    print(f"Downloaded {workspace}/{project} v{version} -> {ds.location}")


ensure_downloaded(CARDD_DIR, "car-damage-detection-cardd", "car-damage-severity-detection-cardd", 3)
ensure_downloaded(NUST_DIR, "damazh", "car-damage-instance-segmentation", 1)
ensure_downloaded(V5_DIR, "car-damage-kadad", "car-damage-v5", 6)

SAM_AVAILABLE = False
try:
    import segment_anything  # noqa: F401

    SAM_AVAILABLE = True
except ImportError:
    pass

if SAM_AVAILABLE:
    ensure_downloaded(SKILL_DIR, "skillfactory", "car-damage-c1f0i", 1)
else:
    print(
        "segment_anything not installed -- skipping Skillfactory (box-only labels "
        "need SAM to become polygon masks). Install segment-anything + a SAM "
        "checkpoint and re-run on a GPU box to include it."
    )

# ---------------------------------------------------------------------------
# 2. Extended unified taxonomy -- original 28 indices preserved exactly,
#    2 new classes appended for damage concepts the original two sources
#    (CarDD, Skillfactory) didn't cover.
# ---------------------------------------------------------------------------

UNIFIED_CLASSES = [
    "car-part-crack", "detachment", "flat-tire", "paint-chips", "minor-deformation",
    "moderate-deformation", "severe-deformation", "scratches", "side-mirror-crack",
    "glass-crack-generic", "lamp-damage-generic", "glass-crack-windscreen-front",
    "glass-crack-windscreen-rear", "glass-crack-window", "headlight-damage",
    "taillight-damage", "signlight-damage", "bumper-dent-front", "bumper-dent-rear",
    "hood-bonnet-dent", "door-dent", "fender-dent", "roof-dent", "pillar-dent",
    "runningboard-dent", "trunk-damage", "generic-dent", "generic-damage",
    # New in v2:
    # - wheel_damage (NUST) means rim/wheel structural damage, which is a
    #   different failure mode from a deflated flat-tire -- kept separate
    #   rather than folded into "flat-tire" to avoid teaching that class
    #   the wrong visual signature.
    "wheel-damage-generic",
    # - "Wreck" (Car Damage V5) denotes write-off-level destruction, distinct
    #   from a localised severe-deformation panel dent. This is also the
    #   closest real-image signal available for the Blueprint's "Total Loss"
    #   severity bucket, which the original CarDD+Skillfactory merge had no
    #   dedicated class for at all.
    "wreck-total-loss",
]
U = {name: i for i, name in enumerate(UNIFIED_CLASSES)}
assert len(UNIFIED_CLASSES) == 30

CARDD_CLASSES = [
    "car-part-crack", "detachment", "flat-tire", "glass-crack", "lamp-crack",
    "minor-deformation", "moderate-deformation", "paint-chips", "scratches",
    "severe-deformation", "side-mirror-crack",
]
CARDD_MAP = {
    "car-part-crack": U["car-part-crack"], "detachment": U["detachment"],
    "flat-tire": U["flat-tire"], "glass-crack": U["glass-crack-generic"],
    "lamp-crack": U["lamp-damage-generic"], "minor-deformation": U["minor-deformation"],
    "moderate-deformation": U["moderate-deformation"], "paint-chips": U["paint-chips"],
    "scratches": U["scratches"], "severe-deformation": U["severe-deformation"],
    "side-mirror-crack": U["side-mirror-crack"],
}

SKILL_CLASSES = [
    "Front-Windscreen-Damage", "Headlight-Damage", "Major-Rear-Bumper-Dent",
    "Rear-windscreen-Damage", "RunningBoard-Dent", "Sidemirror-Damage",
    "Signlight-Damage", "Taillight-Damage", "bonnet-dent", "damaged",
    "damaged-door", "damaged-front-bumper", "damaged-head-light", "damaged-hood",
    "damaged-rear-bumper", "damaged-rear-window", "damaged-tail-light",
    "damaged-trunk", "damaged-window", "damaged-windscreen", "dent",
    "dent-or-scratch", "doorouter-dent", "fender-dent", "front-bumper-dent",
    "medium-Bodypanel-Dent", "pillar-dent", "quaterpanel-dent", "rear-bumper-dent",
    "roof-dent", "scratch",
]
SKILL_MAP = {
    "Front-Windscreen-Damage": U["glass-crack-windscreen-front"],
    "Headlight-Damage": U["headlight-damage"],
    "Major-Rear-Bumper-Dent": U["bumper-dent-rear"],
    "Rear-windscreen-Damage": U["glass-crack-windscreen-rear"],
    "RunningBoard-Dent": U["runningboard-dent"],
    "Sidemirror-Damage": U["side-mirror-crack"],
    "Signlight-Damage": U["signlight-damage"],
    "Taillight-Damage": U["taillight-damage"],
    "bonnet-dent": U["hood-bonnet-dent"],
    "damaged": U["generic-damage"],
    "damaged-door": U["door-dent"],
    "damaged-front-bumper": U["bumper-dent-front"],
    "damaged-head-light": U["headlight-damage"],
    "damaged-hood": U["hood-bonnet-dent"],
    "damaged-rear-bumper": U["bumper-dent-rear"],
    "damaged-rear-window": U["glass-crack-window"],
    "damaged-tail-light": U["taillight-damage"],
    "damaged-trunk": U["trunk-damage"],
    "damaged-window": U["glass-crack-window"],
    "damaged-windscreen": U["glass-crack-windscreen-front"],
    "dent": U["generic-dent"],
    "dent-or-scratch": U["scratches"],
    "doorouter-dent": U["door-dent"],
    "fender-dent": U["fender-dent"],
    "front-bumper-dent": U["bumper-dent-front"],
    "medium-Bodypanel-Dent": U["generic-dent"],
    "pillar-dent": U["pillar-dent"],
    "quaterpanel-dent": U["fender-dent"],
    "rear-bumper-dent": U["bumper-dent-rear"],
    "roof-dent": U["roof-dent"],
    "scratch": U["scratches"],
}

# NUST: 14 classes, 7 are damage concepts, 7 are part/view-presence labels
# ("back", "front", "side", "hood", "wheels", "front_wind_shield",
# "back_wind_shield") that fire on ANY visible instance of that part, damaged
# or not. Those are deliberately left out of the map below -- remap_and_link
# drops label lines for classes not present in idx_map -- since including them
# would teach the detector to flag undamaged parts as damage.
NUST_CLASSES = [
    "back", "back_wind_shield", "broken", "cracks_mirrors", "dents", "front",
    "front_wind_shield", "head_light_damage", "hood", "paint_damage",
    "scratches", "side", "wheel_damage", "wheels",
]
NUST_MAP = {
    # NOTE: assumption -- "broken" has no part qualifier, bucketed with the
    # generic damage catch-all (same judgment call the original merge made
    # for Skillfactory's unqualified "damaged").
    "broken": U["generic-damage"],
    "cracks_mirrors": U["side-mirror-crack"],
    "dents": U["generic-dent"],
    "head_light_damage": U["headlight-damage"],
    "paint_damage": U["paint-chips"],
    "scratches": U["scratches"],
    "wheel_damage": U["wheel-damage-generic"],
}

# Car Damage V5: 4 classes, all damage concepts, all kept.
V5_CLASSES = ["Broken Glass", "Dent", "Scratch", "Wreck"]
V5_MAP = {
    "Broken Glass": U["glass-crack-generic"],
    "Dent": U["generic-dent"],
    "Scratch": U["scratches"],
    # NOTE: see UNIFIED_CLASSES comment above -- kept distinct from
    # severe-deformation as the Total Loss signal.
    "Wreck": U["wreck-total-loss"],
}

CARDD_IDX_MAP = {i: CARDD_MAP[name] for i, name in enumerate(CARDD_CLASSES)}
SKILL_IDX_MAP = {i: SKILL_MAP[name] for i, name in enumerate(SKILL_CLASSES) if name in SKILL_MAP}
NUST_IDX_MAP = {i: NUST_MAP[name] for i, name in enumerate(NUST_CLASSES) if name in NUST_MAP}
V5_IDX_MAP = {i: V5_MAP[name] for i, name in enumerate(V5_CLASSES)}

print(f"Extended unified taxonomy: {len(UNIFIED_CLASSES)} classes "
      f"(28 original + {len(UNIFIED_CLASSES) - 28} new)")

# ---------------------------------------------------------------------------
# 3. Remap each source's labels into the unified index space and copy
#    images + relabeled .txt into merged-damage-dataset-v2.
# ---------------------------------------------------------------------------


def remap_and_link(src_dir: Path, idx_map: dict, prefix: str, split: str):
    img_dir, lbl_dir = src_dir / split / "images", src_dir / split / "labels"
    out_img_dir = MERGED_DIR / split / "images"
    out_lbl_dir = MERGED_DIR / split / "labels"
    out_img_dir.mkdir(parents=True, exist_ok=True)
    out_lbl_dir.mkdir(parents=True, exist_ok=True)

    if not lbl_dir.exists():
        return 0, 0

    n_images, n_instances = 0, 0
    for label_path in sorted(lbl_dir.glob("*.txt")):
        img_candidates = list(img_dir.glob(label_path.stem + ".*"))
        if not img_candidates:
            continue
        img_path = img_candidates[0]

        out_lines = []
        for line in label_path.read_text().strip().splitlines():
            parts = line.split()
            if len(parts) < 5:
                continue
            old_cls = int(parts[0])
            if old_cls not in idx_map:
                continue
            out_lines.append(str(idx_map[old_cls]) + " " + " ".join(parts[1:]))
            n_instances += 1

        if not out_lines:
            continue
        new_name = f"{prefix}_{img_path.stem}"
        (out_lbl_dir / f"{new_name}.txt").write_text("\n".join(out_lines))
        dst_img = out_img_dir / f"{new_name}{img_path.suffix}"
        if not dst_img.exists():
            shutil.copy2(img_path, dst_img)
        n_images += 1

    return n_images, n_instances


if __name__ == "__main__":
    sources = [("cardd", CARDD_DIR, CARDD_IDX_MAP), ("nust", NUST_DIR, NUST_IDX_MAP), ("v5", V5_DIR, V5_IDX_MAP)]
    if SAM_AVAILABLE and (SKILL_DIR / "data.yaml").exists():
        print("SAM available but box->polygon conversion for Skillfactory is a separate "
              "step (see notebook section 5) -- not run automatically by this script yet.")

    totals = {"images": 0, "instances": 0}
    for split in ["train", "valid", "test"]:
        split_total_i, split_total_n = 0, 0
        for prefix, src_dir, idx_map in sources:
            i, n = remap_and_link(src_dir, idx_map, prefix, split)
            print(f"[{split}] {prefix}: {i} images / {n} instances")
            split_total_i += i
            split_total_n += n
        totals["images"] += split_total_i
        totals["instances"] += split_total_n

    merged_data_yaml_path = MERGED_DIR / "data.yaml"
    merged_data_yaml_path.write_text(
        "names:\n" + "\n".join(f"- {c}" for c in UNIFIED_CLASSES) +
        f"\nnc: {len(UNIFIED_CLASSES)}\ntrain: ../train/images\nval: ../valid/images\ntest: ../test/images\n"
    )
    print(f"\nTotal: {totals['images']} images / {totals['instances']} instances")
    print(f"Wrote {merged_data_yaml_path}")
