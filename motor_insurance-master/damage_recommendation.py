"""
Repair-vs-replace recommendation, per detected damage component.

Deliberately deterministic, not an LLM call: the trained YOLO model
(merged_v2_yolov8n_seg, see damage_detector.py) already gives a class name
and a confidence per detection -- turning that into "repair" or "replace"
is a lookup, not a judgment call that needs a language model. Confidence on
the recommendation is the detection's own confidence (how sure the model is
this damage type is actually present), not a separate fabricated number.

This is advisory only, same as every other AI signal in this system: the
assessor sees the recommendation and reason, and makes their own repair/
replace call — never the other way around.
"""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

# Classes where the damage itself (a crack, a break, total separation) means
# the part can't be safely or cost-effectively repaired -- structural glass,
# lighting units, and detached/severely deformed panels all fall here.
_REPLACE_CLASSES = {
    "detachment", "wreck-total-loss", "severe-deformation",
    "glass-crack-generic", "glass-crack-windscreen-front", "glass-crack-windscreen-rear",
    "glass-crack-window", "side-mirror-crack", "car-part-crack",
    "headlight-damage", "taillight-damage", "signlight-damage",
    "flat-tire",
}

# Everything else detectable is cosmetic/structural damage a body shop can
# straighten, fill, or refinish without swapping the part.
_REPAIR_CLASSES = {
    "bumper-dent-front", "bumper-dent-rear", "hood-bonnet-dent", "door-dent",
    "fender-dent", "roof-dent", "pillar-dent", "runningboard-dent",
    "generic-dent", "minor-deformation", "moderate-deformation",
    "scratches", "paint-chips", "wheel-damage-generic", "trunk-damage",
    "lamp-damage-generic", "generic-damage",
}

# These are the model's catch-all classes -- it means "damage is present but
# I couldn't pin down what type," not "this damage is minor." Defaulting
# them to "repair" regardless of context was the bug a real test caught:
# a severely crushed panel got classified as generic-damage at low
# confidence and was recommended for repair anyway. For these classes the
# recommendation instead follows the photo's overall CV-severity read
# (`cv_severity`, computed independently across all detections on the
# photo) -- a generic detection on an otherwise high-severity photo should
# lean toward replace, not repair.
_AMBIGUOUS_CLASSES = {"generic-damage", "generic-dent", "moderate-deformation"}


def is_ambiguous_class(yolo_class: str) -> bool:
    """True for classes whose name doesn't identify a specific part -- these
    are the ones worth a zoomed-in vision-LLM pass (see part_identifier.py)
    to name the actual part instead of falling back to a generic label."""
    return yolo_class in _AMBIGUOUS_CLASSES

LOW_CONFIDENCE_THRESHOLD = 0.4

# Human-readable component name per YOLO class -- parsed from the class
# string where it encodes a part, otherwise a generic label.
_COMPONENT_NAMES = {
    "bumper-dent-front": "Front Bumper",
    "bumper-dent-rear": "Rear Bumper",
    "hood-bonnet-dent": "Bonnet",
    "door-dent": "Door",
    "fender-dent": "Fender",
    "roof-dent": "Roof",
    "pillar-dent": "Pillar",
    "runningboard-dent": "Running Board",
    "trunk-damage": "Trunk",
    "headlight-damage": "Headlamp",
    "taillight-damage": "Taillamp",
    "signlight-damage": "Indicator Light",
    "side-mirror-crack": "Side Mirror",
    "glass-crack-windscreen-front": "Windscreen",
    "glass-crack-windscreen-rear": "Rear Windscreen",
    "glass-crack-window": "Window",
    "glass-crack-generic": "Glass",
    "flat-tire": "Tyre",
    "wheel-damage-generic": "Wheel",
    "car-part-crack": "Body Panel",
    "detachment": "Detached Part",
    "wreck-total-loss": "Vehicle (Total Loss)",
    "minor-deformation": "Body Panel",
    "moderate-deformation": "Body Panel",
    "severe-deformation": "Body Panel",
    "scratches": "Body Panel",
    "paint-chips": "Paintwork",
    "lamp-damage-generic": "Light Unit",
    "generic-dent": "Body Panel",
    "generic-damage": "Body Panel",
}

_REASON_TEMPLATES = {
    "replace": "Visible {noun} may compromise component integrity — repair would not restore it to a safe or roadworthy condition.",
    "repair": "Damage appears localized and the underlying structure appears intact — a straightforward repair should restore it.",
}

_NOUN_BY_CLASS = {
    "detachment": "detachment",
    "wreck-total-loss": "structural loss",
    "severe-deformation": "structural deformation",
    "glass-crack-generic": "cracking",
    "glass-crack-windscreen-front": "cracking",
    "glass-crack-windscreen-rear": "cracking",
    "glass-crack-window": "cracking",
    "side-mirror-crack": "cracking",
    "car-part-crack": "cracking",
    "headlight-damage": "cracking/breakage",
    "taillight-damage": "cracking/breakage",
    "signlight-damage": "cracking/breakage",
    "flat-tire": "tyre failure",
}


@dataclass
class DamageRecommendation:
    yolo_class: str
    component: str
    confidence: float  # 0.0-1.0, the detection's own confidence
    bbox: List[float]
    recommended_action: str  # "repair" or "replace"
    reason: str
    polygon: Optional[List[List[float]]] = None  # traces the actual damage shape, when the model produced a mask
    low_confidence: bool = False

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "class": self.yolo_class,
            "component": self.component,
            "confidence": self.confidence,
            "bbox": self.bbox,
            "recommended_action": self.recommended_action,
            "reason": self.reason,
            "low_confidence": self.low_confidence,
        }
        if self.polygon:
            d["polygon"] = self.polygon
        return d


def recommend_action(
    yolo_class: str, confidence: float,
    bbox: Optional[List[float]] = None, polygon: Optional[List[List[float]]] = None,
    photo_severity: Optional[str] = None,
) -> DamageRecommendation:
    if yolo_class in _AMBIGUOUS_CLASSES:
        # The class name itself carries no real signal here -- fall back to
        # the photo's overall severity read instead of a fixed default.
        if photo_severity == "high":
            action = "replace"
            reason = (
                "The model couldn't classify this damage precisely, but the photo's overall damage "
                "severity reads as high — treat this as a likely replace pending on-site inspection."
            )
        elif photo_severity == "medium":
            action = "repair"
            reason = (
                "The model couldn't classify this damage precisely; overall photo severity reads as "
                "moderate. Verify on-site before committing to repair."
            )
        else:
            action = "repair"
            reason = (
                "The model detected damage here but couldn't classify its specific type or severity — "
                "this recommendation is a low-information default, not a confident read. Verify on-site."
            )
    else:
        action = "replace" if yolo_class in _REPLACE_CLASSES else "repair"
        noun = _NOUN_BY_CLASS.get(yolo_class, "damage")
        reason = _REASON_TEMPLATES[action].format(noun=noun)

    component = _COMPONENT_NAMES.get(yolo_class, yolo_class.replace("-", " ").replace("_", " ").title())
    low_confidence = confidence < LOW_CONFIDENCE_THRESHOLD
    if low_confidence:
        reason += " Detection confidence is low — confirm this finding against the physical vehicle."

    return DamageRecommendation(
        yolo_class=yolo_class,
        component=component,
        confidence=round(confidence, 3),
        bbox=bbox or [],
        recommended_action=action,
        reason=reason,
        polygon=polygon,
        low_confidence=low_confidence,
    )


def build_detections_with_recommendations(
    detections: List[Dict[str, Any]], photo_severity: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """detections: raw output of damage_detector.detect_damage() -- [{"class", "confidence", "bbox", "polygon"?}, ...]"""
    return [
        recommend_action(d.get("class", ""), d.get("confidence", 0.0), d.get("bbox"), d.get("polygon"), photo_severity).to_dict()
        for d in detections
        if d.get("class")
    ]
