"""
Wraps the trained merged_v2_yolov8n_seg model (30-class car damage instance
segmentation) so it can be called in-process from PhotoAnalysisService,
giving deterministic, structured damage detections to ground the LLM's
qualitative reasoning — instead of the LLM guessing damage from pixels alone.

v2 extends the original 28-class merged_v1 (CarDD + Skillfactory) with NUST
and Car Damage V5, adding "wheel-damage-generic" and "wreck-total-loss" while
keeping the original 28 class indices unchanged. Box mAP50 0.547 / mask mAP50
0.528 on the 30-class validation split, slightly ahead of merged_v1's 0.536 /
0.520 on its narrower 28-class set. One caveat: "wheel-damage-generic" (only
~35 source instances, all from NUST) had no validation-split representation
in this training run, so treat its detections with lower confidence than the
other 29 classes until more source images exist for it.

Loaded once at first use and kept resident (loading a YOLO checkpoint per
request would be needlessly slow).
"""

import io
import logging
from pathlib import Path
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

MODEL_PATH = Path(__file__).parent / "cv" / "models" / "merged_v2_yolov8n_seg" / "best.pt"
CONF_THRESHOLD = 0.25

_model = None


def _get_model():
    global _model
    if _model is None:
        from ultralytics import YOLO

        if not MODEL_PATH.exists():
            raise FileNotFoundError(f"Damage detection checkpoint not found at {MODEL_PATH}")

        logger.info(f"🔧 Loading damage detection model from {MODEL_PATH}")
        _model = YOLO(str(MODEL_PATH))
        logger.info(f"✅ Damage detection model loaded — {len(_model.names)} classes")

    return _model


def detect_damage(image_data: bytes, conf_threshold: float = CONF_THRESHOLD) -> List[Dict[str, Any]]:
    """
    Run damage detection on raw image bytes.

    Returns a list of detections:
        [{"class": "bumper-dent-front", "confidence": 0.81, "bbox": [x1,y1,x2,y2]}, ...]
    Empty list if the model finds nothing above threshold, or if inference fails
    (caller should treat that as "no signal", not fail the whole photo analysis).
    """

    try:
        from PIL import Image

        model = _get_model()
        image = Image.open(io.BytesIO(image_data))

        results = model.predict(source=image, conf=conf_threshold, verbose=False)
        r = results[0]

        detections = []
        if r.boxes is not None and len(r.boxes) > 0:
            for box, cls, conf in zip(r.boxes.xyxy, r.boxes.cls, r.boxes.conf):
                detections.append({
                    "class": model.names[int(cls)],
                    "confidence": round(float(conf), 3),
                    "bbox": [round(float(v), 1) for v in box.tolist()],
                })

        return detections

    except Exception as e:
        logger.error(f"❌ Damage detection failed: {str(e)}")
        return []
