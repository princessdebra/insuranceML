"""
Zoomed-in, specific vehicle-part identification for damage detections whose
YOLO class doesn't encode a part.

The trained damage detector (damage_detector.py) classifies DAMAGE TYPE
(dent, crack, scratch...), and several of its classes are genuinely
part-agnostic by design ("generic-damage", "moderate-deformation") --
that's a different problem than part SEGMENTATION (which part of the car
this is), which the model was never trained to do. Rather than train a
second model, this crops the photo down to just the detected region,
upscales it (a literal "zoom in" on a small/distant region of a wide scene
photo), and asks the vision LLM already running in this pipeline
(gemma4:26b via Ollama) a narrow, focused question: name the part. A
close-up crop is a much easier ask for a vision model than "find and
classify damage across an entire wide accident scene," which is why this
tends to succeed even on photos the whole-image analysis handled poorly.

Advisory only, like everything else here -- if this fails or times out,
callers keep the generic component label rather than blocking the claim.
"""

import asyncio
import io
import json
import logging
import os
from typing import Any, Dict, List, Optional

from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

VISION_MODEL = os.environ.get("OLLAMA_VISION_MODEL", "gemma4:26b")
CROP_PADDING_RATIO = 0.20  # extra margin around the bbox so the part's edges/context are visible
MIN_ZOOM_DIMENSION = 512   # upscale small crops so the model gets a genuine close-up, not a thumbnail

# Free-form "give me x1,y1,x2,y2 as fractions 0-1" coordinate regression is a
# known weak spot for vision-LLMs -- a live claim showed a "Roof" damage zone
# placed on the ground, nowhere near either vehicle, despite the model's part
# and damage-type judgment itself being accurate. Picking one labeled cell out
# of a small overlaid grid is a much easier, far better-grounded task for these
# models than regressing raw floats, so the whole-photo scans below draw a
# lettered/numbered grid onto the image before sending it, and ask the model to
# name a cell (e.g. "C2") instead of trusting its own coordinate guess.
_GRID_COLS = 6
_GRID_ROWS = 4


def _add_grid_overlay(image_data: bytes) -> bytes:
    """Returns a copy of the image with a labeled grid drawn on top, for
    grounding the model's spatial answers -- never used for anything shown
    to a human, only as the model's input for zone-finding calls."""
    image = Image.open(io.BytesIO(image_data)).convert("RGB")
    draw = ImageDraw.Draw(image, "RGBA")
    w, h = image.size
    cell_w, cell_h = w / _GRID_COLS, h / _GRID_ROWS
    line_width = max(1, round(min(w, h) * 0.0025))
    for c in range(1, _GRID_COLS):
        x = c * cell_w
        draw.line([(x, 0), (x, h)], fill=(255, 255, 0, 160), width=line_width)
    for r in range(1, _GRID_ROWS):
        y = r * cell_h
        draw.line([(0, y), (w, y)], fill=(255, 255, 0, 160), width=line_width)

    font_size = max(16, round(min(cell_w, cell_h) * 0.20))
    try:
        font = ImageFont.truetype("DejaVuSans-Bold.ttf", font_size)
    except Exception:
        font = ImageFont.load_default()
    for r in range(_GRID_ROWS):
        for c in range(_GRID_COLS):
            label = f"{chr(65 + c)}{r + 1}"
            x, y = c * cell_w + 4, r * cell_h + 2
            text_w = font_size * len(label) * 0.62
            draw.rectangle([x - 2, y - 2, x + text_w, y + font_size + 4], fill=(0, 0, 0, 150))
            draw.text((x, y), label, fill=(255, 255, 0, 255), font=font)

    buf = io.BytesIO()
    image.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


def _grid_cell_bbox(cell: Optional[str]) -> Optional[List[float]]:
    """Converts a reported grid cell name (e.g. "C2") back into a
    normalized bbox covering that cell. Returns None for anything
    unparseable so callers can fall back to the raw coordinate answer."""
    if not cell or not isinstance(cell, str) or len(cell) < 2:
        return None
    col_letter, row_part = cell[0].upper(), cell[1:]
    if not row_part.isdigit():
        return None
    col_idx = ord(col_letter) - ord("A")
    row_idx = int(row_part) - 1
    if not (0 <= col_idx < _GRID_COLS and 0 <= row_idx < _GRID_ROWS):
        return None
    x1, x2 = col_idx / _GRID_COLS, (col_idx + 1) / _GRID_COLS
    y1, y2 = row_idx / _GRID_ROWS, (row_idx + 1) / _GRID_ROWS
    return [round(x1, 4), round(y1, 4), round(x2, 4), round(y2, 4)]

_VALID_PARTS = [
    "Front Bumper", "Rear Bumper", "Bonnet/Hood", "Boot/Trunk", "Roof",
    "Front Left Fender", "Front Right Fender", "Rear Left Fender", "Rear Right Fender",
    "Front Left Door", "Front Right Door", "Rear Left Door", "Rear Right Door",
    "Left Front Wing Mirror", "Right Front Wing Mirror",
    "Windscreen", "Rear Windscreen", "Left Window", "Right Window",
    "Left Headlamp", "Right Headlamp", "Left Taillamp", "Right Taillamp",
    "Front Grille", "Left Pillar", "Right Pillar", "Left Running Board", "Right Running Board",
    "Front Left Wheel", "Front Right Wheel", "Rear Left Wheel", "Rear Right Wheel",
]

_PROMPT = f"""This is a zoomed-in crop of a damaged area on a car, taken from a wider accident photo.

Identify the SPECIFIC vehicle part shown. Choose the closest match from this list:
{", ".join(_VALID_PARTS)}

If you genuinely cannot tell (the crop is too blurry, dark, or shows no identifiable part), say so honestly rather than guessing.

Also briefly describe the visible damage in one short phrase (e.g. "crumpled and torn metal", "deep dent with paint loss", "spider-crack across the surface").

Respond with ONLY this JSON, no other text:
{{"part": "<part name from the list, or null if you can't tell>", "damage_description": "<short phrase>", "confident": true/false}}"""


def crop_and_zoom(image_data: bytes, bbox: List[float]) -> bytes:
    """Crops to the detection's bbox (with padding) and upscales small
    crops so the vision model gets a real close-up instead of a tiny
    region of a wide scene photo."""
    image = Image.open(io.BytesIO(image_data)).convert("RGB")
    x1, y1, x2, y2 = bbox
    w, h = x2 - x1, y2 - y1
    pad_x, pad_y = w * CROP_PADDING_RATIO, h * CROP_PADDING_RATIO
    left = max(0, x1 - pad_x)
    top = max(0, y1 - pad_y)
    right = min(image.width, x2 + pad_x)
    bottom = min(image.height, y2 + pad_y)
    crop = image.crop((left, top, right, bottom))

    if crop.width > 0 and crop.height > 0:
        scale = max(1.0, MIN_ZOOM_DIMENSION / min(crop.width, crop.height))
        if scale > 1.0:
            crop = crop.resize((int(crop.width * scale), int(crop.height * scale)), Image.LANCZOS)

    buf = io.BytesIO()
    crop.save(buf, format="JPEG", quality=92)
    return buf.getvalue()


_VERIFY_PROMPT = f"""This is a zoomed-in crop from a wider vehicle accident photo, at a location the damage detector flagged as POSSIBLE damage but was not confident enough about to report on its own (below its normal confidence threshold) -- common on wide scene photos where the vehicle is small in frame.

Look closely and decide: is there ACTUAL visible vehicle damage in this crop (a dent, crack, crumple, scratch, or similar), or is this just an undamaged part of the vehicle / not a vehicle at all / too unclear to tell?

If real damage is visible, identify the SPECIFIC part from this list:
{", ".join(_VALID_PARTS)}

Respond with ONLY this JSON, no other text:
{{"damage_visible": true/false, "part": "<part name from the list, or null>", "damage_description": "<short phrase, or null>"}}"""


async def verify_and_identify_low_confidence_region(image_data: bytes, bbox: List[float]) -> Optional[Dict[str, Any]]:
    """For a region the detector considered but didn't clear its normal
    confidence threshold on (see service.py's fallback when a photo comes
    back with zero detections) -- crops+zooms in and asks the vision model
    to confirm whether real damage is actually visible there before trusting
    it, rather than just lowering the detector's threshold globally (which
    would flood ordinary photos with false positives). Returns None unless
    the model affirmatively confirms damage."""
    if not bbox or len(bbox) != 4:
        return None
    try:
        from ollama_client import generate

        crop_bytes = crop_and_zoom(image_data, bbox)
        response_text = await asyncio.to_thread(
            generate, _VERIFY_PROMPT, model=VISION_MODEL, images=[crop_bytes], json_mode=True, timeout=60,
        )
        result = json.loads(response_text)
        if not result.get("damage_visible"):
            return None
        part = result.get("part")
        if not part or part not in _VALID_PARTS:
            return None
        return {"part": part, "damage_description": result.get("damage_description", "")}
    except Exception as e:
        logger.warning(f"Low-confidence region verification failed for bbox {bbox}: {e}")
        return None


# Damage-type vocabulary for the whole-photo scan below (not the YOLO
# taxonomy -- this is the vision model's own free judgment of damage
# category per zone it finds, kept intentionally small and generic).
# Damage types that mean "replace" regardless of how severe the model
# rated it -- a crack or a shattered part isn't safely repairable even
# when "minor".
_REPLACE_DAMAGE_TYPES = {"crack", "cracked", "broken", "shattered", "detached", "torn", "missing"}


def _action_for_damage_type(damage_type: str, severity: str = "moderate") -> str:
    """
    A real bug a live test caught: this previously judged only the damage-
    type keyword and ignored severity entirely, so a bonnet the model
    itself rated "severe crumpling" still came back "repair" (crumpling
    alone doesn't match the replace-keyword list) -- directly contradicting
    the same part's other (correct) "replace" finding from the YOLO-
    detection path, which does treat severe deformation as replace.
    Severity now overrides the type-keyword default: anything the model
    itself calls "severe" is treated as replace, matching that same logic.
    """
    dt = (damage_type or "").lower()
    if any(kw in dt for kw in _REPLACE_DAMAGE_TYPES):
        return "replace"
    if (severity or "").lower() == "severe":
        return "replace"
    return "repair"


# A "describe whatever you notice" prompt tends to under-report smaller or
# less-central parts (a live test missed both headlights entirely on a
# clearly front-damaged vehicle). Explicitly walking the model through a
# checklist of commonly-damaged parts -- asking it to actively rule each
# one in or out, rather than only report what spontaneously stands out --
# is a known way to improve recall on exactly this kind of miss.
_CHECKLIST_PARTS = [
    "Front Bumper", "Rear Bumper", "Bonnet/Hood", "Boot/Trunk", "Roof",
    "Left Headlamp", "Right Headlamp", "Left Taillamp", "Right Taillamp",
    "Front Grille", "Windscreen", "Rear Windscreen",
    "Front Left Fender", "Front Right Fender", "Rear Left Fender", "Rear Right Fender",
    "Front Left Door", "Front Right Door", "Rear Left Door", "Rear Right Door",
    "Left Front Wing Mirror", "Right Front Wing Mirror",
]

_SCAN_PROMPT = f"""You are inspecting a vehicle accident photo for an insurance claim. Systematically check the ENTIRE photo for damage -- do not just report the single most obvious area.

Go through this checklist and actively decide, for EACH part, whether it shows visible damage or not. Small parts (headlamps, mirrors, grille) are easy to miss when only reporting what stands out first -- check them as deliberately as the large, obvious damage:
{", ".join(_CHECKLIST_PARTS)}

IMPORTANT: damage is not only visible cracking, denting, or scratching on a part that's still there. A part can also be damaged by being COMPLETELY MISSING, torn off, or knocked out -- e.g. a headlamp socket that's empty or dark where a headlamp should be, a bumper section that's torn away entirely, a mirror that's gone. If you expect a part to be present at its normal location and instead see an empty gap, a dark hollow socket, exposed wiring, or nothing at all where it should be, that IS damage -- report it with damage_type "missing" or "detached", not just damage you can see marks on.

If more than one vehicle appears in the photo, identify which vehicle each piece of damage belongs to -- e.g. "red sedan", "silver SUV", "the car on the left". This matters: a claims admin reviewing this list needs to know which vehicle each finding is about, not just the part name.

For each part that DOES show visible damage (including a part that's simply not there anymore), report:
- part: the specific vehicle part, chosen from this list: {", ".join(_VALID_PARTS)}
- vehicle: a short description of which vehicle this damage is on (e.g. "red sedan", "silver SUV") -- omit or use null only if there is genuinely just one vehicle in the photo
- damage_type: one or two words (e.g. "dent", "crack", "scratch", "crumpled", "shattered", "torn", "missing", "detached")
- severity: "minor", "moderate", or "severe"
- confidence: your own confidence 0-100 that this damage is really there and correctly located
- description: one short factual sentence
- bbox_normalized: approximate bounding box of this damage as [x1, y1, x2, y2], each a fraction from 0.0 to 1.0 of the image width/height (0,0 = top-left corner, 1,1 = bottom-right corner)

Only report damage you can actually see -- do not invent damage on a part just because it's on the checklist. If the vehicle(s) shown are small/distant in the photo, still do your best to identify what's visible; report fewer, lower-confidence zones rather than fabricating detail you can't see.

This image has a semi-transparent yellow grid overlaid on it, with each cell labeled in its top-left corner (columns A-F left to right, rows 1-4 top to bottom -- e.g. the cell "C2" is the 3rd column, 2nd row). For each damaged part, look at where it actually sits on the grid and report grid_cell as the single cell letter+number containing the CENTER of that damage -- this is more important to get right than the bbox_normalized estimate, so use the grid lines to double-check yourself rather than guessing coordinates freehand.

Respond with ONLY a JSON array (no other text), one object per damaged part, most-obvious-damage first, maximum 10 zones:
[{{"part": "...", "vehicle": "...", "damage_type": "...", "severity": "...", "confidence": 0-100, "description": "...", "grid_cell": "C2", "bbox_normalized": [0.0, 0.0, 1.0, 1.0]}}]

If no vehicle damage is visible at all, respond with an empty array: []"""


_CLASSIFY_PROMPT = """Look at this photo, submitted as evidence for a motor insurance claim.

Decide: is this photo actually useful for assessing VEHICLE DAMAGE -- i.e. does it show a vehicle closely or clearly enough that dents, scratches, cracks, or missing parts could plausibly be judged from it?

Answer "false" for photos whose main subject is NOT close/clear vehicle damage -- e.g. a wide parking-lot or street scene included only to show location/context, a photo of documents or a police report, a photo of people, or a vehicle so small/distant in frame that individual damage isn't visible.

Answer "true" for anything showing a vehicle up close or at a distance where damage is actually visible, even if the photo also includes background context.

Respond with ONLY this JSON, no other text:
{"is_damage_photo": true or false, "reason": "one short phrase"}"""


async def classify_photo_purpose(image_data: bytes) -> bool:
    """
    Gate before the (expensive, and prone to overconfident false positives
    on non-damage photos -- see scan_all_damage_zones docstring) whole-photo
    damage scan below. A live claim included a wide parking-lot photo
    submitted purely to show the member's location (corroborating their
    narrative), and the damage-scan prompt -- which is never given the
    option to say "there's no damage here to describe" -- still returned a
    confident, fabricated "Front Bumper: dented and misaligned" finding on
    it. Rather than try to make the damage-scan prompt itself refuse to
    answer (which fights its own instructions to be thorough), this asks a
    separate, narrow yes/no question first and skips the damage scan
    entirely for anything that isn't actually a damage-assessment photo.
    Fails OPEN (treats the photo as a damage photo) on any error, since a
    missed real damage zone is worse than an occasional wasted scan call.
    """
    try:
        from ollama_client import generate

        response_text = await asyncio.to_thread(
            generate, _CLASSIFY_PROMPT, model=VISION_MODEL, images=[image_data], json_mode=True, timeout=30,
        )
        parsed = json.loads(response_text)
        if isinstance(parsed, dict) and "is_damage_photo" in parsed:
            return bool(parsed["is_damage_photo"])
        return True
    except Exception as e:
        logger.warning(f"Photo-purpose classification failed, defaulting to treating it as a damage photo: {e}")
        return True


_LOCATION_CONTEXT_PROMPT = """Look at this photo, submitted alongside a motor insurance claim to help show WHERE the incident happened or where the vehicle currently is.

Describe, in one short factual sentence, any visible clues to the location: readable signage/shop names, landmark buildings, road names or markings, or a general setting description (e.g. "residential street", "shopping mall parking lot", "highway shoulder"). Only report what's actually visible/readable -- do not guess a specific place name you can't actually read in the image.

If there are no useful location clues at all (e.g. it's a close-up with no background visible), respond with null for description.

Respond with ONLY this JSON, no other text:
{"description": "one short sentence, or null if nothing useful is visible"}"""


async def describe_location_context(image_data: bytes) -> Optional[str]:
    """
    Extracts visible location clues (signage, landmarks, setting) from a
    photo -- meant to be called on photos classify_photo_purpose() already
    ruled out as damage photos (a wide parking-lot/street shot submitted
    purely to corroborate where the member says they were), which is
    exactly the class of photo this kind of clue tends to appear in. Feeds
    business_rules.py's location_narrative_correlation rule, which compares
    this against the claim's stated incident location. Advisory only --
    returns None on any failure or if nothing useful is visible, same
    fail-safe spirit as everything else in this module.
    """
    try:
        from ollama_client import generate

        response_text = await asyncio.to_thread(
            generate, _LOCATION_CONTEXT_PROMPT, model=VISION_MODEL, images=[image_data], json_mode=True, timeout=30,
        )
        parsed = json.loads(response_text)
        if not isinstance(parsed, dict):
            return None
        desc = parsed.get("description")
        if not desc or not isinstance(desc, str) or desc.strip().lower() in ("null", "none", ""):
            return None
        return desc.strip()
    except Exception as e:
        logger.warning(f"Location-context extraction failed: {e}")
        return None


# The full checklist scan above asks the model to reason over ~22 parts,
# the whole scene, AND strict JSON formatting in a single call -- a live
# diagnostic proved the model DOES correctly perceive small/destroyed parts
# (both headlights on a car, described in exact plain-English detail) when
# asked a single narrow question about just that region, but the same
# model omitted them entirely from the big checklist scan. That's a
# salience/attention-budget failure of the combined call, not a vision
# capability gap -- so rather than keep tweaking the big prompt, this runs
# a second, cheap, narrowly-scoped follow-up call dedicated to exactly the
# small parts most likely to get crowded out (headlamps, taillamps,
# mirrors, grille), mirroring the free-text style that was proven to work.
_SMALL_PARTS = ["Left Headlamp", "Right Headlamp", "Left Taillamp", "Right Taillamp", "Front Grille", "Left Front Wing Mirror", "Right Front Wing Mirror"]

_SMALL_PARTS_PROMPT = f"""Look closely at every vehicle in this accident photo, one at a time. For EACH vehicle, focus specifically on these small parts: {", ".join(_SMALL_PARTS)}.

For each of these small parts, decide: is it present and undamaged, damaged but still there (cracked/broken/hanging), or completely missing/knocked out (an empty socket, dark hollow gap, exposed wiring, or nothing where it should be)? A part that is simply gone is just as much damage as one that's visibly cracked.

Only report parts that show damage or are missing -- skip parts that look intact. For each damaged/missing one, report:
- part: one of {", ".join(_SMALL_PARTS)}
- vehicle: brief description of which vehicle (e.g. "red car", "silver SUV")
- damage_type: one or two words, use "missing" or "detached" if it's simply not there
- severity: "minor", "moderate", or "severe" (missing/destroyed parts are always "severe")
- confidence: 0-100
- description: one short factual sentence describing exactly what you see (or don't see) in that spot
- grid_cell: this image has a semi-transparent yellow grid overlaid, labeled in each cell's top-left corner (columns A-F left to right, rows 1-4 top to bottom -- e.g. "C2" is 3rd column, 2nd row). Report the single cell containing the CENTER of this part -- use the grid lines to check yourself rather than guessing.
- bbox_normalized: approximate bounding box [x1, y1, x2, y2] as fractions 0.0-1.0 of image width/height

Respond with ONLY a JSON array (no other text), maximum 6 entries:
[{{"part": "...", "vehicle": "...", "damage_type": "...", "severity": "...", "confidence": 0-100, "description": "...", "grid_cell": "C2", "bbox_normalized": [0.0, 0.0, 1.0, 1.0]}}]

If all these small parts look intact on every vehicle, respond with an empty array: []"""


async def _scan_small_parts_followup(image_data: bytes) -> List[Dict[str, Any]]:
    """Narrow second pass dedicated to headlamps/taillamps/mirrors/grille --
    see the comment above _SMALL_PARTS for why this exists as a separate
    call rather than folded into the main checklist prompt."""
    try:
        from ollama_client import generate

        grid_image = _add_grid_overlay(image_data)
        response_text = await asyncio.to_thread(
            generate, _SMALL_PARTS_PROMPT, model=VISION_MODEL, images=[grid_image], json_mode=True, timeout=60,
        )
        parsed = json.loads(response_text)
        if isinstance(parsed, list):
            raw_zones = parsed
        elif isinstance(parsed, dict) and "part" in parsed:
            # See the matching comment in scan_all_damage_zones -- the model
            # sometimes returns one bare zone object instead of a
            # one-element array wrapping it.
            raw_zones = [parsed]
        elif isinstance(parsed, dict):
            # A dict with several list-valued keys (e.g. a stray
            # "confidence_scores": [0.85, 0.9] alongside the real zones
            # list) previously picked whichever list came first, sometimes
            # grabbing a list of bare numbers instead of zone objects and
            # crashing every z.get(...) below with 'float' object has no
            # attribute 'get'. Require the list's own elements to look like
            # zone objects (dicts), not just any list.
            raw_zones = next((v for v in parsed.values() if isinstance(v, list) and (not v or isinstance(v[0], dict))), [])
        else:
            raw_zones = []
        if not raw_zones:
            return []

        image_w, image_h = Image.open(io.BytesIO(image_data)).size
        zones = []
        for z in raw_zones[:6]:
            if not isinstance(z, dict):
                continue
            part = z.get("part")
            if not part or part not in _SMALL_PARTS:
                continue
            bbox_norm = _grid_cell_bbox(z.get("grid_cell"))
            if bbox_norm is None:
                bbox_norm = z.get("bbox_normalized")
                if isinstance(bbox_norm, list) and len(bbox_norm) == 4:
                    if any(v > 1.5 or v < -0.5 for v in bbox_norm):
                        bbox_norm = [
                            bbox_norm[0] / image_w, bbox_norm[1] / image_h,
                            bbox_norm[2] / image_w, bbox_norm[3] / image_h,
                        ]
                    bbox_norm = [round(max(0.0, min(1.0, v)), 4) for v in bbox_norm]
                else:
                    bbox_norm = None
            damage_type = z.get("damage_type", "")
            severity = z.get("severity", "moderate")
            desc = z.get("description", "")
            vehicle = z.get("vehicle")
            if vehicle:
                desc = f"{desc} ({vehicle})" if desc else vehicle
            zones.append({
                "part": part,
                "vehicle": vehicle,
                "damage_type": damage_type,
                "severity": severity,
                "confidence": round(min(100, max(0, z.get("confidence", 50))) / 100, 3),
                "description": desc,
                "bbox_normalized": bbox_norm,
                "recommended_action": _action_for_damage_type(damage_type, severity),
            })
        return zones
    except Exception as e:
        logger.warning(f"Small-parts follow-up scan failed: {e}")
        return []


async def scan_all_damage_zones(image_data: bytes, is_damage_photo: Optional[bool] = None) -> List[Dict[str, Any]]:
    """
    Whole-photo scan for multiple distinct damage zones, in one vision-LLM
    call -- complements the YOLO-detection-anchored functions above (which
    only ever look at regions the trained detector already flagged) with a
    broader qualitative pass that can surface several damaged parts per
    photo, closer to a full damage-report breakdown (front bumper, bonnet,
    headlamp, fender, ... each independently) rather than one region at a
    time. Advisory only -- returns an empty list on any failure or if no
    damage is visible, never raises.

    `is_damage_photo`, if the caller already ran classify_photo_purpose()
    itself (e.g. to also decide whether to run describe_location_context()
    on the same photo), skips a redundant second classification call.
    """
    try:
        from ollama_client import generate

        if is_damage_photo is None:
            is_damage_photo = await classify_photo_purpose(image_data)
        if not is_damage_photo:
            return []

        grid_image = _add_grid_overlay(image_data)
        response_text, small_parts_zones = await asyncio.gather(
            asyncio.to_thread(
                generate, _SCAN_PROMPT, model=VISION_MODEL, images=[grid_image], json_mode=True, timeout=90,
            ),
            _scan_small_parts_followup(image_data),
        )
        parsed = json.loads(response_text)

        # The model doesn't reliably return a bare array despite the prompt
        # asking for one -- it sometimes wraps it in {"damage_zones": [...]}
        # or similar. Unwrap the first list-valued field if we got an object.
        if isinstance(parsed, list):
            raw_zones = parsed
        elif isinstance(parsed, dict) and "part" in parsed:
            # Despite the prompt asking for an array, the model sometimes
            # returns a single bare zone object instead of a one-element
            # list wrapping it (e.g. {"part": "Rear Bumper", ...} directly).
            # Treat it as a single-zone list rather than falling through to
            # the list-valued-key unwrap below, which would otherwise grab
            # this object's own "bbox_normalized" float list and either
            # crash (pre-fix) or silently discard the only zone found.
            raw_zones = [parsed]
        elif isinstance(parsed, dict):
            # See the matching comment in _scan_small_parts_followup -- only
            # accept a list whose elements look like zone objects, not just
            # any list-valued key (a stray numeric list previously got
            # picked instead and crashed every z.get(...) below).
            raw_zones = next((v for v in parsed.values() if isinstance(v, list) and (not v or isinstance(v[0], dict))), [])
        else:
            raw_zones = []
        if not raw_zones:
            return []

        image_w, image_h = Image.open(io.BytesIO(image_data)).size

        zones = []
        for z in raw_zones[:10]:
            if not isinstance(z, dict):
                continue
            part = z.get("part")
            if not part or part not in _VALID_PARTS:
                continue
            # Trust the model's grid-cell pick over its raw float coordinates
            # whenever we have one -- picking a labeled cell out of a small
            # visible grid is a far more reliable task for these models than
            # regressing bbox floats freehand (a live claim placed "Roof" on
            # the ground, nowhere near either vehicle, using the raw-float
            # path this replaces as the fallback).
            bbox_norm = _grid_cell_bbox(z.get("grid_cell"))
            if bbox_norm is None:
                bbox_norm = z.get("bbox_normalized")
                if isinstance(bbox_norm, list) and len(bbox_norm) == 4:
                    # The model doesn't reliably normalize to 0-1 either --
                    # if any coordinate is out of that range, treat the whole
                    # box as pixel coordinates and normalize against the
                    # image's actual dimensions instead of discarding it.
                    if any(v > 1.5 or v < -0.5 for v in bbox_norm):
                        bbox_norm = [
                            bbox_norm[0] / image_w, bbox_norm[1] / image_h,
                            bbox_norm[2] / image_w, bbox_norm[3] / image_h,
                        ]
                    bbox_norm = [round(max(0.0, min(1.0, v)), 4) for v in bbox_norm]
                else:
                    bbox_norm = None
            damage_type = z.get("damage_type", "")
            severity = z.get("severity", "moderate")
            zones.append({
                "part": part,
                "vehicle": z.get("vehicle"),
                "damage_type": damage_type,
                "severity": severity,
                "confidence": round(min(100, max(0, z.get("confidence", 50))) / 100, 3),
                "description": z.get("description", ""),
                "bbox_normalized": bbox_norm,
                "recommended_action": _action_for_damage_type(damage_type, severity),
            })

        # Fold in the small-parts follow-up, skipping any part the main
        # scan already caught on its own.
        found_parts = {z["part"] for z in zones}
        for sz in small_parts_zones:
            if sz["part"] not in found_parts:
                zones.append(sz)
                found_parts.add(sz["part"])

        return zones[:12]
    except Exception as e:
        logger.warning(f"Whole-photo damage zone scan failed: {e}")
        return []


async def identify_specific_part(image_data: bytes, bbox: List[float]) -> Optional[Dict[str, Any]]:
    """Returns {"part": str, "damage_description": str} or None if the
    model couldn't confidently identify the part, or the call failed --
    callers should keep their existing generic label in either case."""
    if not bbox or len(bbox) != 4:
        return None
    try:
        from ollama_client import generate

        crop_bytes = crop_and_zoom(image_data, bbox)
        response_text = await asyncio.to_thread(
            generate, _PROMPT, model=VISION_MODEL, images=[crop_bytes], json_mode=True, timeout=60,
        )
        result = json.loads(response_text)
        part = result.get("part")
        if not part or not result.get("confident", True) or part not in _VALID_PARTS:
            return None
        return {"part": part, "damage_description": result.get("damage_description", "")}
    except Exception as e:
        logger.warning(f"Part identification failed for bbox {bbox}: {e}")
        return None


# Illustrative Kenyan parts + labour cost ranges (KES) by part category and
# action, same spirit as business_rules.py's _ILLUSTRATIVE_MARKET_VALUE_KES
# -- NOT a live pricing feed, just enough of a grounded anchor that the
# estimate lands in a realistic band instead of being a raw LLM number. An
# LLM asked to freely guess a total KES figure has no numeric anchor at all
# and can be off by an order of magnitude (a live test came back ~KES 1.9M
# for a single bumper + fender, versus a realistic ~30-80K) -- doing the
# arithmetic here in code and using the LLM only to classify each zone into
# one of these known categories is far more reliable than trusting an LLM's
# own large-number arithmetic.
#
# Calibrated (Aug 2026) against real part listings on Jiji.co.ke and
# Partfinder Kenya for common Toyota models (the dominant fleet on Kenyan
# roads), a Probox-specific bumper price breakdown, plus quoted Nairobi
# garage service prices -- not invented from scratch:
#   - bumper, Probox-specific: used/aftermarket KES 5K-15K, new/full
#     assembly KES 30K-50K. The first calibration of this table priced
#     "replace" off the NEW-part figures, which a real-world check flagged
#     as unrealistically high (a full bumper replace job for KES 40.5K) --
#     Kenyan repair shops default to used/aftermarket parts unless a
#     customer specifically pays for new/genuine, so "replace" here is
#     calibrated off the USED-part price (the realistic default), not new
#   - fender part-only: KES 7K-18K (Vitz ~7K, Prado ~13-18K)
#   - headlight/taillight assemblies: genuine/aftermarket Toyota units
#     commonly KES 5K-15K/1.5K-6K respectively (marketplace "from KES 150"
#     listings are broken/incomplete parts, not representative units)
#   - windscreen: real quoted all-in Nairobi replacement service, KES
#     10K-25K -- the one figure here that's a full service price already,
#     not a part-only price with labour added on top
#   - panel spray/paint labour: Nairobi garages quote "from KES 2,000" per
#     panel for a basic respray, more for prep/filler work on real damage
# "replace" bands below = typical used/aftermarket part price + ~3-8K
# fitting/paint labour. Everything without a direct data point (bonnet,
# door, roof, boot, mirror, wheel) is interpolated from the same
# part-size/complexity logic and the same used-part-by-default assumption
# as the sourced figures above -- still an estimate, just not a blind one.
# (repair_low, repair_high, replace_low, replace_high)
_PART_COST_RANGES_KES: Dict[str, tuple] = {
    "bumper":       (5_000, 15_000, 8_000, 25_000),
    "fender":       (5_000, 12_000, 8_000, 20_000),
    "wing":         (5_000, 12_000, 8_000, 20_000),
    "headlight":    (2_000, 5_000, 4_000, 12_000),
    "taillight":    (1_500, 4_000, 3_000, 8_000),
    "bonnet":       (8_000, 20_000, 12_000, 30_000),
    "hood":         (8_000, 20_000, 12_000, 30_000),
    "door":         (8_000, 20_000, 10_000, 25_000),
    "windshield":   (0, 0, 10_000, 25_000),
    "windscreen":   (0, 0, 10_000, 25_000),
    "mirror":       (0, 0, 2_500, 8_000),
    "roof":         (12_000, 30_000, 20_000, 55_000),
    "boot":         (8_000, 20_000, 12_000, 30_000),
    "trunk":        (8_000, 20_000, 12_000, 30_000),
    "wheel":        (0, 0, 4_000, 15_000),
    "windowglass":  (0, 0, 2_500, 8_000),
}
_DEFAULT_COST_RANGE_KES = (5_000, 15_000, 7_000, 20_000)  # unmatched/generic part

_SEVERITY_POSITION = {"minor": 0.15, "low": 0.15, "moderate": 0.5, "medium": 0.5, "severe": 0.9, "high": 0.9, "critical": 1.0}


def _match_part_category(part_name: str) -> tuple:
    normalized = (part_name or "").lower().replace(" ", "").replace("-", "").replace("_", "")
    for key, cost_range in _PART_COST_RANGES_KES.items():
        if key in normalized:
            return cost_range
    return _DEFAULT_COST_RANGE_KES


def _zone_cost_kes(zone: Dict[str, Any]) -> float:
    action = (zone.get("recommended_action") or "repair").lower()
    severity = (zone.get("severity") or "moderate").lower()
    repair_low, repair_high, replace_low, replace_high = _match_part_category(zone.get("part", ""))
    position = _SEVERITY_POSITION.get(severity, 0.5)

    if action == "replace" or (repair_high == 0 and replace_high > 0):
        low, high = replace_low, replace_high
    else:
        low, high = repair_low, repair_high
        if high == 0:  # e.g. windshield/mirror -- repair isn't a real option
            low, high = replace_low, replace_high

    return low + (high - low) * position


async def estimate_damage_cost(
    damage_zones: List[Dict[str, Any]],
    vehicle_make: str = "",
    vehicle_model: str = "",
    vehicle_year: str = "",
) -> Optional[Dict[str, Any]]:
    """
    AI's own independent repair-cost estimate from detected damage, used to
    cross-check whatever cost figure a human (member/assessor) entered --
    see business_rules.py's cost-reasonableness rule. Advisory only, like
    everything else here: if this fails, callers should treat the human
    figure as the only one available rather than blocking the claim.

    Cost per zone is computed deterministically from _PART_COST_RANGES_KES
    (illustrative reference ranges, not a live pricing source) rather than
    asked of the LLM as raw arithmetic -- see the comment on that table for
    why. No LLM involvement in the breakdown either: it's the literal
    per-zone line items so the total is independently checkable (add up
    the lines yourself and you get the same number), not a prose summary
    that collapses/rounds individual figures and hides the arithmetic.

    Returns {"estimated_cost_kes": float, "breakdown": str} or None.
    """
    if not damage_zones:
        return None
    try:
        line_items = []
        total = 0.0
        for z in damage_zones:
            cost = _zone_cost_kes(z)
            total += cost
            action = (z.get("recommended_action") or "repair").lower()
            line_items.append(f"{z.get('part', 'Unknown part')} ({action}, {z.get('severity', 'moderate')}): KES {cost:,.0f}")

        if total <= 0:
            return None

        # No rounding to a "round number" here -- each line item already
        # displays its exact per-zone cost, so the exact (unrounded) sum is
        # used for both the breakdown's "Total" line and the returned
        # estimated_cost_kes. Rounding either one separately (e.g. to the
        # nearest 100) would make the headline number disagree with what
        # adding up the line items by hand actually gives you.
        breakdown = "\n".join(f"- {item}" for item in line_items) + f"\nTotal: KES {total:,.0f}"

        return {"estimated_cost_kes": total, "breakdown": breakdown}
    except Exception as e:
        logger.warning(f"AI damage cost estimation failed: {e}")
        return None


_CRUSH_DEPTH_PROMPT_TEMPLATE = """You are looking at a close-up photo of vehicle damage to the {part}.

This vehicle's overall body width is approximately {vehicle_width_mm:.0f}mm -- use that as your scale reference: judge roughly what fraction of the vehicle's width is visible across this photo's frame, then use that to convert the visible deformation into a real-world measurement.

Estimate how deep the crush/deformation goes into the vehicle's body, in millimeters -- i.e. how far the original body line has been pushed inward, not the total visible damaged area. A scuff or paint-only scratch is close to 0mm. A dent that's still shallow relative to the panel is often 20-80mm. A deep crumple where the panel has folded is often 150-400mm+.

This is a rough single-photo visual estimate, not a physical measurement -- be honest that you're estimating, and give a confidence (0-100) for how sure you are given the photo angle, lighting, and how clearly the deformation depth is actually visible (a straight-on shot with clear deformation should score higher than an oblique angle or a photo where the depth is hard to judge).

Respond with ONLY this JSON, no other text:
{{"crush_depth_mm": 0-500, "confidence": 0-100, "reasoning": "one short phrase"}}"""


async def estimate_crush_depth_mm(image_data: bytes, part: str, vehicle_width_mm: float) -> Optional[Dict[str, Any]]:
    """
    Single-photo, scale-calibrated crush-depth estimate -- used as a fallback
    ONLY when neither the narrative nor an assessor provided a crush-depth
    measurement (physics reconstruction otherwise has no independent way to
    check a claimed speed against the actual damage). This is genuinely an
    estimate, not a measurement: a monocular photo has no depth channel, so
    what this actually does is ask the vision model to reason about apparent
    deformation proportional to the vehicle's own known real-world width
    (from vehicle_registry.py) as a scale anchor -- a legitimate but rough
    photogrammetry technique, meaningfully different in reliability from an
    assessor's tape-measure reading or a narrative-stated figure. Callers
    MUST label this data source as a vision estimate (not "measured" or
    "calculated"), matching the honesty rule already used for every other
    inferred value in this pipeline. Returns None on any failure -- physics
    reconstruction falls back to a 0mm/no-crush-check run rather than a
    fabricated number.
    """
    try:
        from ollama_client import generate

        prompt = _CRUSH_DEPTH_PROMPT_TEMPLATE.format(part=part, vehicle_width_mm=vehicle_width_mm)
        response_text = await asyncio.to_thread(
            generate, prompt, model=VISION_MODEL, images=[image_data], json_mode=True, timeout=45,
        )
        parsed = json.loads(response_text)
        if not isinstance(parsed, dict):
            return None
        depth = parsed.get("crush_depth_mm")
        if depth is None:
            return None
        depth = max(0.0, min(500.0, float(depth)))
        confidence = max(0, min(100, int(parsed.get("confidence", 40))))
        return {"crush_depth_mm": round(depth, 1), "confidence": confidence, "reasoning": parsed.get("reasoning", "")}
    except Exception as e:
        logger.warning(f"Vision crush-depth estimation failed: {e}")
        return None
