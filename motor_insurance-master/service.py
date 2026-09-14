import logging
import os
from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime, timedelta
import random
import asyncio
from PIL import Image
import io
import numpy as np
import json
import base64
import hashlib
import time
import traceback
import functools
import re
import math
# Gemini imports
import google.generativeai as genai

from utils import ImageProcessor, TextProcessor, MetricsCalculator
from schemas import PhotoAnomalySchema, NarrativeAnalysisSchema, RiskScoringSchema, RiskLevel
from database import db_manager
from ollama_client import generate, generate_json, OllamaError
import ai_rol
import business_rules
import graph_relationship
import narrative_similarity

# The dev GPU can only host one large model at a time — the team standardized
# on gemma4:26b (also used for photo vision), so all text-reasoning tasks that
# used to call Gemini share this same model instead of a second one.
TEXT_REASONING_MODEL = os.environ.get("OLLAMA_TEXT_MODEL", "gemma4:26b")

# Configure comprehensive logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - [%(funcName)s:%(lineno)d] - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('motor_underwriting_ai.log', encoding='utf-8')
    ]
)

logger = logging.getLogger(__name__)

# Gemini API Configuration
#GEMINI_API_KEY = "AIzaSyBASeUr3bcegRWGiiNreYEr4A-TYpcsQUQ"
GEMINI_API_KEY = "AIzaSyBAIlTiNR3thcxU7nbn8DH654swOY25bPw"
# Performance logging decorator
def log_performance(func):
    """Decorator to log function performance metrics"""
    @functools.wraps(func)
    async def async_wrapper(*args, **kwargs):
        start_time = time.time()
        function_name = f"{func.__module__}.{func.__qualname__}"
        
        # Extract claim_id if available for database logging
        claim_id = kwargs.get('claim_id') or (args[2] if len(args) > 2 else 'unknown')
        
        logger.info(f"Starting {function_name} with args count: {len(args)}, kwargs: {list(kwargs.keys())}")
        
        try:
            result = await func(*args, **kwargs)
            execution_time = time.time() - start_time
            execution_time_ms = int(execution_time * 1000)
            
            # Log to database
            db_manager.log_processing_step(
                claim_id=str(claim_id),
                step=function_name,
                processing_time_ms=execution_time_ms,
                status='success',
                details=f"Completed successfully in {execution_time:.3f}s"
            )
            
            # Log success metrics
            logger.info(f"{function_name} completed successfully in {execution_time:.3f}s")
            
            # Log result summary if it's a dict
            if isinstance(result, dict):
                summary_keys = list(result.keys())[:5]  # First 5 keys
                logger.debug(f"{function_name} result keys: {summary_keys}")
            
            return result
            
        except Exception as e:
            execution_time = time.time() - start_time
            execution_time_ms = int(execution_time * 1000)
            
            # Log error to database
            db_manager.log_processing_step(
                claim_id=str(claim_id),
                step=function_name,
                processing_time_ms=execution_time_ms,
                status='error',
                details=f"Failed with error: {str(e)}"
            )
            
            logger.error(f"{function_name} failed after {execution_time:.3f}s: {str(e)}")
            logger.debug(f"{function_name} traceback: {traceback.format_exc()}")
            raise
    
    @functools.wraps(func)
    def sync_wrapper(*args, **kwargs):
        start_time = time.time()
        function_name = f"{func.__module__}.{func.__qualname__}"
        
        # Extract claim_id if available for database logging
        claim_id = kwargs.get('claim_id') or (args[2] if len(args) > 2 else 'unknown')
        
        logger.info(f"Starting {function_name} with args count: {len(args)}, kwargs: {list(kwargs.keys())}")
        
        try:
            result = func(*args, **kwargs)
            execution_time = time.time() - start_time
            execution_time_ms = int(execution_time * 1000)
            
            # Log to database
            db_manager.log_processing_step(
                claim_id=str(claim_id),
                step=function_name,
                processing_time_ms=execution_time_ms,
                status='success',
                details=f"Completed successfully in {execution_time:.3f}s"
            )
            
            logger.info(f"{function_name} completed successfully in {execution_time:.3f}s")
            
            if isinstance(result, dict):
                summary_keys = list(result.keys())[:5]
                logger.debug(f"{function_name} result keys: {summary_keys}")
            
            return result
            
        except Exception as e:
            execution_time = time.time() - start_time
            execution_time_ms = int(execution_time * 1000)
            
            # Log error to database
            db_manager.log_processing_step(
                claim_id=str(claim_id),
                step=function_name,
                processing_time_ms=execution_time_ms,
                status='error',
                details=f"Failed with error: {str(e)}"
            )
            
            logger.error(f"{function_name} failed after {execution_time:.3f}s: {str(e)}")
            logger.debug(f"{function_name} traceback: {traceback.format_exc()}")
            raise
    
    # Return appropriate wrapper based on whether function is async
    if asyncio.iscoroutinefunction(func):
        return async_wrapper
    else:
        return sync_wrapper

class PhotoAnalysisService:
    """
    Photo analysis service for fraud detection, backed by:
      - the trained merged_v2_yolov8n_seg model (damage_detector.py) for
        deterministic, structured damage detection, and
      - a self-hosted Ollama vision-language model (gemma4:26b by default,
        override via OLLAMA_VISION_MODEL) for qualitative reasoning —
        grounded in the CV model's detections rather than guessing from
        pixels alone.

    Deliberately pinned to its own vision-capable model rather than the
    shared ollama_client.OLLAMA_MODEL default: that default now points at
    claims-advisory-v1, a text-only LoRA fine-tune with no vision tower
    (see nlp/scripts/export_to_ollama.md) -- sending images to it would
    silently misbehave.

    Replaces the previous Gemini Vision integration.
    """

    VISION_MODEL = os.environ.get("OLLAMA_VISION_MODEL", "gemma4:26b")

    def __init__(self):
        logger.info("Initializing PhotoAnalysisService (YOLO + Ollama backed)")
        self.image_processor = ImageProcessor()
        logger.info("PhotoAnalysisService initialized")
    
    def _check_duplicate_hash(self, perceptual_hash: str, claim_id: str) -> bool:
        """Check if image hash already exists in database"""
        try:
            with db_manager.get_connection() as conn:
                cursor = conn.cursor()
                
                # Check if this hash exists in any previous photo analysis
                cursor.execute('''
                    SELECT cp.claim_id, cp.filename, cp.analysis_result
                    FROM claim_photos cp
                    WHERE json_extract(analysis_result, '$.hash') = ?
                    AND cp.claim_id != ?
                ''', (perceptual_hash, claim_id))
                
                duplicate = cursor.fetchone()
                
                if duplicate:
                    logger.warning(f"DUPLICATE DETECTED: Hash {perceptual_hash[:12]}... found in claim {duplicate['claim_id']}, file {duplicate['filename']}")
                    return True
                
                return False
                
        except Exception as e:
            logger.error(f"Error checking duplicate hash: {str(e)}")
            # Return False to allow processing if database check fails
            return False
    
    def _detect_ai_generated(self, image_data: bytes, filename: str) -> dict:
        """
        Detect AI-generated images before sending to Gemini.
 
        Checks:
        1. Filename patterns from known AI generators
        2. Complete absence of EXIF (AI images have zero metadata)
        3. Suspiciously perfect image statistics (std dev, entropy)
        """
 
        findings = []
        risk_score = 0
 
        # ── Check 1: Filename patterns ────────────────────────────────────────
        AI_FILENAME_PATTERNS = [
            r'gemini[_\-]generated',
            r'dall[_\-]?e',
            r'midjourney',
            r'stable[_\-]diffusion',
            r'sd[_\-]\d+',
            r'ai[_\-]generated',
            r'generated[_\-]image',
            r'imagen',
            r'firefly',
            r'bing[_\-]image',
            r'gpt[_\-]image',
        ]
 
        fname_lower = filename.lower()
        for pattern in AI_FILENAME_PATTERNS:
            if re.search(pattern, fname_lower):
                findings.append({
                    "type":        "ai_generated_filename",
                    "severity":    "critical",
                    "description": (
                        f"Filename '{filename}' matches known AI image generator pattern '{pattern}'. "
                        f"This image was almost certainly generated by an AI tool, not photographed at an accident scene."
                    ),
                    "confidence": 97,
                })
                risk_score += 60
                break
 
        # ── Check 2: Complete absence of EXIF ────────────────────────────────
        try:
            img = Image.open(io.BytesIO(image_data))
            exif = img._getexif()
 
            if exif is None:
                # Real phone photos always have some EXIF — complete absence is suspicious
                findings.append({
                    "type":        "no_exif_metadata",
                    "severity":    "high",
                    "description": (
                        "Photo has no EXIF metadata whatsoever. Real smartphone photos always contain "
                        "camera model, capture timestamp, and exposure settings. Complete EXIF absence "
                        "strongly indicates AI generation, screenshot, or deliberate metadata stripping."
                    ),
                    "confidence": 85,
                })
                risk_score += 30
            else:
                # Has EXIF — check if suspiciously minimal (only 1-2 fields)
                if len(exif) < 3:
                    findings.append({
                        "type":        "minimal_exif_metadata",
                        "severity":    "medium",
                        "description": (
                            f"Photo has only {len(exif)} EXIF field(s). "
                            "Genuine accident photos typically contain 20+ EXIF fields including "
                            "camera model, GPS, timestamp, and exposure data."
                        ),
                        "confidence": 70,
                    })
                    risk_score += 15
 
        except Exception:
            pass
 
        # ── Check 3: Image statistics (AI images have unnaturally uniform noise) ─
        try:
            import numpy as np
            img_gray = img.convert('L')
            pixels   = np.array(img_gray, dtype=float)
 
            std_dev = float(np.std(pixels))
            # Calculate approximate entropy
            hist = np.histogram(pixels, bins=256, range=(0, 256))[0]
            hist = hist[hist > 0] / hist.sum()
            entropy = float(-np.sum(hist * np.log2(hist)))
 
            # AI images tend to have lower noise std dev and higher entropy
            # Real accident photos: std_dev typically 40-80, entropy 6.5-7.5
            if std_dev < 25 and entropy > 7.2:
                findings.append({
                    "type":        "ai_image_statistics",
                    "severity":    "medium",
                    "description": (
                        f"Image noise profile (std_dev={std_dev:.1f}, entropy={entropy:.2f}) "
                        "is consistent with AI generation. Real accident photos have more "
                        "natural noise variation from camera sensors and compression."
                    ),
                    "confidence": 65,
                })
                risk_score += 20
 
        except Exception:
            pass
 
        return {
            "is_ai_generated":  len([f for f in findings if f["severity"] == "critical"]) > 0,
            "ai_risk_score":    min(risk_score, 100),
            "findings":         findings,
        }

    @log_performance
    async def analyze_photo(
        self,
        image_data: bytes,
        filename: str,
        claim_id: str,
        party: str = "member",
        narrative_context: str = "",
    ) -> PhotoAnomalySchema:
        """Comprehensive photo analysis for fraud detection using Gemini Vision with database integration"""
 
        logger.info(f"Starting photo analysis for claim {claim_id}, file: {filename} from party: {party}")
        logger.debug(f"Image data size: {len(image_data):,} bytes")
 
        analysis_start = time.time()
        anomalies = []
        risk_score = 0
 
        try:
            logger.debug("Converting image data to PIL format")
            image = Image.open(io.BytesIO(image_data))
            logger.info(f"Image loaded successfully - Size: {image.size}, Mode: {image.mode}")
 
            logger.debug("Computing image hashes for duplicate detection")
            hash_start = time.time()
            file_hash = self.image_processor.compute_file_hash(image_data)
            perceptual_hash = self.image_processor.compute_perceptual_hash(image)
            hash_time = time.time() - hash_start
            logger.debug(f"Hash computation completed in {hash_time:.3f}s - Perceptual: {perceptual_hash[:12]}...")
 
            # 1. Database-powered Duplicate Detection
            logger.info(f"Checking for duplicate photos in database (party: {party})")
            duplicate_check = self._check_duplicate_hash_multiparty(perceptual_hash, claim_id, party)
 
            if duplicate_check['is_duplicate']:
                logger.warning(f"DUPLICATE DETECTED: Hash {perceptual_hash[:12]}... matches {duplicate_check['matched_party']} submission")
 
                if duplicate_check['matched_party'] != party:
                    anomalies.append({
                        "type": "cross_party_duplicate",
                        "severity": "critical",
                        "description": f"Photo submitted by {party} matches photo from {duplicate_check['matched_party']} - FRAUD INDICATOR",
                        "confidence": 95,
                        "party": party,
                        "matched_party": duplicate_check['matched_party']
                    })
                    risk_score += 50
                else:
                    anomalies.append({
                        "type": "duplicate_photo",
                        "severity": "high",
                        "description": f"Photo hash {perceptual_hash} matches previously submitted claim",
                        "confidence": 95,
                        "party": party
                    })
                    risk_score += 35
            else:
                logger.debug("No duplicate detected in database")
 
            # 1b. AI generation detection (pre-Gemini, filename + EXIF checks)
            ai_check = self._detect_ai_generated(image_data, filename)
            if ai_check["findings"]:
                anomalies.extend([{**f, "party": party} for f in ai_check["findings"]])
                risk_score += ai_check["ai_risk_score"]
                if ai_check["is_ai_generated"]:
                    logger.warning(
                        f"AI-GENERATED IMAGE DETECTED: {filename} — "
                        f"risk score +{ai_check['ai_risk_score']}"
                    )
 
            # 2. AI-powered damage analysis: trained YOLO model + Ollama VLM
            photo_detections: List[Dict[str, Any]] = []
            try:
                llm_analysis = await self._analyze_with_llm(
                    image_data, filename, claim_id, party,
                    narrative_context=narrative_context,
                )
                logger.info(f"LLM analysis completed - Found {len(llm_analysis['anomalies'])} anomalies, risk: {llm_analysis['risk_score']}")
                anomalies.extend(llm_analysis["anomalies"])
                risk_score += llm_analysis["risk_score"]
                photo_detections = llm_analysis.get("detections", []) or []
            except Exception as e:
                logger.error(f"LLM analysis failed: {str(e)}, falling back to rule-based")
                fallback_analysis = await self._fallback_damage_analysis(image, filename, party)
                anomalies.extend(fallback_analysis["anomalies"])
                risk_score += fallback_analysis["risk_score"]

            # Trained-model-only severity read, independent of the vision LLM
            # (see `_cv_severity_from_detections` docstring for why this matters
            # for cross-checking a party's own claimed measurements).
            cv_severity = self._cv_severity_from_detections(photo_detections)
            detected_classes = [d.get("class") for d in photo_detections if d.get("class")]
            # Low-confidence-recovery detections already had their part
            # confirmed by the vision LLM inline (see _analyze_with_llm) --
            # keyed by bbox so the zoom-in pass below can reuse that result
            # instead of paying for a second identical vision call.
            vision_confirmed_by_bbox = {
                tuple(d["bbox"]): (d["vision_confirmed_part"], d.get("vision_confirmed_description", ""))
                for d in photo_detections
                if d.get("vision_confirmed_part") and d.get("bbox")
            }
            import damage_recommendation
            detections_with_recommendations = damage_recommendation.build_detections_with_recommendations(photo_detections, photo_severity=cv_severity)

            # ── Zoomed-in specific-part identification ──────────────────────────
            # The YOLO class names the DAMAGE TYPE, and several classes are
            # genuinely part-agnostic ("generic-damage") or low-confidence --
            # for those, crop+zoom the region and ask the vision LLM to name
            # the actual part, instead of leaving a generic "Body Panel" label.
            import part_identifier
            for det in detections_with_recommendations:
                needs_part_id = (
                    damage_recommendation.is_ambiguous_class(det.get("class", ""))
                    or det.get("low_confidence")
                )
                if not needs_part_id or not det.get("bbox"):
                    continue
                bbox_key = tuple(det["bbox"])
                if bbox_key in vision_confirmed_by_bbox:
                    part, description = vision_confirmed_by_bbox[bbox_key]
                    identified = {"part": part, "damage_description": description}
                else:
                    identified = await part_identifier.identify_specific_part(image_data, det["bbox"])
                if identified:
                    det["component"] = identified["part"]
                    det["part_identified_via"] = "vision_llm_zoom"
                    if identified.get("damage_description"):
                        det["reason"] = (
                            f"Close-up review identified this as the {identified['part']}, "
                            f"showing {identified['damage_description']}. {det['reason']}"
                        )

            # ── Whole-photo multi-zone scan ──────────────────────────────────────
            # Complements the YOLO-anchored detections above (which only ever
            # cover regions the trained detector itself flagged) with a
            # broader pass that can name several distinct damaged parts per
            # photo -- closer to the multi-part damage-report style this was
            # built to match, rather than one region at a time.
            is_damage_photo = await part_identifier.classify_photo_purpose(image_data)
            damage_zones = await part_identifier.scan_all_damage_zones(image_data, is_damage_photo=is_damage_photo)

            # A photo that isn't a damage close-up is usually the member
            # showing WHERE they were (a parking-lot/street shot submitted
            # to corroborate their narrative) -- pull any visible location
            # clues from exactly those photos so business_rules.py's
            # location_narrative_correlation rule can cross-check them
            # against the claim's stated incident location.
            location_context = None
            if not is_damage_photo:
                location_context = await part_identifier.describe_location_context(image_data)

            # 3. Enhanced lighting analysis
            logger.debug("Analyzing lighting conditions")
            lighting_analysis = self.image_processor.analyze_lighting_conditions(image_data)
            logger.debug(f"Lighting condition: {lighting_analysis['condition']}")
 
            if lighting_analysis["condition"] == "very_low_light":
                logger.warning("Very poor lighting detected")
                anomalies.append({
                    "type": "poor_lighting",
                    "severity": "medium",
                    "description": "Photo taken in very poor lighting conditions, may affect damage assessment",
                    "confidence": 80,
                    "party": party
                })
                risk_score += 15
 
            # 4. EXIF Metadata Analysis
            logger.debug("Extracting and analyzing EXIF metadata")
            exif_data = self.image_processor.extract_exif_metadata(image)
            logger.debug(f"EXIF data extracted - GPS: {exif_data.get('has_gps', False)}, Timestamp: {bool(exif_data.get('timestamp'))}")
 
            if not exif_data["has_gps"]:
                logger.warning("No GPS data found in image")
                anomalies.append({
                    "type": "missing_location_data",
                    "severity": "medium",
                    "description": "Photo lacks GPS location data for verification",
                    "confidence": 70,
                    "party": party
                })
                risk_score += 10
 
            # 5. Enhanced file analysis
            logger.debug("Checking for potential image editing")
            if self._detect_potential_editing(image_data, filename):
                logger.warning(f"Potential editing detected in {filename}")
                anomalies.append({
                    "type": "potential_editing",
                    "severity": "high",
                    "description": "Image shows signs of potential digital manipulation",
                    "confidence": 85,
                    "party": party
                })
                risk_score += 25
 
            # 6. Temporal Consistency
            if exif_data["timestamp"]:
                logger.debug(f"Checking timestamp consistency: {exif_data['timestamp']}")
                temporal_issues = self._check_temporal_consistency(exif_data["timestamp"])
                if temporal_issues:
                    logger.warning(f"Temporal inconsistency detected: {temporal_issues}")
                    anomalies.append({
                        "type": "temporal_inconsistency",
                        "severity": "medium",
                        "description": temporal_issues,
                        "confidence": 75,
                        "party": party
                    })
                    risk_score += 15
 
            final_risk_score = min(risk_score, 100)
            analysis_confidence = 85
            analysis_time = time.time() - analysis_start
 
            photo_result = PhotoAnomalySchema(
                filename=filename,
                hash=perceptual_hash,
                anomalies=anomalies,
                risk_score=final_risk_score,
                analysis_confidence=analysis_confidence,
                cv_severity=cv_severity,
                detected_classes=detected_classes,
                detections=detections_with_recommendations,
                damage_zones=damage_zones,
                location_context=location_context,
            )
 
            try:
                analysis_dict = photo_result.dict()
                analysis_dict['party'] = party
                db_manager.store_photo_analysis(
                    claim_id=claim_id,
                    filename=filename,
                    file_size=len(image_data),
                    content_type="image/jpeg",
                    analysis_result=analysis_dict
                )
                logger.debug(f"Photo analysis stored in database for {filename} (party: {party})")
            except Exception as e:
                logger.error(f"Failed to store photo analysis in database: {str(e)}")
 
            logger.info(f"Photo analysis completed for {filename} ({party}):")
            logger.info(f"Risk Score: {final_risk_score}/100")
            logger.info(f"Confidence: {analysis_confidence}%")
            logger.info(f"Anomalies: {len(anomalies)}")
            logger.info(f"Processing Time: {analysis_time:.3f}s")
 
            return photo_result
 
        except Exception as e:
            logger.error(f"Photo analysis failed for {filename}: {str(e)}")
            logger.debug(f"Full traceback: {traceback.format_exc()}")
            return self._get_error_response(filename, str(e))

    def _check_duplicate_hash_multiparty(self, perceptual_hash: str, claim_id: str, current_party: str) -> Dict[str, Any]:
        """
        Check for duplicate photos with party tracking
        Returns info about which party submitted the duplicate
        """
        try:
            with db_manager.get_connection() as conn:
                cursor = conn.cursor()
                
                # Check for matching hash in database
                cursor.execute('''
                    SELECT claim_id, filename, analysis_result
                    FROM claim_photos
                    WHERE claim_id != ?
                    ORDER BY created_at DESC
                    LIMIT 100
                ''', (claim_id,))
                
                for row in cursor.fetchall():
                    stored_analysis = json.loads(row['analysis_result']) if row['analysis_result'] else {}
                    stored_hash = stored_analysis.get('hash', '')
                    stored_party = stored_analysis.get('party', 'unknown')
                    
                    # Check if hashes match
                    if stored_hash and self._hashes_similar(perceptual_hash, stored_hash):
                        return {
                            'is_duplicate': True,
                            'matched_claim_id': row['claim_id'],
                            'matched_filename': row['filename'],
                            'matched_party': stored_party,
                            'cross_party': stored_party != current_party
                        }
                
                return {'is_duplicate': False}
                
        except Exception as e:
            logger.error(f"Error checking duplicate hash: {str(e)}")
            return {'is_duplicate': False}

    def _hashes_similar(self, hash1: str, hash2: str, threshold: int = 5) -> bool:
        """Check if two perceptual hashes are similar (Hamming distance)"""
        if not hash1 or not hash2 or len(hash1) != len(hash2):
            return False
        
        # Calculate Hamming distance
        distance = sum(c1 != c2 for c1, c2 in zip(hash1, hash2))
        return distance <= threshold

    @log_performance
    async def _analyze_with_llm(
        self,
        image_data: bytes,
        filename: str,
        claim_id: str,
        party: str = "member",
        narrative_context: str = "",
    ) -> Dict[str, Any]:
        """Analyze image using the trained YOLO damage detector + an Ollama vision-language model"""

        logger.info(f"Starting LLM analysis for {filename} from {party} (Claim: {claim_id})")

        try:
            # Ground the VLM's reasoning in the trained model's actual detections,
            # instead of asking it to eyeball damage zones/severity from scratch.
            from damage_detector import detect_damage
            # CPU-bound YOLO inference — off the event loop for the same
            # reason as the Ollama calls below (see their comments).
            detections = await asyncio.to_thread(detect_damage, image_data)

            # ── Low-confidence recovery ──────────────────────────────────────
            # A wide scene photo (damaged vehicle small in frame, further
            # away) can leave every real detection just under the normal
            # threshold -- confirmed on a live test claim (moderate-
            # deformation at 22.7%, cutoff 25%). Rather than lower the
            # threshold globally (which would flood ordinary close-up photos
            # with false positives), retry at a much lower threshold only
            # when the normal pass found nothing, and have the vision LLM
            # zoom into and confirm/reject each candidate before trusting it.
            if not detections:
                import part_identifier
                weak_candidates = await asyncio.to_thread(detect_damage, image_data, 0.08)
                weak_candidates = sorted(weak_candidates, key=lambda d: d["confidence"], reverse=True)[:3]
                for candidate in weak_candidates:
                    verified = await part_identifier.verify_and_identify_low_confidence_region(
                        image_data, candidate.get("bbox")
                    )
                    if verified:
                        detections.append({
                            "class": "generic-damage",
                            "confidence": candidate["confidence"],
                            "bbox": candidate.get("bbox"),
                            "vision_confirmed_part": verified["part"],
                            "vision_confirmed_description": verified.get("damage_description", ""),
                        })
                if detections:
                    logger.info(f"Low-confidence recovery found {len(detections)} vision-confirmed detection(s) for {filename}")

            if detections:
                detected_damage_section = "DETECTED DAMAGE (from trained CV model — treat as ground truth unless the photo clearly contradicts it):\n" + "\n".join(
                    f"- {d['class']} (confidence: {d['confidence']:.2f})" for d in detections
                )
            else:
                detected_damage_section = "DETECTED DAMAGE (from trained CV model): none detected above confidence threshold."

            # Build narrative cross-reference section if narrative is available
            narrative_section = f"""
NARRATIVE CLAIM — CROSS-REFERENCE AGAINST THE IMAGE:
"{narrative_context}"
 
For each item below, compare what is CLAIMED in the narrative against what is VISIBLE in the image:
- Damage zones: narrative claims specific parts were damaged — are they all visibly damaged in the photo?
- Weather and lighting: does the image match the described conditions (time of day, rain, visibility)?
- Airbag deployment: if claimed, are deployed airbags visible in the image?
- Damage severity: does the image severity match the narrative description?
- Scene consistency: does the overall scene match the described location and circumstances?
""" if narrative_context.strip() else ""
 
            prompt = f"""
You are a senior forensic vehicle damage analyst and fraud investigator for a Kenyan motor insurer.
Analyse this photo submitted by {party.upper()} for a motor insurance claim.

{detected_damage_section}

{narrative_section}

Return ONLY valid JSON — no text outside the JSON block.
 
{{
    "ai_generation_check": {{
        "appears_ai_generated": true/false,
        "ai_confidence": 0-100,
        "ai_indicators": ["list specific visual signals suggesting AI generation, empty list if none"],
        "reasoning": "one sentence"
    }},
    "scene_authenticity": {{
        "appears_genuine_accident": true/false,
        "photographer_shadow_or_reflection_visible": true/false,
        "motion_blur_or_natural_noise_present": true/false,
        "scene_looks_staged_or_too_clean": true/false,
        "background_looks_rendered_or_stock": true/false,
        "reasoning": "one sentence"
    }},
    "damage_assessment": {{
        "damage_visible": true/false,
        "damage_zones_visible": ["list zones actually visible in photo"],
        "damage_severity": "none/minor/moderate/severe/total_loss",
        "damage_appears_physically_deformed": true/false,
        "airbag_deployment_visible": true/false,
        "damage_description": "one sentence factual description"
    }},
    "narrative_cross_reference": {{
        "narrative_provided": {"true" if narrative_context.strip() else "false"},
        "damage_zones_match_narrative": true/false,
        "weather_conditions_match_narrative": true/false,
        "lighting_matches_claimed_time": true/false,
        "airbag_claim_consistent_with_image": true/false,
        "damage_severity_matches_narrative": true/false,
        "discrepancies": ["list any specific contradictions between image and narrative"]
    }},
    "photo_quality": {{
        "lighting": "poor/adequate/good",
        "angle": "poor/adequate/good",
        "clarity": "poor/adequate/good",
        "manipulation_detected": true/false,
        "appropriate_for_{party}": true/false
    }},
    "fraud_indicators": [
        {{
            "type": "ai_generated/staged_scene/damage_inconsistency/narrative_mismatch/weather_mismatch/manipulation/other",
            "severity": "low/medium/high/critical",
            "description": "specific finding with reason why it is suspicious",
            "confidence": 0-100
        }}
    ],
    "risk_assessment": {{
        "overall_risk_score": 0-100,
        "primary_concerns": ["list of main issues"],
        "verification_needed": ["suggested verification steps"]
    }},
    "kenyan_context": {{
        "typical_damage_pattern": true/false,
        "road_condition_consistent": true/false,
        "vehicle_type_appropriate": true/false
    }}
}}
 
AI generation indicators to check:
- Unnaturally perfect lighting for an accident scene
- No photographer shadow, hand, or reflection visible anywhere
- No motion blur, rain drops on lens, dust, or sensor noise
- Perfectly positioned vehicle with no crowd, bystanders, or emergency responders
- Background looks like a stock image, CGI render, or suspiciously clean
- Damage looks digitally overlaid rather than physically deformed crumpled metal
- Hyper-realistic textures too perfect for a rushed accident photo
 
{"Member photos may be taken quickly from awkward angles — that is normal." if party == "member" else ""}
{"Assessor photos should be professional, well-lit, from multiple angles." if party == "assessor" else ""}
{"Repair shop photos should show damage detail and repair areas clearly." if party == "repair_shop" else ""}
"""
 
            logger.info("Sending request to Ollama...")
            from ollama_client import generate, OllamaError
            # generate() uses the synchronous `requests` library — run it in a
            # worker thread so its blocking network I/O doesn't freeze the
            # single-threaded asyncio event loop (which would stall every
            # other request on the server, not just this one, for the full
            # duration of the Ollama call — sometimes minutes under load).
            try:
                response_text = await asyncio.to_thread(
                    generate, prompt, model=self.VISION_MODEL, images=[image_data], json_mode=True, timeout=120
                )
                logger.info("Ollama response received")
                parsed_result = self._parse_llm_response(response_text, filename)
            except OllamaError as e:
                # The vision LLM is advisory reasoning on top of the trained
                # CV model, not the source of the detections themselves --
                # `detections` above already came from our own YOLO model and
                # is real, regardless of whether Ollama is reachable. Losing
                # it here (previously: re-raising, which sent callers to a
                # filename-string/random-coin-flip stub with no relation to
                # the actual photo) threw away a working, trained result to
                # fall back to a strictly worse one.
                logger.warning(f"Ollama vision call failed ({e}), using CV-only detections for {filename}")
                parsed_result = self._cv_only_analysis(detections)

            parsed_result["detections"] = detections

            for anomaly in parsed_result.get('anomalies', []):
                anomaly['party'] = party

            logger.info(f"LLM analysis results for {filename} ({party}):")
            logger.info(f"Anomalies found: {len(parsed_result.get('anomalies', []))}")
            logger.info(f"Risk score: {parsed_result.get('risk_score', 0)}")

            return parsed_result

        except Exception as e:
            logger.error(f"LLM analysis failed for {filename}: {str(e)}")
            raise

    # Heuristic bucketing of the 30-class merged_v2_yolov8n_seg taxonomy into
    # rough severity tiers, for when there's no vision LLM available to give
    # a qualitative severity read -- refine if a specific class turns out to
    # be miscategorized in practice.
    _CV_HIGH_SEVERITY_CLASSES = {
        "severe-deformation", "detachment", "wreck-total-loss", "generic-damage",
    }
    _CV_LOW_SEVERITY_CLASSES = {
        "minor-deformation", "scratches", "paint-chips", "car-part-crack",
        "glass-crack-generic", "lamp-damage-generic", "glass-crack-windscreen-front",
        "glass-crack-windscreen-rear", "glass-crack-window", "headlight-damage",
        "taillight-damage", "signlight-damage", "side-mirror-crack",
    }
    # Everything else (moderate-deformation, generic-dent, the part-specific
    # dents, flat-tire, wheel-damage-generic, trunk-damage, ...) -> medium.

    def _cv_severity_from_detections(self, detections: List[Dict[str, Any]]) -> Optional[str]:
        """
        Trained-model-only severity read (low/medium/high) from raw YOLO
        detections, regardless of whether the vision LLM ran. Used as the
        independent anchor for cross-checking a party's claimed crush depth,
        since it can't be talked into a different answer via prompt/narrative.
        """
        if not detections:
            return None
        tiers = set()
        for d in detections:
            cls = d.get("class")
            if cls in self._CV_HIGH_SEVERITY_CLASSES:
                tiers.add("high")
            elif cls in self._CV_LOW_SEVERITY_CLASSES:
                tiers.add("low")
            else:
                tiers.add("medium")
        if "high" in tiers:
            return "high"
        if "medium" in tiers:
            return "medium"
        return "low"

    def _cv_only_analysis(self, detections: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Builds an anomalies/risk_score result straight from the trained YOLO
        model's detections, in the same shape _parse_llm_response produces --
        used when the vision LLM call fails, so a broken/misconfigured Ollama
        model degrades to "real CV detections, no qualitative cross-check"
        rather than to unrelated dummy output.
        """
        severity_scores = {"high": 25, "medium": 15, "low": 5}
        anomalies = []
        risk_score = 0

        for d in detections:
            cls = d["class"]
            if cls in self._CV_HIGH_SEVERITY_CLASSES:
                severity = "high"
            elif cls in self._CV_LOW_SEVERITY_CLASSES:
                severity = "low"
            else:
                severity = "medium"

            anomalies.append({
                "type": "cv_detected_damage",
                "severity": severity,
                "description": f"Trained CV model detected {cls} (confidence {d['confidence']:.2f})",
                "confidence": round(d["confidence"] * 100),
            })
            risk_score += severity_scores[severity]

        return {
            "anomalies": anomalies,
            "risk_score": min(risk_score, 100),
            "analysis_mode": "cv_only_fallback",
        }

    def _parse_llm_response(self, response_text: str, filename: str) -> Dict[str, Any]:
        """Parse the LLM's response and convert to anomalies format"""
        
        try:
            # Try to extract JSON from response
            json_start = response_text.find('{')
            json_end = response_text.rfind('}') + 1
            
            if json_start >= 0 and json_end > json_start:
                json_str = response_text[json_start:json_end]
                gemini_analysis = json.loads(json_str)
                
                anomalies = []
                risk_score = 0
                
                # Convert Gemini analysis to our anomaly format
                damage = gemini_analysis.get("damage_assessment", {})
                if not damage.get("appears_genuine", True):
                    anomalies.append({
                        "type": "suspicious_damage_pattern",
                        "severity": "high",
                        "description": f"Damage appears potentially staged: {damage.get('description', '')}",
                        "confidence": 85
                    })
                    risk_score += 30
                
                # Process fraud indicators
                for indicator in gemini_analysis.get("fraud_indicators", []):
                    anomalies.append({
                        "type": f"ai_detected_{indicator.get('type', 'unknown')}",
                        "severity": indicator.get("severity", "medium"),
                        "description": indicator.get("description", "AI detected potential issue"),
                        "confidence": indicator.get("confidence", 70)
                    })
                    severity_scores = {"high": 25, "medium": 15, "low": 5}
                    risk_score += severity_scores.get(indicator.get("severity", "medium"), 15)
                
                # Check photo quality issues
                photo_quality = gemini_analysis.get("photo_quality", {})
                if photo_quality.get("manipulation_detected", False):
                    anomalies.append({
                        "type": "photo_manipulation",
                        "severity": "high",
                        "description": "AI detected signs of photo manipulation",
                        "confidence": 80
                    })
                    risk_score += 35
                
                # Check scene analysis
                scene = gemini_analysis.get("scene_analysis", {})
                if scene.get("appears_staged", False):
                    anomalies.append({
                        "type": "staged_scene",
                        "severity": "high",
                        "description": "Scene appears to be potentially staged",
                        "confidence": 85
                    })
                    risk_score += 30
                
                # Use overall risk score from Gemini if available
                risk_assessment = gemini_analysis.get("risk_assessment", {})
                gemini_risk_score = risk_assessment.get("overall_risk_score", 0)
                final_risk_score = max(risk_score, gemini_risk_score)
                
                return {
                    "anomalies": anomalies,
                    "risk_score": min(final_risk_score, 100),
                    "gemini_analysis": gemini_analysis
                }
            
        except Exception as e:
            logger.error(f"Error parsing Gemini response for {filename}: {str(e)}")
        
        # Fallback parsing if JSON parsing fails
        return self._parse_gemini_text_fallback(response_text, filename)
    
    def _parse_gemini_text_fallback(self, response_text: str, filename: str) -> Dict[str, Any]:
        """Fallback text parsing when JSON extraction fails"""
        
        anomalies = []
        risk_score = 20  # Base risk for failed parsing
        
        text_lower = response_text.lower()
        
        # Look for key risk indicators in text
        high_risk_terms = ['staged', 'suspicious', 'manipulated', 'fraudulent', 'fake', 'inconsistent']
        medium_risk_terms = ['concerning', 'unusual', 'questionable', 'unclear', 'investigate']
        
        high_risk_found = [term for term in high_risk_terms if term in text_lower]
        medium_risk_found = [term for term in medium_risk_terms if term in text_lower]
        
        if high_risk_found:
            anomalies.append({
                "type": "ai_detected_risk",
                "severity": "high", 
                "description": f"AI analysis flagged potential issue related to: {', '.join(high_risk_found)}",
                "confidence": 70
            })
            risk_score += 25 * len(high_risk_found)
        
        if medium_risk_found:
            anomalies.append({
                "type": "ai_detected_concern",
                "severity": "medium",
                "description": f"AI analysis noted concerns: {', '.join(medium_risk_found)}",
                "confidence": 60
            })
            risk_score += 15 * len(medium_risk_found)
        
        if not anomalies:
            anomalies.append({
                "type": "ai_analysis_incomplete",
                "severity": "low",
                "description": "AI analysis completed but response format unclear - manual review recommended",
                "confidence": 50
            })
        
        return {
            "anomalies": anomalies,
            "risk_score": min(risk_score, 100)
        }
    
    async def _fallback_damage_analysis(self, image: Image.Image, filename: str, party: str = "member") -> Dict[str, Any]:
        """Fallback analysis when the LLM is not available"""
        
        anomalies = []
        risk_score = 0
        
        filename_lower = filename.lower()
        
        # Basic filename analysis for damage consistency
        if "front" in filename_lower and random.random() < 0.3:
            anomalies.append({
                "type": "damage_inconsistency",
                "severity": "medium",
                "description": "Front damage photo may show inconsistent damage patterns",
                "confidence": 65
            })
            risk_score += 20
        elif "rear" in filename_lower and random.random() < 0.25:
            anomalies.append({
                "type": "damage_inconsistency", 
                "severity": "medium",
                "description": "Rear damage incompatible with claimed impact type",
                "confidence": 70
            })
            risk_score += 20
        
        return {
            "anomalies": anomalies,
            "risk_score": risk_score
        }
    
    def _detect_potential_editing(self, image_data: bytes, filename: str) -> bool:
        """Detect potential image editing"""
        
        file_size = len(image_data)
        
        # Very small files might be heavily compressed/edited
        if file_size < 50000:  # Less than 50KB
            logger.warning(f"Suspiciously small file size: {file_size:,} bytes < 50KB")
            return True
        
        # Very large files might be uncompressed/edited
        if file_size > 10000000:  # Greater than 10MB
            logger.warning(f"Suspiciously large file size: {file_size:,} bytes > 10MB")
            return True
            
        # Check filename for editing indicators
        editing_indicators = ["edited", "modified", "copy", "new", "final", "v2", "revised"]
        filename_lower = filename.lower()
        
        found_indicators = [indicator for indicator in editing_indicators if indicator in filename_lower]
        
        if found_indicators:
            logger.warning(f"Editing indicators found in filename: {found_indicators}")
            return True
        
        return False
    
    def _check_temporal_consistency(self, timestamp: str) -> Optional[str]:
        """Check timestamp consistency"""
        
        try:
            # Parse timestamp and check if it's reasonable
            photo_time = datetime.strptime(timestamp, "%Y:%m:%d %H:%M:%S")
            now = datetime.now()
            
            time_diff = now - photo_time
            
            # Photo from future
            if photo_time > now:
                return "Photo timestamp is in the future"
            
            # Photo too old (more than 90 days for motor claims)
            if time_diff.days > 90:
                return "Photo timestamp indicates very old incident (>90 days)"
            
            # Photo too recent (less than 1 hour might indicate staging)
            if time_diff.total_seconds() < 3600:
                return "Photo taken very recently - verify incident timing"
                
        except Exception as e:
            logger.error(f"Failed to parse timestamp'{timestamp}': {str(e)}")
            return "Invalid or corrupted timestamp data"
        
        return None
    
    def _get_error_response(self, filename: str, error: str) -> PhotoAnomalySchema:
        """Return error response"""
        
        return PhotoAnomalySchema(
            filename=filename,
            hash="error",
            anomalies=[{
                "type": "analysis_error",
                "severity": "medium",
                "description": f"Photo analysis failed: {error}",
                "confidence": 0
            }],
            risk_score=50,  # Medium risk when analysis fails
            analysis_confidence=0
        )

class NarrativeAnalysisService:
    """Enhanced LLM-powered narrative analysis service using Gemini with database integration"""
    
    def __init__(self, gemini_api_key: str = None):
        self.text_processor = TextProcessor()
        
        # Initialize Gemini with the provided API key
        api_key = gemini_api_key or GEMINI_API_KEY
        
        try:
            logger.info("Configuring Gemini API for narrative analysis")
            genai.configure(api_key=api_key)
            self.gemini_model = genai.GenerativeModel('gemini-2.5-flash')
            logger.info("Gemini model initialized for narrative analysis")
        except Exception as e:
            logger.error(f"Failed to initialize Gemini for narrative analysis: {str(e)}")
            self.gemini_model = None
    
    async def analyze_narrative(self, narrative: str, claim_id: str, party: str = "member") -> NarrativeAnalysisSchema:
        """Comprehensive narrative analysis using the self-hosted LLM, with database logging"""

        logger.info(f"Analyzing narrative from {party} for claim {claim_id}")

        try:
            llm_analysis = await self._analyze_narrative_with_gemini(narrative, claim_id, party)
            result = self._convert_gemini_to_schema(llm_analysis, narrative, party)
            
            return result
                
        except Exception as e:
            logger.error(f"Error in narrative analysis: {str(e)}")
            return self._get_default_narrative_analysis(party)
    

    async def _analyze_narrative_with_gemini(
        self, narrative: str, claim_id: str, party: str = "member"
    ) -> Dict[str, Any]:
        """Analyze narrative using the self-hosted gemma4:26b model"""
        from datetime import date

        logger.info(f"Narrative analysis for {party} (model={TEXT_REASONING_MODEL})")
    
        prompt = f"""
    SYSTEM CONTEXT — READ FIRST:
    The current date is {date.today().strftime('%d %B %Y')} ({date.today().year}).
    You are operating as an investigator in {date.today().year}.
    Any incident date in {date.today().year} or earlier is historically valid.
    Do NOT flag {date.today().year} dates as future dates under any circumstances.
    
    You are a senior motor vehicle insurance fraud investigator specialising in the Kenyan market.
    You have investigated hundreds of staged accidents in Nairobi, Mombasa, and surrounding areas.
    
    Analyse this {party.upper()} narrative for fraud indicators using your Kenya-specific knowledge.
    
    NARRATIVE: "{narrative}"
    
    {"- Member narratives may be emotional and less structured — that is normal." if party == "member" else ""}
    {"- Assessor reports should be professional, technical, and objective." if party == "assessor" else ""}
    {"- Repair shop estimates should be detailed and focused on repairs." if party == "repair_shop" else ""}
    
    KENYA-SPECIFIC FRAUD PATTERNS — check each one explicitly:
    1. Third-party vehicle fled scene but claimant recorded full plate number — pre-arranged accident indicator
    2. Precise witness name and phone number provided in chaotic hit-and-run — suspiciously complete detail
    3. Airbag deployment claimed alongside only minor injuries (cuts, bruises) — force inconsistency
    4. Multiple separate damage zones (front + side + rear) from a single impact — staging indicator
    5. Heavy rain + night + poor visibility but precise third-party vehicle details recorded — physically implausible
    6. Police OB number provided same day for a hit-and-run — suggests pre-arranged documentation
    7. Trailer or matatu involved as third party — common vehicle type in Kenya staged accident rings
    8. Incident on Mombasa Road, Thika Road, Ngong Road, Eastern Bypass — high-fraud corridors
    9. Claimant treated at private hospital immediately with no mention of ambulance — cost inflation
    10. Third-party driver fled before police arrived — prevents independent witness verification
    
    Return ONLY valid JSON — no text outside the JSON block:
    {{
        "credibility_assessment": {{
            "overall_credibility_score": 0-100,
            "credibility_level": "very_low/low/medium/high/very_high",
            "appropriate_for_{party}": true/false
        }},
        "fraud_risk_analysis": {{
            "fraud_risk_score": 0-100,
            "risk_level": "low/medium/high"
        }},
        "extracted_information": {{
            "incident_details": {{
                "impact_type": "rear/front/side/rollover/multiple/unknown",
                "weather_conditions": "clear/rainy/foggy/windy/unknown"
            }},
            "vehicles": {{
                "v1_make": "claimant vehicle brand e.g. Toyota — null if not stated",
                "v1_model": "e.g. Fielder, Corolla, Probox — null if not stated",
                "v1_body_type": "sedan/suv/pickup/matatu/bus/truck/motorcycle/unknown",
                "v1_stated_speed_kmh": "number explicitly stated in narrative — null if not stated, do NOT estimate",
                "v1_stationary": "true if narrative states vehicle was parked or completely stopped at time of impact — false otherwise",
                "v2_make": "third-party vehicle brand — null if not stated",
                "v2_model": "null if not stated",
                "v2_body_type": "sedan/suv/pickup/matatu/bus/truck/motorcycle/unknown",
                "v2_stated_speed_kmh": "number explicitly stated for third-party — null if not stated",
                "v2_stationary": "true if third-party was stationary at impact — false otherwise"
            }},
            "damage_physics": {{
                "crush_depth_mm": "number or null — infer ONLY from explicit damage descriptions using these benchmarks: scratch/scuff=5, small_dent=20, dent=35, minor_damage=50, moderate_damage=80, significant_damage=120, severe_damage=160, significant_crumpling=200, cannot_open_boot_or_door=220, structural_damage=280, write_off_or_total_loss=380, airbags_deployed_alone=180 — null if damage is not described at all",
                "approach_angle_deg": "0=direct rear-end claimant stationary, 45=angled rear, 90=perpendicular T-bone, 135=angled head-on, 180=direct head-on — null if unclear",
                "primary_impact_zone": "front/rear/side-left/side-right/roof/multiple/unknown",
                "airbag_deployed": "true if narrative explicitly states airbags deployed, false if explicitly states they did NOT deploy, null if not mentioned"
            }}
        }},
        "inconsistency_analysis": {{
            "major_inconsistencies": [
                {{
                    "type": "temporal/logical/factual/physical/kenya_fraud_pattern",
                    "description": "specific inconsistency with explanation of why it is suspicious",
                    "severity": "minor/moderate/major"
                }}
            ]
        }},
        "kenya_fraud_signals": [
            {{
                "pattern_number": 1-10,
                "pattern": "name of fraud pattern",
                "detected": true/false,
                "detail": "specific evidence from the narrative",
                "severity": "low/medium/high/critical"
            }}
        ],
        "party_consistency": {{
            "tone_appropriate": true/false,
            "detail_level_appropriate": true/false,
            "professional_language": true/false
        }}
    }}
    """
    
        try:
            parsed = await asyncio.to_thread(
                generate_json, prompt, model=TEXT_REASONING_MODEL, retries=1, timeout=120,
            )
            parsed["party"] = party
            return parsed
        except Exception as e:
            logger.error(f"Narrative analysis ({TEXT_REASONING_MODEL}) failed: {str(e)}")
            # Re-raise rather than returning a fallback here: this function's
            # contract is to return a raw dict (fed into _convert_gemini_to_schema,
            # which calls .get() on it) -- the caller's own except clause
            # already handles this by calling _get_default_narrative_analysis,
            # which correctly returns a NarrativeAnalysisSchema object directly.
            # Returning a Schema object from here instead used to crash with
            # "'NarrativeAnalysisSchema' object has no attribute 'get'" on
            # every Ollama failure.
            raise
        
    def _parse_gemini_narrative_response(self, response_text: str) -> Dict[str, Any]:
        """Parse Gemini narrative analysis response"""
        try:
            # Extract JSON from response
            json_start = response_text.find('{')
            json_end = response_text.rfind('}') + 1
            
            if json_start >= 0 and json_end > json_start:
                json_str = response_text[json_start:json_end]
                return json.loads(json_str)
        except Exception as e:
            logger.error(f"Error parsing Gemini narrative response: {str(e)}")
        
        # Fallback text analysis
        return self._parse_narrative_text_fallback(response_text)
    
    def _parse_narrative_text_fallback(self, response_text: str) -> Dict[str, Any]:
        """Fallback parsing for narrative analysis"""
        text_lower = response_text.lower()
        
        # Basic risk assessment from text
        risk_score = 30  # Base risk
        
        if any(term in text_lower for term in ['high risk', 'suspicious', 'fraudulent', 'inconsistent']):
            risk_score += 30
        elif any(term in text_lower for term in ['medium risk', 'concerning', 'questionable']):
            risk_score += 20
        elif any(term in text_lower for term in ['low risk', 'credible', 'consistent']):
            risk_score -= 10
        
        return {
            "fraud_risk_analysis": {
                "fraud_risk_score": min(max(risk_score, 0), 100),
                "risk_level": "high" if risk_score > 70 else "medium" if risk_score > 40 else "low"
            },
            "credibility_assessment": {
                "overall_credibility_score": max(100 - risk_score, 0),
                "credibility_level": "low" if risk_score > 70 else "medium" if risk_score > 40 else "high"
            },
            "extracted_information": {
                "incident_details": {"impact_type": "unknown"}
            },
            "inconsistency_analysis": {
                "major_inconsistencies": [{"type": "parsing", "description": "Analysis format unclear"}]
            }
        }
    
    def _convert_gemini_to_schema(
        self, gemini_analysis: Dict[str, Any], narrative: str, party: str = "member"
    ) -> NarrativeAnalysisSchema:
        """
        Convert raw Gemini JSON response into NarrativeAnalysisSchema.
        Maps the extended extracted_information fields (vehicles, damage_physics)
        into extracted_data so the orchestrator and physics engine can consume them.
        """
        extracted_info = gemini_analysis.get("extracted_information", {})
        incident       = extracted_info.get("incident_details", {})
        vehicles       = extracted_info.get("vehicles", {})
        damage         = extracted_info.get("damage_physics", {})
    
        credibility   = gemini_analysis.get("credibility_assessment", {})
        inconsistency = gemini_analysis.get("inconsistency_analysis", {})
        kenya_signals = gemini_analysis.get("kenya_fraud_signals", [])
    
        credibility_score = int(credibility.get("overall_credibility_score", 50))
    
        # Build extracted_data — includes the new vehicles + damage_physics blocks
        # that _run_physics_reconstruction reads via narrative_analysis.extracted_data
        extracted_data = {
            # Original fields kept for backward compatibility
            "impact_type":        incident.get("impact_type"),
            "weather_conditions": incident.get("weather_conditions"),
            # New physics fields
            "vehicles": {
                "v1_make":             vehicles.get("v1_make"),
                "v1_model":            vehicles.get("v1_model"),
                "v1_body_type":        vehicles.get("v1_body_type"),
                "v1_stated_speed_kmh": vehicles.get("v1_stated_speed_kmh"),
                "v1_stationary":       vehicles.get("v1_stationary", False),
                "v2_make":             vehicles.get("v2_make"),
                "v2_model":            vehicles.get("v2_model"),
                "v2_body_type":        vehicles.get("v2_body_type"),
                "v2_stated_speed_kmh": vehicles.get("v2_stated_speed_kmh"),
                "v2_stationary":       vehicles.get("v2_stationary", False),
            },
            "damage_physics": {
                "crush_depth_mm":      damage.get("crush_depth_mm"),
                "approach_angle_deg":  damage.get("approach_angle_deg"),
                "primary_impact_zone": damage.get("primary_impact_zone"),
                "airbag_deployed":     damage.get("airbag_deployed"),
            },
        }
    
        # Build inconsistencies — same format your original used
        converted_inconsistencies = []
        for inc in inconsistency.get("major_inconsistencies", []):
            converted_inconsistencies.append({
                "type":        inc.get("type", "unknown"),
                "severity":    inc.get("severity", "medium"),
                "description": inc.get("description", "Inconsistency detected"),
                "confidence":  75,
                "party":       party,
            })
    
        # Promote detected Kenya fraud signals into inconsistencies
        for signal in kenya_signals:
            if signal.get("detected"):
                converted_inconsistencies.append({
                    "type":        "kenya_fraud_pattern",
                    "severity":    signal.get("severity", "medium"),
                    "description": (
                        f"Pattern {signal.get('pattern_number')}: "
                        f"{signal.get('pattern')} — {signal.get('detail')}"
                    ),
                    "confidence":  85,
                    "party":       party,
                })
    
        # Log extraction coverage so you can verify the new fields are coming through
        extracted_fields = [
            k for k, v in {
                "v1_make":       vehicles.get("v1_make"),
                "v1_speed":      vehicles.get("v1_stated_speed_kmh"),
                "v1_stationary": vehicles.get("v1_stationary"),
                "v2_make":       vehicles.get("v2_make"),
                "crush_depth":   damage.get("crush_depth_mm"),
                "angle":         damage.get("approach_angle_deg"),
                "airbag":        damage.get("airbag_deployed"),
            }.items() if v is not None
        ]
        logger.info(
            f"Narrative extraction coverage ({party}): "
            f"{len(extracted_fields)}/7 physics fields extracted — {extracted_fields}"
        )
    
        # Constructor matches your existing NarrativeAnalysisSchema signature exactly
        return NarrativeAnalysisSchema(
            extracted_data=extracted_data,
            inconsistencies=converted_inconsistencies,
            narrative_quality_score=70.0,
            key_entities=[],
            sentiment="neutral",
            credibility_score=credibility_score,
            gemini_analysis=gemini_analysis,
        )
    
    def _get_default_narrative_analysis(self, party: str = "member") -> NarrativeAnalysisSchema:
        """Return default analysis on error"""
        return NarrativeAnalysisSchema(
            extracted_data={"impact_type": "unknown"},
            inconsistencies=[],
            narrative_quality_score=50,
            key_entities=[],
            sentiment="neutral"
        )

class RiskScoringService:
    """Enhanced ML-powered risk scoring service with AI-generated insights and database integration"""
    
    def __init__(self, gemini_api_key: str = None):
        self.model_version = "1.1"
        self.metrics_calculator = MetricsCalculator()
        
        # Initialize Gemini for AI-generated recommendations
        api_key = gemini_api_key or GEMINI_API_KEY
        
        try:
            logger.info("Configuring Gemini API for risk scoring")
            genai.configure(api_key=api_key)
            self.gemini_model = genai.GenerativeModel('gemini-2.5-flash')
            logger.info("Gemini model initialized for risk scoring")
        except Exception as e:
            logger.error(f"Failed to initialize Gemini for risk scoring: {str(e)}")
            self.gemini_model = None
    
    @log_performance
    async def calculate_risk_score(
        self,
        photo_scores: List[int],
        narrative_analysis: NarrativeAnalysisSchema,
        claim_amount: float,
        location: str,
        historical_data: Optional[Dict] = None,
        claim_id: str = "unknown",
        business_rules_score: Optional[int] = None
    ) -> RiskScoringSchema:
        """Calculate comprehensive fraud risk score with database integration"""
        try:
            enhanced_historical_data = await self._get_enhanced_historical_data(
                location, claim_amount, claim_id
            )
            if historical_data:
                enhanced_historical_data.update(historical_data)

            # ── AI-ROL: Operational Benchmarking recommendation (Appendix C §C.2) ──
            if enhanced_historical_data.get("benchmark_observation"):
                ai_rol.record_recommendation(
                    claim_id=claim_id,
                    capability="historical_benchmarking",
                    recommendation=enhanced_historical_data["benchmark_observation"],
                    confidence=None,
                    evidence={
                        "cost_percentile": enhanced_historical_data.get("cost_percentile"),
                        "population_avg_cost": enhanced_historical_data.get("population_avg_cost"),
                        "population_size": enhanced_historical_data.get("population_size"),
                        "recent_claims_same_location": enhanced_historical_data.get("recent_claims"),
                        "fraud_flags_same_location": enhanced_historical_data.get("fraud_flags"),
                    },
                )

            # Component scores
            photo_score      = self._calculate_photo_component_score(photo_scores)
            narrative_score  = self._calculate_narrative_component_score(narrative_analysis)
            amount_score     = self._calculate_amount_component_score(claim_amount)
            location_score   = self._calculate_location_component_score(location)
            historical_score = self._calculate_historical_component_score(enhanced_historical_data)
            physics_score, physics_verdict = await self._get_physics_data(claim_id)
    
            # ── Dynamic photo weight boost ────────────────────────────────────────
            if photo_score >= 80:
                photo_boost = 0.20
            elif photo_score >= 60:
                photo_boost = 0.10
            else:
                photo_boost = 0.0
    
            # ── Build weights ─────────────────────────────────────────────────────
            if physics_score > 0:
                raw_weights = {
                    "photo":      0.20 + photo_boost,
                    "narrative":  max(0.10, 0.20 - photo_boost * 0.5),
                    "amount":     max(0.08, 0.15 - photo_boost * 0.3),
                    "location":   0.10,
                    "historical": 0.05,
                    "physics":    0.30,
                }
                logger.info(
                    f"Physics score ({physics_score}/100) included in final risk for {claim_id}"
                )
            else:
                raw_weights = {
                    "photo":      0.35 + photo_boost,
                    "narrative":  max(0.10, 0.30 - photo_boost * 0.5),
                    "amount":     max(0.08, 0.20 - photo_boost * 0.3),
                    "location":   0.10,
                    "historical": 0.05,
                }

            # ── Business rules component (Appendix C rules engine) ─────────────────
            # Only added when the caller actually ran the rules engine -- renormalized
            # in with everything else below so it never changes the *shape* of the
            # weights, just makes room for a new evidence-backed signal.
            if business_rules_score is not None:
                raw_weights["business_rules"] = 0.20

            total   = sum(raw_weights.values())
            weights = {k: round(v / total, 4) for k, v in raw_weights.items()}
    
            if photo_boost > 0:
                logger.info(
                    f"Photo boost applied for {claim_id}: "
                    f"photo_score={photo_score} → photo_weight={weights['photo']:.0%}"
                )
    
            # ── Overall score ─────────────────────────────────────────────────────
            scores_map = {
                "photo":      photo_score,
                "narrative":  narrative_score,
                "amount":     amount_score,
                "location":   location_score,
                "historical": historical_score,
            }
            if physics_score > 0:
                scores_map["physics"] = physics_score
            if business_rules_score is not None:
                scores_map["business_rules"] = business_rules_score

            overall_score = int(sum(scores_map[k] * weights[k] for k in weights))
            overall_score = min(max(overall_score, 0), 100)
    
            # ── Risk level ────────────────────────────────────────────────────────
            if overall_score >= 75:
                risk_level = RiskLevel.HIGH
            elif overall_score >= 45:
                risk_level = RiskLevel.MEDIUM
            else:
                risk_level = RiskLevel.LOW
    
            # ── CHANGE A: Physics floor — level_order (only raises, never lowers) ─
            _level_order = {RiskLevel.LOW: 0, RiskLevel.MEDIUM: 1, RiskLevel.HIGH: 2}
    
            physics_implied = (
                RiskLevel.HIGH   if physics_score >= 85 else
                RiskLevel.MEDIUM if physics_score >= 70 else
                RiskLevel.LOW
            )
    
            if _level_order[physics_implied] > _level_order[risk_level]:
                logger.info(
                    f"Physics floor applied: {risk_level.value} → {physics_implied.value} "
                    f"(physics={physics_score}/100) for {claim_id}"
                )
                risk_level = physics_implied

            # ── Business rules floor — same pattern as physics: can only raise ─────
            if business_rules_score is not None:
                rules_implied = (
                    RiskLevel.HIGH   if business_rules_score >= 70 else
                    RiskLevel.MEDIUM if business_rules_score >= 40 else
                    RiskLevel.LOW
                )
                if _level_order[rules_implied] > _level_order[risk_level]:
                    logger.info(
                        f"Business rules floor applied: {risk_level.value} → {rules_implied.value} "
                        f"(business_rules={business_rules_score}/100) for {claim_id}"
                    )
                    risk_level = rules_implied

            # ── Recommendations ───────────────────────────────────────────────────
            recommendations = self._generate_recommendations(
                overall_score,
                {
                    "photo":      photo_score,
                    "narrative":  narrative_score,
                    "amount":     amount_score,
                    "location":   location_score,
                    "historical": historical_score,
                },
                physics_score=physics_score,
                physics_verdict=physics_verdict,
            )
    
            # CHANGE B: only flag physics in recommendations when signal is meaningful
            if physics_score >= 70 and physics_verdict in ("INCONSISTENT", "SUSPICIOUS"):
                recommendations.insert(
                    0,
                    f"Physics reconstruction flagged inconsistencies "
                    f"(score: {physics_score}/100, verdict: {physics_verdict})"
                )
    
            # ── Explanation ───────────────────────────────────────────────────────
            explanation = self._generate_explanation(overall_score, weights, scores_map)
    
            return RiskScoringSchema(
                overall_score=overall_score,
                risk_level=risk_level,
                component_scores={
                    "photo_analysis":         photo_score,
                    "narrative_analysis":     narrative_score,
                    "amount_based":           amount_score,
                    "location_based":         location_score,
                    "historical_patterns":    historical_score,
                    "physics_reconstruction": physics_score,
                    "business_rules":         business_rules_score if business_rules_score is not None else 0,
                },
                recommendations=recommendations,
                explanation=explanation,
                weights=weights,
            )
    
        except Exception as e:
            logger.error(f"Error in risk scoring: {str(e)}")
            return self._get_default_risk_scoring()
    
 
    async def _get_physics_data(self, claim_id: str) -> tuple[float, str]:
        """
        Pull physics fraud score and verdict from DB.
        Returns (0.0, 'CONSISTENT') if reconstruction has not been run.
        """
        try:
            with db_manager.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT physics_fraud_score, physics_verdict FROM claims WHERE claim_id = ?",
                    (claim_id,)
                )
                row = cursor.fetchone()
                if row and row["physics_fraud_score"] not in (None, 0):
                    return float(row["physics_fraud_score"]), (row["physics_verdict"] or "CONSISTENT")
            return 0.0, "CONSISTENT"
        except Exception as e:
            logger.error(f"Error fetching physics data for {claim_id}: {str(e)}")
            return 0.0, "CONSISTENT"

    
    async def _get_enhanced_historical_data(self, location: str, claim_amount: float, current_claim_id: str) -> Dict[str, Any]:
        """Get enhanced historical data from database, including population-level benchmarking."""
        try:
            historical_data = {"recent_claims": 0, "fraud_flags": 0, "location_risk_factor": 0}

            with db_manager.get_connection() as conn:
                cursor = conn.cursor()

                # Count recent high-risk claims in the same location
                cursor.execute('''
                    SELECT COUNT(*) FROM claims
                    WHERE location LIKE ?
                    AND fraud_risk_score >= 70
                    AND created_at >= datetime('now', '-30 days')
                    AND claim_id != ?
                ''', (f"%{location}%", current_claim_id))

                location_high_risk = cursor.fetchone()[0]
                historical_data["location_risk_factor"] = min(location_high_risk * 10, 50)

                # Count similar amount claims
                amount_range_low = claim_amount * 0.8
                amount_range_high = claim_amount * 1.2

                cursor.execute('''
                    SELECT COUNT(*) FROM claims
                    WHERE estimated_cost BETWEEN ? AND ?
                    AND fraud_risk_score >= 60
                    AND created_at >= datetime('now', '-60 days')
                    AND claim_id != ?
                ''', (amount_range_low, amount_range_high, current_claim_id))

                similar_amount_claims = cursor.fetchone()[0]
                historical_data["similar_amount_risk"] = min(similar_amount_claims * 5, 30)

                # ── recent_claims / fraud_flags were previously dead fields
                # (always 0 -- _calculate_historical_component_score reads
                # them but nothing ever set them). Now genuinely populated:
                # any claims at this location recently, and how many of
                # those were high-risk.
                cursor.execute('''
                    SELECT COUNT(*) FROM claims
                    WHERE location LIKE ?
                    AND created_at >= datetime('now', '-30 days')
                    AND claim_id != ?
                ''', (f"%{location}%", current_claim_id))
                historical_data["recent_claims"] = cursor.fetchone()[0]

                cursor.execute('''
                    SELECT COUNT(*) FROM claims
                    WHERE location LIKE ?
                    AND risk_level = 'high'
                    AND created_at >= datetime('now', '-90 days')
                    AND claim_id != ?
                ''', (f"%{location}%", current_claim_id))
                historical_data["fraud_flags"] = cursor.fetchone()[0]

                # ── Operational benchmarking (Appendix C §C.2) — where this
                # claim's cost sits relative to the whole claims population,
                # not just a 30/60-day location/amount window.
                cursor.execute('SELECT COUNT(*) as total, AVG(estimated_cost) as avg_cost FROM claims WHERE claim_id != ?', (current_claim_id,))
                pop_row = cursor.fetchone()
                total_claims = pop_row["total"] or 0
                avg_cost = pop_row["avg_cost"] or 0
                if total_claims > 0:
                    cursor.execute(
                        'SELECT COUNT(*) as cnt FROM claims WHERE estimated_cost <= ? AND claim_id != ?',
                        (claim_amount, current_claim_id),
                    )
                    below_or_equal = cursor.fetchone()["cnt"]
                    percentile = round(100 * below_or_equal / total_claims)
                    historical_data["cost_percentile"] = percentile
                    historical_data["population_avg_cost"] = round(avg_cost, 2)
                    historical_data["population_size"] = total_claims
                    historical_data["benchmark_observation"] = (
                        f"This claim's estimated cost (KES {claim_amount:,.0f}) is at the {percentile}th percentile "
                        f"of all {total_claims} claims on record (population average: KES {avg_cost:,.0f})."
                    )

            return historical_data

        except Exception as e:
            logger.error(f"Error getting enhanced historical data: {str(e)}")
            return {"recent_claims": 0, "fraud_flags": 0}
    
    def _calculate_photo_component_score(self, photo_scores: List[int]) -> float:
        """Calculate photo component of risk score"""
        if not photo_scores:
            return 0.0
        return sum(photo_scores) / len(photo_scores)
    
    def _calculate_narrative_component_score(self, narrative_analysis: NarrativeAnalysisSchema) -> float:
        """Calculate narrative component of risk score"""
        base_score = 0.0
        
        # Points for inconsistencies
        base_score += len(narrative_analysis.inconsistencies) * 20
        
        # Quality assessment
        quality_penalty = max(0, 80 - narrative_analysis.narrative_quality_score) * 0.5
        base_score += quality_penalty
        
        # Credibility assessment if available
        if hasattr(narrative_analysis, 'credibility_score'):
            credibility_penalty = max(0, 80 - narrative_analysis.credibility_score) * 0.6
            base_score += credibility_penalty
        
        # Sentiment-based adjustments
        if narrative_analysis.sentiment == "negative":
            base_score += 10
        
        return min(base_score, 100.0)
    
    def _calculate_amount_component_score(self, amount: float) -> float:
        """Calculate amount-based risk score"""
        if amount > 2000000:  # > 2M KES
            return 45.0
        elif amount > 1000000:  # > 1M KES
            return 35.0
        elif amount > 500000:  # > 500K KES
            return 25.0
        elif amount > 200000:  # > 200K KES
            return 15.0
        elif amount < 10000:   # Unusually low claims
            return 20.0
        else:
            return 5.0
    
    def _calculate_location_component_score(self, location: str) -> float:
        """Calculate location-based risk score"""
        high_risk_areas = ["eastleigh", "kayole", "dandora", "mathare"]
        medium_risk_areas = ["south b", "south c", "umoja", "embakasi"]
        
        location_lower = location.lower()
        
        for area in high_risk_areas:
            if area in location_lower:
                return 30.0
        
        for area in medium_risk_areas:
            if area in location_lower:
                return 15.0
        
        return 5.0
    
    def _calculate_historical_component_score(self, historical_data: Optional[Dict]) -> float:
        """Calculate historical pattern risk score with enhanced database data"""
        if not historical_data:
            return 0.0
        
        score = 0.0
        
        recent_claims = historical_data.get("recent_claims", 0)
        if recent_claims > 3:
            score += 25.0
        elif recent_claims > 1:
            score += 10.0
        
        previous_fraud_flags = historical_data.get("fraud_flags", 0)
        score += previous_fraud_flags * 15
        
        # Add enhanced factors from database
        location_risk = historical_data.get("location_risk_factor", 0)
        score += location_risk
        
        similar_amount_risk = historical_data.get("similar_amount_risk", 0)
        score += similar_amount_risk
        
        return min(score, 100.0)
    
    def _generate_recommendations(
        self,
        overall_score: int,
        component_scores: Dict[str, float],
        physics_score: float = 0.0,
        physics_verdict: str = "CONSISTENT",
    ) -> List[str]:
        """Generate recommendations based on risk score and physics verdict"""
        recommendations = []

        # ── Overall score based recommendations ──────────────────────────────
        if overall_score >= 75:
            recommendations.extend([
                "Immediate investigation required",
                "Do not process payment until investigation complete",
                "Verify all submitted documents independently",
                "Contact Kenya Police for incident verification",
            ])
        elif overall_score >= 50:
            recommendations.extend([
                "Enhanced verification required",
                "Request additional supporting documentation",
                "Verify incident details with independent sources",
            ])
        elif physics_verdict == "INCONSISTENT" or physics_score >= 45:
            # Physics overrode a low overall score — recommendations must reflect that
            recommendations.extend([
                "Do not approve — physics reconstruction flagged material inconsistencies",
                "Refer to Investigation Unit for independent crash reconstruction verification",
                "Obtain independent assessor measurement of actual crush depth",
                "Cross-check stated impact speed against vehicle damage records",
            ])
        elif physics_verdict == "SUSPICIOUS":
            recommendations.extend([
                "Approve only after mandatory assessor review",
                "Request independent verification of stated impact speed",
                "Assessor to measure and document actual crush depth on site",
            ])
        else:
            recommendations.append("Standard processing can proceed")

        # ── Component-level recommendations ───────────────────────────────────
        if component_scores.get("photo", 0) > 50:
            recommendations.append("Independent vehicle damage assessment required")

        if component_scores.get("narrative", 0) > 50:
            recommendations.append("Request detailed witness statements")

        if component_scores.get("amount", 0) > 40:
            recommendations.append("Verify repair estimates with approved assessors")

        return recommendations
    
    def _generate_explanation(
        self,
        overall_score: int,
        weights: Dict[str, float],
        scores: Dict[str, float]
    ) -> str:

        key_map = {
            "photo_analysis":         "photo",
            "narrative_analysis":     "narrative",
            "amount_based":           "amount",
            "location_based":         "location",
            "historical_patterns":    "historical",
            "physics_reconstruction": "physics",
        }

        components = []
        for component, score in scores.items():
            weight_key = key_map.get(component, component)
            weight = weights.get(weight_key, 0)
            if weight == 0:
                continue
            contribution = score * weight
            components.append(
                f"{component.replace('_', ' ').title()}: "
                f"{score:.1f} (weight: {weight:.0%}) = {contribution:.1f}"
            )

        explanation = f"Overall risk score of {overall_score}/100 calculated from: "
        explanation += "; ".join(components) if components else "component scores unavailable"

        if overall_score >= 75:
            explanation += ". HIGH RISK: Multiple fraud indicators detected."
        elif overall_score >= 45:
            explanation += ". MEDIUM RISK: Some concerning patterns identified."
        elif scores.get("physics_reconstruction", 0) >= 45 or scores.get("physics", 0) >= 45:
            explanation += ". LOW WEIGHTED SCORE — physics reconstruction has escalated this claim for investigation."
        else:
            explanation += ". LOW RISK: No significant fraud indicators."

        return explanation
    
    def _get_default_risk_scoring(self) -> RiskScoringSchema:
        """Return default scoring on error"""
        return RiskScoringSchema(
            overall_score=50,
            risk_level=RiskLevel.MEDIUM,
            component_scores={},
            recommendations=["Manual review required due to analysis error"],
            explanation="Risk scoring failed - manual assessment needed"
        )


class ClaimOrchestrator:
    """
    Enhanced orchestrator with multi-party support
    Handles member, assessor, and repair shop submissions
    """
    
    def __init__(self, gemini_api_key: str = None):
        api_key = gemini_api_key or GEMINI_API_KEY
        
        self.photo_service = PhotoAnalysisService()
        self.narrative_service = NarrativeAnalysisService(api_key)
        self.risk_service = RiskScoringService(api_key)
        
        logger.info("ClaimOrchestrator initialized with multi-party support")
    
    @log_performance
    async def analyze_claim(
        self,
        claim_id: str,
        narrative: str,
        photos: List[Tuple[bytes, str]],
        estimated_cost: float,
        location: str,
        historical_data: Optional[Dict] = None
    ) -> Dict[str, Any]:
        """
        Original single-party claim analysis (backward compatible)
        """
        return await self.analyze_multiparty_claim(
            claim_id=claim_id,
            member_narrative=narrative,
            member_photos=photos,
            estimated_cost=estimated_cost,
            location=location,
            historical_data=historical_data
        )
    
    @log_performance
    async def analyze_multiparty_claim(
        self,
        claim_id: str,
        member_narrative: str,
        member_photos: List[Tuple[bytes, str]],
        estimated_cost: float,
        location: str,
        assessor_report: Optional[str] = None,
        assessor_photos: Optional[List[Tuple[bytes, str]]] = None,
        repair_estimate: Optional[str] = None,
        repair_photos: Optional[List[Tuple[bytes, str]]] = None,
        historical_data: Optional[Dict] = None,
        assessor_crush_depth_mm: Optional[float] = None,
        assessor_approach_angle_deg: Optional[float] = None,
        assessor_id: Optional[str] = None,
        member_id: Optional[str] = None,
        policy_id: Optional[str] = None,
        claim_type: str = "motor",
        incident_details: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Multi-party claim analysis.
        Analyzes submissions from member, assessor, and repair shop.
        Physics reconstruction runs automatically after narrative analysis.
        """
        start_time = datetime.now()

        logger.info(f"Multi-party analysis for claim {claim_id}")
        logger.info(
            f"Parties: Member + "
            f"{'Assessor ' if assessor_report else ''}"
            f"{'RepairShop' if repair_estimate else ''}"
        )

        try:
            all_photos = []
            member_cv_severities = []
            assessor_cv_severities = []

            # Analyze member photos
            logger.info(f"Analyzing {len(member_photos)} member photos...")
            for photo_data, filename in member_photos[:3]:
                result = await self.photo_service.analyze_photo(
                    photo_data, filename, claim_id,
                    party="member",
                    narrative_context=member_narrative,
                )
                all_photos.append(result)
                if result.cv_severity:
                    member_cv_severities.append(result.cv_severity)
                await asyncio.sleep(2)

            # Analyze assessor photos
            if assessor_photos:
                logger.info(f"Analyzing {len(assessor_photos)} assessor photos...")
                for photo_data, filename in assessor_photos[:3]:
                    result = await self.photo_service.analyze_photo(
                        photo_data, filename, claim_id,
                        party="assessor",
                        narrative_context=assessor_report or "",
                    )
                    all_photos.append(result)
                    if result.cv_severity:
                        assessor_cv_severities.append(result.cv_severity)
                    await asyncio.sleep(2)

            _severity_rank = {"low": 1, "medium": 2, "high": 3}
            member_cv_severity = (
                max(member_cv_severities, key=lambda s: _severity_rank.get(s, 0))
                if member_cv_severities else None
            )
            assessor_cv_severity = (
                max(assessor_cv_severities, key=lambda s: _severity_rank.get(s, 0))
                if assessor_cv_severities else None
            )

            # AI's own independent repair-cost estimate from everything
            # detected so far (member + assessor photos) -- lets the
            # cost-reasonableness business rule flag a human-entered figure
            # that looks off, instead of trusting estimated_cost blindly.
            # Advisory only: a failed/empty estimate just means the rule
            # skips, never blocks the claim.
            ai_cost_estimate = None
            # Dedup by photo hash before merging damage zones -- the same
            # physical photo can legitimately end up analyzed twice under
            # different party labels (e.g. an assessor's upload happens to
            # be identical to one the member already submitted, or a test
            # artifact like this), and without this its damage gets counted
            # once per party label instead of once, inflating the estimate.
            seen_photo_hashes = set()
            deduped_photos = []
            for p in all_photos:
                if p.hash and p.hash in seen_photo_hashes:
                    continue
                if p.hash:
                    seen_photo_hashes.add(p.hash)
                deduped_photos.append(p)
            # Flat-concatenating zones across photos double(triple)-counts
            # cost whenever the same physical part shows up in more than one
            # photo -- normal and expected, since a claim's photo set is
            # usually several angles of ONE car, not one photo per part.
            # Keep one entry per (part, vehicle), preferring the
            # highest-confidence read of that part across all the photos.
            all_zones = [z for p in deduped_photos for z in (p.damage_zones or [])]
            best_zone_by_part: Dict[tuple, Dict[str, Any]] = {}
            for z in all_zones:
                key = ((z.get("part") or "").lower(), (z.get("vehicle") or "").lower())
                existing = best_zone_by_part.get(key)
                if existing is None or (z.get("confidence") or 0) > (existing.get("confidence") or 0):
                    best_zone_by_part[key] = z
            merged_damage_zones = list(best_zone_by_part.values())
            if merged_damage_zones:
                try:
                    import part_identifier

                    policy_vehicle_make, policy_vehicle_model, policy_vehicle_year = None, None, None
                    with db_manager.get_connection() as conn:
                        cursor = conn.cursor()
                        cursor.execute(
                            """
                            SELECT m.vehicle_make, m.vehicle_model, m.vehicle_year
                            FROM claims c
                            LEFT JOIN motor_policy_details m ON m.policy_id = c.policy_id
                            WHERE c.claim_id = ?
                            """,
                            (claim_id,)
                        )
                        row = cursor.fetchone()
                        if row:
                            policy_vehicle_make = row["vehicle_make"]
                            policy_vehicle_model = row["vehicle_model"]
                            policy_vehicle_year = row["vehicle_year"]

                    ai_cost_estimate = await part_identifier.estimate_damage_cost(
                        merged_damage_zones,
                        vehicle_make=policy_vehicle_make or "",
                        vehicle_model=policy_vehicle_model or "",
                        vehicle_year=str(policy_vehicle_year or ""),
                    )
                    if ai_cost_estimate:
                        logger.info(
                            f"AI damage cost estimate for {claim_id}: "
                            f"KES {ai_cost_estimate['estimated_cost_kes']:,.0f} "
                            f"(quoted: KES {estimated_cost:,.0f})"
                        )
                        # Written immediately (not deferred to the usual
                        # post-store_claim() persistence step, unlike
                        # simulation_video_path below) -- the
                        # cost_reasonableness business rule reads this
                        # straight from the DB via BusinessRulesEngine.evaluate()
                        # further down in this same function call, before
                        # store_claim() ever runs. store_claim()'s own
                        # UPSERT doesn't touch this column, so this write is
                        # never at risk of being clobbered the way
                        # simulation_video_path is.
                        with db_manager.get_connection() as conn:
                            conn.execute(
                                "UPDATE claims SET ai_estimated_cost = ? WHERE claim_id = ?",
                                (ai_cost_estimate["estimated_cost_kes"], claim_id)
                            )
                            conn.commit()
                except Exception as e:
                    logger.warning(f"AI cost estimation step failed for {claim_id}: {e}")

            # Analyze repair shop photos
            if repair_photos:
                logger.info(f"Analyzing {len(repair_photos)} repair shop photos...")
                for photo_data, filename in repair_photos[:2]:
                    result = await self.photo_service.analyze_photo(
                        photo_data, filename, claim_id,
                        party="repair_shop",
                        narrative_context=repair_estimate or "",
                    )
                    all_photos.append(result)
                    await asyncio.sleep(2)

            # ── AI-ROL: Computer Vision recommendation ─────────────────────────────
            if all_photos:
                total_anomalies = sum(len(p.anomalies) for p in all_photos)
                avg_confidence = sum(p.analysis_confidence for p in all_photos) / len(all_photos) / 100
                ai_rol.record_recommendation(
                    claim_id=claim_id,
                    capability="computer_vision",
                    recommendation=(
                        f"{len(all_photos)} photo(s) analyzed — {total_anomalies} anomal"
                        f"{'y' if total_anomalies == 1 else 'ies'} detected"
                    ),
                    confidence=avg_confidence,
                    evidence={
                        "photos": [
                            {"filename": p.filename, "risk_score": p.risk_score, "anomalies": p.anomalies}
                            for p in all_photos
                        ]
                    },
                )

            # Analyze all narratives
            logger.info("Analyzing narratives...")

            member_narrative_analysis = await self.narrative_service.analyze_narrative(
                member_narrative, claim_id, party="member"
            )
            await asyncio.sleep(2)

            assessor_narrative_analysis = None
            if assessor_report:
                assessor_narrative_analysis = await self.narrative_service.analyze_narrative(
                    assessor_report, claim_id, party="assessor"
                )
                await asyncio.sleep(2)

            repair_narrative_analysis = None
            if repair_estimate:
                repair_narrative_analysis = await self.narrative_service.analyze_narrative(
                    repair_estimate, claim_id, party="repair_shop"
                )
                await asyncio.sleep(2)

            # ── AI-ROL: Narrative Intelligence recommendation (one per party) ──────
            for party_label, narrative_analysis in (
                ("member", member_narrative_analysis),
                ("assessor", assessor_narrative_analysis),
                ("repair_shop", repair_narrative_analysis),
            ):
                if narrative_analysis is None:
                    continue
                ai_rol.record_recommendation(
                    claim_id=claim_id,
                    capability="narrative_intelligence",
                    recommendation=(
                        f"{party_label} narrative — quality {narrative_analysis.narrative_quality_score}/100, "
                        f"sentiment {narrative_analysis.sentiment}, "
                        f"{len(narrative_analysis.inconsistencies)} inconsistency(ies) flagged"
                    ),
                    confidence=narrative_analysis.narrative_quality_score / 100,
                    evidence={
                        "party": party_label,
                        "inconsistencies": narrative_analysis.inconsistencies,
                        "key_entities": narrative_analysis.key_entities,
                    },
                )

            # Cross-party verification
            cross_party_check = await self._verify_cross_party_consistency(
                member_narrative_analysis,
                assessor_narrative_analysis,
                repair_narrative_analysis,
                all_photos
            )

            # ── AI-ROL: Cross-validation & Risk Intelligence recommendation ────────
            ai_rol.record_recommendation(
                claim_id=claim_id,
                capability="cross_validation",
                recommendation=(
                    f"{cross_party_check.get('inconsistency_count', 0)} cross-party inconsistency(ies) found "
                    f"— cross-party risk {cross_party_check.get('cross_party_risk_score', 0)}/100"
                    if cross_party_check.get("inconsistencies_found")
                    else "No cross-party inconsistencies found"
                ),
                confidence=None,
                evidence={
                    "inconsistencies": cross_party_check.get("inconsistencies", []),
                    "duplicate_photos_detected": cross_party_check.get("duplicate_photos_detected", 0),
                    "verification_quality": cross_party_check.get("verification_quality"),
                },
            )

            # ── Physics reconstruction ────────────────────────────────────────────
            # Passes narrative_analysis so bridge uses Gemini-extracted entities
            # instead of keyword inference for speed, crush depth, and angle.
            physics_summary = await self._run_physics_reconstruction(
                claim_id=claim_id,
                member_narrative=member_narrative,
                location=location,
                estimated_cost=estimated_cost,
                assessor_report=assessor_report,
                narrative_analysis=member_narrative_analysis,
                assessor_crush_depth_mm=assessor_crush_depth_mm,
                assessor_approach_angle_deg=assessor_approach_angle_deg,
                assessor_id=assessor_id,
                member_cv_severity=member_cv_severity,
                assessor_cv_severity=assessor_cv_severity,
            )

            # ── AI-ROL: Physics & Mathematical Consistency recommendation ──────────
            if physics_summary.get("status") == "complete":
                ai_rol.record_recommendation(
                    claim_id=claim_id,
                    capability="physics_consistency",
                    recommendation=(
                        f"{physics_summary.get('physics_verdict')} — "
                        f"score {physics_summary.get('physics_fraud_score')}/100: "
                        f"{physics_summary.get('verdict_reason')}"
                    ),
                    confidence=physics_summary.get("confidence"),
                    evidence={
                        "comparison": physics_summary.get("comparison"),
                        "inconsistencies": physics_summary.get("inconsistencies"),
                        "measurement_flags": physics_summary.get("measurement_flags", []),
                    },
                )

            # ── Business Rules Engine (Appendix C §C.1 + extensions) ────────────────
            business_rules_result = None
            if member_id and policy_id:
                try:
                    business_rules_result = business_rules.BusinessRulesEngine(db_manager).evaluate(
                        claim_id=claim_id,
                        member_id=member_id,
                        policy_id=policy_id,
                        claim_type=claim_type,
                        narrative_text=member_narrative,
                        photo_count=len(member_photos),
                        incident_details=incident_details,
                    )
                    ai_rol.record_recommendation(
                        claim_id=claim_id,
                        capability="business_rules",
                        recommendation=(
                            f"{len(business_rules_result.findings)} rule(s) triggered "
                            f"— business rules risk {business_rules_result.risk_score}/100"
                            if business_rules_result.findings
                            else "No business rules triggered"
                        ),
                        confidence=None,
                        evidence=business_rules_result.to_dict(),
                    )
                except Exception as e:
                    logger.error(f"Business rules evaluation failed for {claim_id}: {str(e)}")
                    business_rules_result = None

            # ── Graph-based relationship analysis (Appendix C §C.2) ─────────────────
            relationship_result = None
            if member_id:
                try:
                    claim_row_for_graph = db_manager.get_claim(claim_id) or {}
                    relationship_result = graph_relationship.analyze_relationships(
                        db_manager, claim_id=claim_id, member_id=member_id,
                        repair_shop_id=claim_row_for_graph.get("repair_shop_id"),
                    )
                    ai_rol.record_recommendation(
                        claim_id=claim_id,
                        capability="graph_relationship_analysis",
                        recommendation=(
                            f"{len(relationship_result.findings)} relationship(s) flagged"
                            if relationship_result.findings
                            else "No hidden relationships identified"
                        ),
                        confidence=None,
                        evidence=relationship_result.to_dict(),
                    )
                except Exception as e:
                    logger.error(f"Relationship analysis failed for {claim_id}: {str(e)}")
                    relationship_result = None

            # ── Narrative-similarity search (Appendix C §C.2) ───────────────────────
            similarity_result = None
            try:
                corpus = db_manager.get_historical_narrative_corpus()
                similarity_result = narrative_similarity.find_similar_narratives(member_narrative, corpus)
                ai_rol.record_recommendation(
                    claim_id=claim_id,
                    capability="narrative_similarity",
                    recommendation=(
                        f"{len(similarity_result.matches)} similar historical narrative(s) found"
                        if similarity_result.matches
                        else "No similar historical narrative patterns found"
                    ),
                    confidence=(similarity_result.matches[0].similarity if similarity_result.matches else None),
                    evidence=similarity_result.to_dict(),
                )
            except Exception as e:
                logger.error(f"Narrative similarity search failed for {claim_id}: {str(e)}")
                similarity_result = None

            # ── Combine business-rules, relationship, and similarity signals ────────
            # These three all produce "evidence" findings rather than continuous
            # scores, so they're blended into one figure and fed into the risk
            # model as a single "business_rules" component rather than adding
            # three more weighted slots to calculate_risk_score's signature.
            combined_evidence_score = None
            if business_rules_result is not None or relationship_result is not None or similarity_result is not None:
                combined_evidence_score = business_rules_result.risk_score if business_rules_result else 0
                if relationship_result:
                    combined_evidence_score += sum(
                        business_rules.SEVERITY_WEIGHT.get(f.severity, 0) for f in relationship_result.findings
                    )
                if similarity_result and similarity_result.matches:
                    top_match = similarity_result.matches[0]
                    combined_evidence_score += int(top_match.similarity * top_match.fraud_score)
                combined_evidence_score = min(100, combined_evidence_score)

            # Calculate risk score — picks up physics score from DB
            logger.info("Calculating risk score...")
            photo_scores = [photo.risk_score for photo in all_photos]
            risk_result = await self.risk_service.calculate_risk_score(
                photo_scores,
                member_narrative_analysis,
                estimated_cost,
                location,
                historical_data,
                claim_id,
                business_rules_score=combined_evidence_score,
            )

            # ── Apply cross-party adjustment then recalculate label ───────────────
            if cross_party_check["inconsistencies_found"]:
                risk_result.overall_score = min(risk_result.overall_score + 20, 100)
                risk_result.recommendations.insert(0, "Cross-party inconsistencies detected")

            if risk_result.overall_score >= 75:
                risk_result.risk_level = RiskLevel.HIGH
            elif risk_result.overall_score >= 45:
                risk_result.risk_level = RiskLevel.MEDIUM
            else:
                risk_result.risk_level = RiskLevel.LOW

            # ── Physics floor re-application ──────────────────────────────────────
            # Uses level_order so physics can only RAISE the risk level, never lower
            # it. This preserves any bump from cross-party adjustment.
            physics_score_val   = physics_summary.get("physics_fraud_score", 0)
            physics_verdict_val = physics_summary.get("physics_verdict", "CONSISTENT")

            _level_order = {RiskLevel.LOW: 0, RiskLevel.MEDIUM: 1, RiskLevel.HIGH: 2}

            physics_implied = (
                RiskLevel.HIGH   if physics_score_val >= 85 else
                RiskLevel.MEDIUM if physics_score_val >= 70 else
                RiskLevel.LOW
            )

            if _level_order[physics_implied] > _level_order[risk_result.risk_level]:
                logger.info(
                    f"Physics floor re-applied: {risk_result.risk_level.value} → "
                    f"{physics_implied.value} "
                    f"(physics={physics_score_val}/100) for {claim_id}"
                )
                risk_result.risk_level = physics_implied

            # ── Business rules floor re-application ────────────────────────────────
            # Same pattern as physics: a HIGH-severity rule finding (e.g. usage-class
            # mismatch, driver ineligibility, a shared bank account across claimants)
            # shouldn't get diluted away just because the other components
            # (photo/narrative/amount/location) happen to score low on an otherwise
            # mundane-looking claim. Business-rules and relationship findings share
            # the same severity vocabulary, so they're floored together.
            if business_rules_result is not None or relationship_result is not None:
                all_findings = list(business_rules_result.findings if business_rules_result else []) + \
                    list(relationship_result.findings if relationship_result else [])
                highest_severity = max(
                    (f.severity for f in all_findings),
                    key=lambda s: business_rules.SEVERITY_WEIGHT.get(s, 0),
                    default=None,
                )
                rules_implied = (
                    RiskLevel.HIGH   if highest_severity in ("high", "critical") else
                    RiskLevel.MEDIUM if highest_severity == "medium" else
                    RiskLevel.LOW
                )
                if _level_order[rules_implied] > _level_order[risk_result.risk_level]:
                    logger.info(
                        f"Business rules floor re-applied: {risk_result.risk_level.value} → "
                        f"{rules_implied.value} "
                        f"(highest rule severity={highest_severity}) for {claim_id}"
                    )
                    risk_result.risk_level = rules_implied

            # Regenerate explanation with final adjusted score
            risk_result.explanation = self.risk_service._generate_explanation(
                overall_score=risk_result.overall_score,
                weights=risk_result.weights,
                scores=risk_result.component_scores
            )

            # ── Decision gate ─────────────────────────────────────────────────────
            # Tiered by physics signal strength.
            # Combined-signal escalation: INCONSISTENT physics + HIGH risk = REJECT.
            # CONSISTENT physics falls through to risk_level-based decision.
            if physics_verdict_val == "INCONSISTENT" and physics_score_val >= 70:
                if risk_result.risk_level == RiskLevel.HIGH:
                    decision        = "REJECT_CLAIM"
                    decision_reason = (
                        f"Physics and fraud signals both critical — "
                        f"physics score {physics_score_val}/100, "
                        f"overall risk {risk_result.overall_score}/100. "
                        f"Claim rejected pending full investigation."
                    )
                else:
                    decision        = "FLAG_FOR_INVESTIGATION"
                    decision_reason = (
                        f"Physics reconstruction found significant inconsistencies "
                        f"(score: {physics_score_val}/100). "
                        f"Manual review required before payment."
                    )

            elif physics_verdict_val == "INCONSISTENT" and physics_score_val >= 50:
                decision        = "APPROVE_WITH_REVIEW"
                decision_reason = (
                    f"Borderline physics inconsistencies (score: {physics_score_val}/100). "
                    f"Approve with mandatory assessor verification."
                )

            elif physics_verdict_val == "SUSPICIOUS":
                decision        = "APPROVE_WITH_REVIEW"
                decision_reason = (
                    f"Minor physics anomalies detected (score: {physics_score_val}/100). "
                    f"Proceed with assessor review."
                )

            elif risk_result.risk_level == RiskLevel.HIGH:
                decision        = "REJECT_CLAIM"
                decision_reason = "High fraud risk — claim rejected pending full investigation."

            elif risk_result.risk_level == RiskLevel.MEDIUM:
                decision        = "FLAG_FOR_INVESTIGATION"
                decision_reason = "Medium fraud risk — manual review required before payment."

            else:
                decision        = "APPROVE_CLAIM"
                decision_reason = "Low fraud risk — proceed with payment."

            logger.info(
                f"Final decision for {claim_id}: {decision} | "
                f"Risk: {risk_result.overall_score}/100 ({risk_result.risk_level.value}) | "
                f"Physics: {physics_score_val}/100 {physics_verdict_val} | "
                f"McHenry ran: {physics_summary.get('data_sources', {}).get('mchenry_ran', 'unknown')}"
            )

            processing_time = int((datetime.now() - start_time).total_seconds() * 1000)

            # Bubble simulation_video_path up from physics_summary so it is
            # available at the top level of analysis_result and survives store_claim.
            simulation_video_path = physics_summary.get("simulation_video_path")

            # ── AI Advisory consolidation ──────────────────────────────────────────
            # Synthesizes narrative/CV/physics/cross-party signals into one
            # recommendation via claims-advisory-v1 (see _build_ai_advisory).
            ai_advisory = await self._build_ai_advisory(
                claim_id=claim_id,
                estimated_cost=estimated_cost,
                location=location,
                member_narrative=member_narrative,
                narrative_analysis=member_narrative_analysis,
                all_photos=all_photos,
                physics_summary=physics_summary,
                cross_party_check=cross_party_check,
                risk_result=risk_result,
                business_rules_result=business_rules_result,
                relationship_result=relationship_result,
                similarity_result=similarity_result,
            )

            # ── AI-ROL: AI Advisory recommendation ─────────────────────────────────
            # This is the consolidated recommendation the claims handler actually
            # reviews and acts on (proceed/clarify/escalate/override), so handler
            # actions recorded later are matched against this record by default.
            ai_rol.record_recommendation(
                claim_id=claim_id,
                capability="ai_advisory",
                recommendation=(
                    f"{ai_advisory.get('recommended_action', 'Refer for Manual Review')}: "
                    f"{ai_advisory.get('explanation', '')}"
                ),
                confidence=ai_advisory.get("confidence"),
                evidence={
                    "early_risk_indicator": ai_advisory.get("early_risk_indicator"),
                    "narrative_intelligence_observation": ai_advisory.get("narrative_intelligence_observation"),
                    "computer_vision_observation": ai_advisory.get("computer_vision_observation"),
                    "physics_math_consistency_observation": ai_advisory.get("physics_math_consistency_observation"),
                    "cross_validation_risk_observation": ai_advisory.get("cross_validation_risk_observation"),
                    "source": ai_advisory.get("source"),
                },
            )

            analysis_result = {
                "claim_id":           claim_id,
                "analysis_timestamp": datetime.now().isoformat(),
                "ai_advisory":        ai_advisory,

                "final_assessment": {
                    "decision":         decision,
                    "decision_reason":  decision_reason,
                    "fraud_risk_score": risk_result.overall_score,
                    "risk_level":       risk_result.risk_level.value,
                },

                "parties_analyzed": {
                    "member":       True,
                    "assessor":     assessor_report is not None,
                    "repair_shop":  repair_estimate is not None,
                },

                "fraud_risk_score":      risk_result.overall_score,
                "risk_level":            risk_result.risk_level.value,
                "simulation_video_path": simulation_video_path,
                # store_claim() persists this into claims.narrative — without
                # a top-level key here it silently defaulted to '' even
                # though the real text was captured fine in member_submission
                # below (that nested copy stays too, for the full JSON record).
                "narrative":             member_narrative,

                "member_submission": {
                    "narrative":          member_narrative,
                    "narrative_analysis": member_narrative_analysis.dict(),
                    "photos_count":       len(member_photos),
                },

                "assessor_submission": {
                    "report":       assessor_report,
                    "analysis":     assessor_narrative_analysis.dict() if assessor_narrative_analysis else None,
                    "photos_count": len(assessor_photos) if assessor_photos else 0,
                } if assessor_report else None,

                "repair_shop_submission": {
                    "estimate":     repair_estimate,
                    "analysis":     repair_narrative_analysis.dict() if repair_narrative_analysis else None,
                    "photos_count": len(repair_photos) if repair_photos else 0,
                } if repair_estimate else None,

                "photo_analysis": {
                    "total_photos": len(all_photos),
                    "by_party": {
                        "member":       len(member_photos),
                        "assessor":     len(assessor_photos) if assessor_photos else 0,
                        "repair_shop":  len(repair_photos) if repair_photos else 0,
                    },
                    "results": [photo.dict() for photo in all_photos],
                },

                "cross_party_verification": cross_party_check,
                "physics_reconstruction":   physics_summary,
                "business_rules":           business_rules_result.to_dict() if business_rules_result else None,
                "relationship_analysis":    relationship_result.to_dict() if relationship_result else None,
                "narrative_similarity":     similarity_result.to_dict() if similarity_result else None,
                "risk_scoring":             risk_result.dict(),
                "recommendations":          risk_result.recommendations,

                "estimated_cost":     estimated_cost,
                "ai_estimated_cost":  ai_cost_estimate["estimated_cost_kes"] if ai_cost_estimate else None,
                "ai_cost_breakdown":  ai_cost_estimate["breakdown"] if ai_cost_estimate else None,
                "location":           location,
                "processing_time_ms": processing_time,
                "timestamp":          datetime.now().isoformat(),
                "analysis_quality":   "enhanced_ai_multiparty_physics",
            }

            # ── Persist to DB ─────────────────────────────────────────────────────
            try:
                db_manager.store_claim(analysis_result)
                logger.info(f"Multi-party analysis stored for {claim_id}")
            except Exception as e:
                logger.error(f"Storage error: {str(e)}")

            # ── Preserve simulation_video_path after store_claim ──────────────────
            # store_claim does a full row write and doesn't know about this column,
            # so it silently wipes whatever _run_physics_reconstruction wrote in
            # Step 9. A targeted UPDATE immediately after re-applies the path.
            if simulation_video_path:
                try:
                    with db_manager.get_connection() as conn:
                        cur = conn.cursor()
                        cur.execute(
                            "UPDATE claims SET simulation_video_path = ? WHERE claim_id = ?",
                            (simulation_video_path, claim_id)
                        )
                        conn.commit()
                    logger.info(
                        f"simulation_video_path persisted for {claim_id}: {simulation_video_path}"
                    )
                except Exception as e:
                    logger.error(
                        f"Failed to persist simulation_video_path for {claim_id}: {str(e)}"
                    )
            # ─────────────────────────────────────────────────────────────────────

            logger.info(f"Multi-party analysis complete: Risk {risk_result.overall_score}/100")

            return analysis_result

        except Exception as e:
            logger.error(f"Multi-party analysis failed: {str(e)}")
            logger.debug(traceback.format_exc())

            return {
                "claim_id":         claim_id,
                "fraud_risk_score": 50,
                "risk_level":       "medium",
                "error":            str(e),
                "recommendations":  ["Analysis failed — manual review required"],
            }
    
        
    async def _classify_claim_for_physics(self, narrative: str) -> dict:
        """
        Use the self-hosted LLM to determine if physics reconstruction is applicable.
        Called before running physics to avoid wasting compute on non-collision claims.
        Defaults to running physics if classification fails — safe fallback.
        """
        from datetime import date
 
        prompt = f"""
Today's date is {date.today().strftime('%d %B %Y')}. Any incident date on or before today is valid — do not flag past dates as future dates.
 
You are an insurance claim analyst. Read this claim narrative and determine whether 
it involves a physical vehicle-to-vehicle collision where crash physics analysis 
would be meaningful.
 
Narrative:
{narrative}
 
Respond in JSON only — no explanation outside the JSON:
{{
    "is_collision": true/false,
    "claim_category": "collision / single_vehicle_fixed_object / fire / theft / flood / vandalism / mechanical / other",
    "physics_applicable": true/false,
    "impacts_fixed_object": true/false,
    "reason": "one sentence explanation"
}}

physics_applicable is TRUE when:
- Two or more vehicles physically impacted each other (crush/velocity analysis applies to both), OR
- ONE vehicle struck a rigid, effectively immovable fixed object -- a wall, concrete pillar/barrier, curb, or similar structure solid enough that the vehicle's own crush depth against it is physically meaningful (set claim_category to "single_vehicle_fixed_object" and impacts_fixed_object to true in this case; leave it false for the two-vehicle case)

physics_applicable is FALSE for:
- Fire (including arson)
- Theft or attempted theft
- Flood or weather damage
- Vandalism with no collision
- Mechanical breakdown
- Single vehicle incidents with genuinely no measurable impact (e.g. fell into a ditch with no crush damage, tyre burst, rolled with no described contact) -- but a described impact against a wall/pillar/barrier with actual crush damage IS physics_applicable (see above)
"""
        try:
            result = await asyncio.to_thread(
                generate_json, prompt, model=TEXT_REASONING_MODEL, retries=1, timeout=60,
            )
            logger.info(
                f"Physics classification: {result.get('claim_category')} —"
                f"applicable={result.get('physics_applicable')} — {result.get('reason')}"
            )
            return result
        except Exception as e:
            logger.warning(f"Physics classification failed: {str(e)} — defaulting to run physics")
 
        return {
            "is_collision": True,
            "claim_category": "unknown",
            "physics_applicable": True,
            "reason": "Classification failed — defaulting to physics analysis"
        }

    # ── In service.py — add this helper to ClaimOrchestrator ─────────────────
    async def _build_physics_explanation(self, result, terrain_slope: float, terrain_surface: str) -> str:
        """
        Generate plain-English physics explanation using the self-hosted LLM.
        Falls back to hardcoded template if the model call fails.
        """
        try:
            velocity_penalty = {
                "none": 0, "low": 5, "medium": 20, "high": 40, "critical": 65
            }.get(result.velocity_fraud_severity, 0)
            consistency_penalty = max(0, 100 - result.impact_consistency_score)
            inconsistency_descriptions = [inc['description'] for inc in result.inconsistencies]
            speed_source_note = (
                f"This speed was NOT explicitly stated by the member — it was a rough estimate "
                f"guessed from general wording in their narrative, at only "
                f"{result.v1_speed_confidence:.0%} confidence. Do not describe it as something "
                f"the member said or claimed. If a strong conclusion would rest mainly on this "
                f"number, say the evidence is inconclusive rather than asserting fraud."
                if result.v1_speed_is_inferred else
                "This speed was explicitly given by the member in their account of the incident."
            )

            prompt = f"""
    You are a senior motor insurance fraud investigator in Kenya writing a forensic report
    for a claims committee. Explain the following physics reconstruction findings clearly
    and professionally. Connect the numbers together — explain what they mean, not just
    what they are. Use plain English. Do not use bullet points. Write 2-3 paragraphs.

    Findings:
    - Insured vehicle (V1): {result.vehicle_1_key}
    - Third party vehicle (V2): {result.vehicle_2_key}
    - Road: {terrain_surface.replace('_', ' ')}, slope {terrain_slope}°
    - V1 speed used for reconstruction: {result.stated_speed_v1_kmh} km/h. {speed_source_note}
    - Physics-derived speed (McHenry crush energy model): {result.computed_speed_v1_kmh} km/h
    - Speed delta: {result.stated_vs_computed_delta_kmh} km/h — fraud signal: {result.velocity_fraud_severity.upper()}
    - Expected crush depth at this speed: {result.expected_crush_depth_mm} mm
    - Observed crush depth: {result.stated_crush_depth_mm} mm
    - Impact consistency score: {result.impact_consistency_score}/100
    - Inconsistencies found: {len(result.inconsistencies)}
    - Inconsistency details: {inconsistency_descriptions}
    - Velocity penalty: {velocity_penalty} points (weight 60%)
    - Consistency penalty: {consistency_penalty} points (weight 40%)
    - Final physics score: {result.physics_fraud_score}/100 — {result.physics_verdict}
    - Simulation: {result.simulation_method}, {result.pathway}
    - Confidence: {result.confidence:.0%}

    End with one sentence stating the overall conclusion and recommended action.
    """
            explanation = await asyncio.to_thread(
                generate, prompt, model=TEXT_REASONING_MODEL, timeout=90,
            )
            explanation = explanation.strip()
            logger.info(f"Physics explanation generated for {result.claim_id} (model={TEXT_REASONING_MODEL})")
            return explanation

        except Exception as e:
            logger.warning(f"Physics explanation generation failed — using hardcoded fallback: {str(e)}")
            from physics_engine import build_physics_explanation
            from terrain_service import TerrainResult, ROAD_FRICTION
            terrain = TerrainResult(
                latitude=0.0,
                longitude=0.0,
                elevation_m=0.0,
                slope_degrees=terrain_slope,
                road_surface=terrain_surface,
                friction_coefficient=ROAD_FRICTION.get(terrain_surface, 0.75),
                source="fallback",
                confidence=0.3
            )
            return build_physics_explanation(result, terrain)
    
    async def _run_physics_reconstruction(
        self,
        claim_id: str,
        member_narrative: str,
        location: str,
        estimated_cost: float,
        assessor_report: Optional[str] = None,
        narrative_analysis=None,
        assessor_crush_depth_mm: Optional[float] = None,
        assessor_approach_angle_deg: Optional[float] = None,
        assessor_id: Optional[str] = None,
        member_cv_severity: Optional[str] = None,
        assessor_cv_severity: Optional[str] = None,
    ) -> dict:
        """
        Auto-run physics reconstruction after narrative analysis.
        Uses Gemini to classify claim type before deciding whether physics applies.
        Uses assessor report as source narrative when available (more reliable).
        Generates a 5-second kinematic timeline via the multi-vehicle simulation.
        Renders timeline to MP4 video file for report attachment.
        Non-blocking — returns summary dict, never raises.

        assessor_crush_depth_mm / assessor_approach_angle_deg: physical
        measurements the assessor took on-site, if provided. These still take
        priority over narrative-text inference for the actual reconstruction
        run (measured beats guessed) -- but an assessor colluding with the
        member/repairer can type in whatever number produces the outcome they
        want, so a blind override defeats the one signal in this pipeline
        that's supposed to be independent of what the human parties say.
        Instead: the narrative-extracted value is kept as a baseline, and the
        assessor's number is cross-checked against it AND against the
        trained CV model's severity read from the *member's* independently
        submitted photos (member_cv_severity) -- those were uploaded earlier,
        separately, before the assessor was even assigned, so they're a much
        harder signal for an assessor to have shaped. assessor_cv_severity
        (from the assessor's own photos) is checked too but weighted as a
        weaker signal, since a colluding assessor controls those photos as
        well as the number. Any material mismatch is recorded as a warning
        and does not silently disappear, and repeated mismatches for the same
        assessor raise their historical flag rate for future claims (see
        db_manager.get_assessor_track_record).
        """
        try:
            import json as _json
            import part_identifier
            from pipeline_bridge import get_bridge
            from physics_engine import get_engine
            from terrain_service import get_terrain
            from vehicle_registry import get_vehicle_profile
            from multi_vehicle_simulation import ConfigurableMultiVehicleEngine
            # NOTE: render_simulation_video is imported lazily inside Step 8,
            # not here. It pulls in matplotlib, which is an optional dependency
            # for video rendering only. Importing it at the top means a missing
            # matplotlib install kills physics reconstruction entirely (this
            # caused physics_fraud_score=0 / CONSISTENT fallback in production).

            # ── Step 1: Gemini classifies whether physics applies ─────────────────
            source_narrative = assessor_report if assessor_report else member_narrative
            classification = await self._classify_claim_for_physics(source_narrative)

            if not classification.get("physics_applicable", True):
                logger.info(
                    f"Physics skipped for {claim_id} — "
                    f"{classification.get('claim_category')} claim: {classification.get('reason')}"
                )
                return {
                    "status": "skipped",
                    "reason": classification.get("reason"),
                    "claim_category": classification.get("claim_category"),
                    "physics_applicable": False,
                }

            # ── Step 2: Skip if already run for this claim ────────────────────────
            # Exception: if the assessor has since provided real on-site
            # measurements, that's a strictly better input than whatever ran
            # at member-submission time -- re-run rather than silently
            # keeping the earlier, narrative-guessed result.
            has_assessor_override = assessor_crush_depth_mm is not None or assessor_approach_angle_deg is not None
            with db_manager.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT physics_fraud_score FROM claims WHERE claim_id = ?",
                    (claim_id,)
                )
                row = cursor.fetchone()
                if row and row["physics_fraud_score"] not in (None, 0) and not has_assessor_override:
                    logger.info(f"Physics already run for {claim_id} — skipping")
                    return {
                        "status": "already_run",
                        "physics_fraud_score": row["physics_fraud_score"],
                        "claim_category": classification.get("claim_category"),
                    }

            # ── Step 2.5: Look up the claimant's actual vehicle from their policy ──
            # The claimant's own vehicle (make/model/year) is already known — it's
            # on their policy record in motor_policy_details — but physics
            # reconstruction previously only ever guessed it from narrative text,
            # the same weakness the third-party vehicle had before this week's
            # fixes. This is real stored data, not an inference, so it takes
            # priority over anything narrative extraction comes up with.
            policy_vehicle_make = None
            policy_vehicle_model = None
            with db_manager.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT c.policy_id, m.vehicle_make, m.vehicle_model, m.vehicle_year
                    FROM claims c
                    LEFT JOIN motor_policy_details m ON m.policy_id = c.policy_id
                    WHERE c.claim_id = ?
                    """,
                    (claim_id,)
                )
                policy_row = cursor.fetchone()
                if policy_row and policy_row["vehicle_make"]:
                    policy_vehicle_make = policy_row["vehicle_make"]
                    policy_vehicle_model = policy_row["vehicle_model"]
                    logger.info(
                        f"V1 vehicle for {claim_id} resolved from policy record: "
                        f"{policy_vehicle_make} {policy_vehicle_model} "
                        f"({policy_row['vehicle_year']}) — overriding narrative-based inference"
                    )

            # ── Step 3: Extract Gemini-parsed entities ────────────────────────────
            ed       = {}
            vehicles = {}
            damage   = {}

            if narrative_analysis and hasattr(narrative_analysis, "extracted_data"):
                ed       = narrative_analysis.extracted_data or {}
                vehicles = ed.get("vehicles") or {}
                damage   = ed.get("damage_physics") or {}

            v1_stationary    = bool(vehicles.get("v1_stationary", False))
            v2_stationary    = bool(vehicles.get("v2_stationary", False))
            narrative_crush  = damage.get("crush_depth_mm")
            narrative_angle  = damage.get("approach_angle_deg")
            gemini_crush     = narrative_crush
            gemini_angle     = narrative_angle
            gemini_v1_speed  = vehicles.get("v1_stated_speed_kmh")
            gemini_v2_speed  = vehicles.get("v2_stated_speed_kmh")

            # ── Step 3.4: Find the actual CV-detected damage location ─────────────
            # Previously the reconstruction's impact zone/angle only ever came
            # from narrative text (infer_impact_zone/infer_approach_angle in
            # pipeline_bridge.py), completely disconnected from what the
            # member's own photos actually show -- a claim whose narrative
            # said "front-end collision" but whose photo clearly shows door
            # damage got animated as a head-on impact regardless. This finds
            # the highest-confidence damage_zones[] entry already produced by
            # part_identifier.py for this claim's photos (same data source
            # DamagePhotoOverlay uses) so the physics input -- and therefore
            # the rendered animation -- reflects the real damaged part rather
            # than a narrative guess. Used below for crush depth (existing
            # vision fallback), impact_zone_v1, and approach_angle_deg.
            best_photo_zone, best_photo_filename, best_photo_party = None, None, None
            try:
                with db_manager.get_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute(
                        "SELECT filename, analysis_result FROM claim_photos WHERE claim_id = ?",
                        (claim_id,),
                    )
                    for row in cursor.fetchall():
                        try:
                            row_result = _json.loads(row["analysis_result"]) if row["analysis_result"] else {}
                        except Exception:
                            continue
                        for zone in (row_result.get("damage_zones") or []):
                            if best_photo_zone is None or (zone.get("confidence") or 0) > (best_photo_zone.get("confidence") or 0):
                                best_photo_zone, best_photo_filename, best_photo_party = zone, row["filename"], row_result.get("party")
            except Exception as e:
                logger.warning(f"Could not look up detected damage zone for {claim_id}: {e}")

            # part_identifier.py's part vocabulary (e.g. "Front Left Door")
            # is much finer-grained than physics_engine's structural zone map
            # (front_bumper/rear_bumper/driver_door/passenger_door/etc) --
            # this collapses one onto the other. "Left"/"Right" maps to
            # driver_door/passenger_door in the SAME direction pipeline_bridge.py's
            # own narrative-keyword inference already uses (that mapping predates
            # this change and isn't altered here, just matched for consistency).
            _PART_TO_ZONE = {
                "front bumper": "front_bumper", "bonnet/hood": "front_bumper", "front grille": "front_bumper",
                "windscreen": "front_bumper", "left headlamp": "front_bumper", "right headlamp": "front_bumper",
                "rear bumper": "rear_bumper", "boot/trunk": "rear_bumper", "rear windscreen": "rear_bumper",
                "left taillamp": "rear_bumper", "right taillamp": "rear_bumper",
                "front left door": "driver_door", "front left fender": "driver_door", "left pillar": "driver_door",
                "left running board": "driver_door", "left front wing mirror": "driver_door", "left window": "driver_door",
                "front left wheel": "driver_door",
                "front right door": "passenger_door", "front right fender": "passenger_door", "right pillar": "passenger_door",
                "right running board": "passenger_door", "right front wing mirror": "passenger_door", "right window": "passenger_door",
                "front right wheel": "passenger_door",
                "rear left door": "rear_driver", "rear left fender": "rear_driver", "rear left wheel": "rear_driver",
                "rear right door": "rear_passenger", "rear right fender": "rear_passenger", "rear right wheel": "rear_passenger",
                "roof": "roof",
            }
            photo_impact_zone = _PART_TO_ZONE.get((best_photo_zone or {}).get("part", "").lower()) if best_photo_zone else None
            # Only zones the animation's fixed junction geometry can actually
            # distinguish visually: a front/rear hit stays a 180°/0° approach,
            # anything on a door/fender/pillar/wheel is a side impact (90°) --
            # matches the same coarse angle buckets infer_approach_angle()
            # already uses for narrative keywords, just sourced from the photo.
            photo_impact_angle = (
                180.0 if photo_impact_zone == "front_bumper" else
                0.0 if photo_impact_zone == "rear_bumper" else
                90.0 if photo_impact_zone else None
            )
            if photo_impact_zone:
                logger.info(
                    f"Impact zone for {claim_id}: CV-detected '{best_photo_zone.get('part')}' "
                    f"on '{best_photo_filename}' (confidence {best_photo_zone.get('confidence')}) "
                    f"-> {photo_impact_zone}, angle {photo_impact_angle}° -- overriding narrative-inferred zone/angle"
                )

            # Claimant-confirmed position (FNOL "where was the other vehicle"
            # question) -- a STATED input, same tier as stated speed/crush
            # depth: used as the geometry when nothing stronger exists, but
            # cross-checked against CV-detected damage location (harder for a
            # colluding claimant to fake, since photos were uploaded
            # separately) rather than trusted blindly. Precedence: CV-detected
            # > claimant-stated > narrative keyword guess.
            from pipeline_bridge import infer_stated_collision_geometry
            stated_geometry = infer_stated_collision_geometry(source_narrative)
            geometry_source = "cv_detected" if photo_impact_zone else "narrative_inferred"
            if stated_geometry:
                stated_zone, stated_angle = stated_geometry
                if photo_impact_zone and photo_impact_zone != stated_zone:
                    measurement_flags.append(
                        f"Claimant said the other vehicle struck their {stated_zone.replace('_', ' ')}, "
                        f"but the uploaded photos show damage consistent with {photo_impact_zone.replace('_', ' ')} -- "
                        f"using the photo-detected location for reconstruction"
                    )
                elif not photo_impact_zone:
                    photo_impact_zone, photo_impact_angle = stated_zone, stated_angle
                    geometry_source = "claimant_stated"
                    logger.info(
                        f"Impact zone for {claim_id}: claimant-stated '{stated_zone}', "
                        f"angle {stated_angle}° -- overriding narrative-inferred zone/angle"
                    )

            # Assessor's on-site measurements still take priority over
            # narrative-text inference for the reconstruction run itself
            # ("measured beats guessed"), but they are NOT blindly trusted --
            # a colluding assessor can type in whatever number they want, so
            # every override is cross-checked against (a) what the narrative
            # independently described and (b) what the trained CV model sees
            # in the photos, and any material mismatch is recorded rather
            # than silently discarded.
            measurement_flags: List[str] = []

            CV_SEVERITY_CRUSH_RANGE_MM = {
                "low":    (0, 50),
                "medium": (20, 160),
                "high":   (120, 380),
            }

            def _check_crush_vs_cv(crush_mm: float, cv_severity: Optional[str], source_label: str) -> Optional[str]:
                if crush_mm is None or not cv_severity:
                    return None
                lo, hi = CV_SEVERITY_CRUSH_RANGE_MM.get(cv_severity, (0, 400))
                tolerance = 30
                if crush_mm < lo - tolerance or crush_mm > hi + tolerance:
                    return (
                        f"Claimed crush depth ({crush_mm}mm) is inconsistent with {source_label} "
                        f"photos, whose CV-detected damage severity ('{cv_severity}') implies "
                        f"roughly {lo}-{hi}mm"
                    )
                return None

            if assessor_crush_depth_mm is not None:
                logger.info(
                    f"Crush depth for {claim_id}: assessor measured "
                    f"{assessor_crush_depth_mm}mm (narrative-extracted: {narrative_crush})"
                )
                if narrative_crush:
                    delta_pct = abs(assessor_crush_depth_mm - narrative_crush) / max(narrative_crush, 1) * 100
                    if delta_pct > 40:
                        measurement_flags.append(
                            f"Assessor-measured crush depth ({assessor_crush_depth_mm}mm) differs "
                            f"from the narrative's described damage ({narrative_crush}mm) by "
                            f"{delta_pct:.0f}%"
                        )
                member_flag = _check_crush_vs_cv(
                    assessor_crush_depth_mm, member_cv_severity,
                    "the member's independently-submitted"
                )
                if member_flag:
                    measurement_flags.append(member_flag)
                assessor_flag = _check_crush_vs_cv(
                    assessor_crush_depth_mm, assessor_cv_severity, "the assessor's own"
                )
                if assessor_flag:
                    measurement_flags.append(assessor_flag + " (weaker signal — assessor-supplied photos)")
                gemini_crush = assessor_crush_depth_mm
                crush_source = "assessor_measured"
            elif narrative_crush:
                crush_source = "llm_extracted"
            else:
                # Neither the narrative nor an assessor gave a crush-depth
                # number -- previously this meant physics ran with
                # crush_depth_mm=0 (no independent speed-vs-damage check at
                # all, just echoing the stated speed back). Try a vision-
                # based estimate from the claim's own damage photos first,
                # so the reconstruction doesn't depend on a human having
                # typed a number in. Genuinely a rough single-photo
                # estimate, not a measurement -- tagged as such in
                # data_sources below, never presented as "measured".
                crush_source = "not_available"
                try:
                    v1_make_for_lookup = policy_vehicle_make or vehicles.get("v1_make") or ""
                    v1_model_for_lookup = policy_vehicle_model or vehicles.get("v1_model") or ""
                    v1_profile, _ = get_vehicle_profile(v1_make_for_lookup, v1_model_for_lookup, vehicles.get("v1_body_type"))

                    # Reuses best_photo_zone/best_photo_filename/best_photo_party
                    # from the CV-detected-damage-location lookup above (Step
                    # 3.4) instead of re-querying the same table a second time.
                    if best_photo_zone and best_photo_filename:
                        with db_manager.get_connection() as conn:
                            cursor = conn.cursor()
                            cursor.execute(
                                "SELECT file_data FROM claim_photo_files WHERE claim_id = ? AND filename = ? AND party = ?",
                                (claim_id, best_photo_filename, best_photo_party or "member"),
                            )
                            file_row = cursor.fetchone()
                        if file_row and file_row["file_data"]:
                            vision_estimate = await part_identifier.estimate_crush_depth_mm(
                                file_row["file_data"], best_photo_zone.get("part", "damaged panel"), v1_profile.width_mm,
                            )
                            if vision_estimate and vision_estimate["confidence"] >= 30:
                                gemini_crush = vision_estimate["crush_depth_mm"]
                                crush_source = "vision_estimated"
                                logger.info(
                                    f"Crush depth for {claim_id}: vision-estimated {gemini_crush}mm "
                                    f"(confidence {vision_estimate['confidence']}%, from '{best_photo_filename}') "
                                    f"-- no narrative or assessor figure was available"
                                )
                except Exception as e:
                    logger.warning(f"Vision crush-depth fallback failed for {claim_id}: {e}")

            if assessor_approach_angle_deg is not None:
                logger.info(
                    f"Approach angle for {claim_id}: assessor measured "
                    f"{assessor_approach_angle_deg}° (narrative-extracted: {narrative_angle})"
                )
                if narrative_angle is not None:
                    delta_deg = abs(assessor_approach_angle_deg - narrative_angle)
                    if delta_deg > 45:
                        measurement_flags.append(
                            f"Assessor-measured approach angle ({assessor_approach_angle_deg}°) "
                            f"differs from the narrative's described angle ({narrative_angle}°) "
                            f"by {delta_deg:.0f}°"
                        )
                gemini_angle = assessor_approach_angle_deg

            # Historical pattern check: an assessor whose overrides are
            # flagged unusually often across their claims is itself a fraud
            # signal, independent of whether any single claim looks OK.
            if measurement_flags and assessor_id:
                track = db_manager.get_assessor_track_record(assessor_id)
                if track["total_assessed_claims"] >= 3 and track["flagged_rate_pct"] >= 30:
                    measurement_flags.append(
                        f"Assessor {assessor_id} pattern risk: "
                        f"{track['flagged_measurement_discrepancies']}/{track['total_assessed_claims']} "
                        f"of their prior assessed claims ({track['flagged_rate_pct']}%) had a "
                        f"measurement-discrepancy flag — recommend supervisor review"
                    )

            if measurement_flags:
                for flag in measurement_flags:
                    logger.warning(f"Measurement discrepancy for {claim_id}: {flag}")

            v1_speed = 0.0 if v1_stationary else float(gemini_v1_speed or 0)
            v2_speed = 0.0 if v2_stationary else float(gemini_v2_speed or 0)

            if v1_stationary:
                logger.info(
                    f"V1 stationary at impact for {claim_id} — speed set to 0 km/h"
                )
            if v2_stationary:
                logger.info(
                    f"V2 stationary at impact for {claim_id} — speed set to 0 km/h"
                )

            # ── Step 3.5: Single-vehicle-vs-fixed-object impacts ───────────────────
            # A single vehicle hitting a wall/pillar/barrier previously got
            # skipped entirely -- there's no second vehicle, but there IS a
            # real physical impact whose crush depth still says something
            # about impact speed (the McHenry crush-energy math in
            # physics_engine.py never actually needed a second vehicle for
            # V1's own speed-vs-crush check; only the momentum/ΔV step does,
            # and a barrier's momentum contribution is trivial once treated
            # as effectively immovable). Route these through the same
            # reconstruct() call with a synthetic 50-tonne "fixed_object"
            # profile as V2 instead of skipping -- see vehicle_registry.py's
            # FALLBACK_PROFILES["fixed_object"] for why that mass value.
            impacts_fixed_object = bool(classification.get("impacts_fixed_object"))
            if impacts_fixed_object:
                vehicles = dict(vehicles)
                vehicles["v2_make"] = "Fixed Object"
                vehicles["v2_model"] = "Barrier"
                vehicles["v2_body_type"] = "fixed_object"
                v2_speed = 0.0
                logger.info(f"Physics for {claim_id}: single-vehicle-vs-fixed-object impact — reconstructing against a synthetic immovable barrier instead of skipping")

            # The CV-detected damage location (Step 3.4) beats a narrative-
            # text angle guess the same way it beats narrative crush depth --
            # but an assessor's own on-site angle measurement still wins over
            # either (assessor_approach_angle_deg, applied earlier above,
            # already sits in gemini_angle by this point when present).
            if photo_impact_zone and assessor_approach_angle_deg is None:
                gemini_angle = photo_impact_angle

            # ── Step 4: Run reconstruction ────────────────────────────────────────
            bridge = get_bridge()
            engine = get_engine()

            physics_input = bridge.process_manual_payload(
                claim_id=claim_id,
                narrative=source_narrative,
                location_text=location,
                estimated_repair_cost=estimated_cost,
                v1_make=policy_vehicle_make or vehicles.get("v1_make") or "",
                v1_model=policy_vehicle_model or vehicles.get("v1_model") or "",
                v1_body_type=vehicles.get("v1_body_type") or "",
                v1_stated_speed_kmh=v1_speed,
                v2_make=vehicles.get("v2_make") or "",
                v2_model=vehicles.get("v2_model") or "",
                v2_body_type=vehicles.get("v2_body_type") or "",
                v2_stated_speed_kmh=v2_speed,
                crush_depth_mm=float(gemini_crush or 0),
                approach_angle_deg=float(gemini_angle) if gemini_angle is not None else 0.0,
                impact_zone_v1=photo_impact_zone or "",
            )

            if policy_vehicle_make:
                physics_input.warnings.append(
                    f"V1 vehicle confirmed from policy record: {policy_vehicle_make} {policy_vehicle_model} "
                    f"(not inferred from narrative)"
                )

            for flag in measurement_flags:
                physics_input.warnings.append(f"MEASUREMENT DISCREPANCY: {flag}")

            # ── Step 5: Post-hoc overrides ────────────────────────────────────────
            if not gemini_crush:
                physics_input.crush_depth_mm = 0.0
                logger.warning(
                    f"Crush depth not extracted from narrative for {claim_id} — "
                    f"zeroing bridge keyword inference. McHenry skipped. "
                    f"Assessor measurement required."
                )
            else:
                # NOTE: deliberately NOT named crush_source -- that name is
                # already used above (Step 3) to build data_sources["crush_depth"],
                # and Python has no block scoping, so reusing it here silently
                # overwrote the real value (e.g. "vision_estimated") right
                # before the final dict was built. This is a log-message-only
                # label, unrelated to data_sources.
                crush_log_label = "assessor measurement" if assessor_crush_depth_mm is not None else "LLM extraction"
                logger.info(
                    f"Crush depth from {crush_log_label}: {gemini_crush}mm for {claim_id}"
                )

            if v2_stationary:
                physics_input.v2_stated_speed_kmh = 0.0
                physics_input.v2_speed_is_inferred = False
                physics_input.v2_speed_confidence = 1.0
                physics_input.warnings = [
                    w for w in physics_input.warnings
                    if "V2 speed estimated" not in w
                ]
                physics_input.warnings.append(
                    "V2 stationary at impact — speed set to 0 km/h "
                    "(narrative: stopped / parked / at red light)"
                )

            if v1_stationary:
                physics_input.v1_stated_speed_kmh = 0.0
                physics_input.v1_speed_is_inferred = False
                physics_input.v1_speed_confidence = 1.0
                physics_input.warnings = [
                    w for w in physics_input.warnings
                    if "V1 speed estimated" not in w
                ]
                physics_input.warnings.append(
                    "V1 stationary at impact — speed set to 0 km/h "
                    "(narrative: stopped / parked / at red light)"
                )

            if gemini_angle is not None:
                physics_input.approach_angle_deg = float(gemini_angle)
                physics_input.warnings = [
                    w for w in physics_input.warnings
                    if "Approach angle inferred" not in w
                ]
                angle_log_label = "assessor measurement" if assessor_approach_angle_deg is not None else "LLM extraction"
                physics_input.warnings.append(
                    f"Approach angle from {angle_log_label}: {gemini_angle}°"
                )

            logger.info(
                f"Physics input for {claim_id}: "
                f"V1={physics_input.v1_make} {physics_input.v1_model} "
                f"@{physics_input.v1_stated_speed_kmh}km/h "
                f"({'stationary' if v1_stationary else 'moving'}) | "
                f"V2={physics_input.v2_make} {physics_input.v2_model} "
                f"@{physics_input.v2_stated_speed_kmh}km/h | "
                f"crush={physics_input.crush_depth_mm}mm | "
                f"angle={physics_input.approach_angle_deg}° | "
                f"quality={physics_input.data_quality_score}"
            )

            result = engine.reconstruct_from_input(physics_input)

            # ── Step 6: Generate plain-English explanation ────────────────────────
            terrain = get_terrain(location_text=location)
            result.physics_explanation = await self._build_physics_explanation(
                result=result,
                terrain_slope=terrain.slope_degrees,
                terrain_surface=terrain.road_surface,
            )
            logger.info(f"Physics explanation generated for {claim_id}")

            # ── Step 7: Generate 5-second kinematic timeline ──────────────────────
            timeline_output = None
            try:
                profile_v1, _ = get_vehicle_profile(
                    physics_input.v1_make,
                    physics_input.v1_model,
                    physics_input.v1_body_type,
                )
                profile_v2, _ = get_vehicle_profile(
                    physics_input.v2_make,
                    physics_input.v2_model,
                    physics_input.v2_body_type,
                )

                registry_json = _json.dumps({
                    "vehicle_dimension_registry": {
                        physics_input.v1_model.lower().replace(" ", "_"): {
                            "kenyan_operational_mass_kg": profile_v1.effective_mass_kg,
                            "class": profile_v1.body_type,
                        },
                        physics_input.v2_model.lower().replace(" ", "_"): {
                            "kenyan_operational_mass_kg": profile_v2.effective_mass_kg,
                            "class": profile_v2.body_type,
                        },
                    }
                })

                # `or` chains here previously treated a genuine 0 km/h as
                # "no value, use the fallback" (Python's `or` can't tell
                # "explicitly zero" from "unset") -- for a fixed-object
                # barrier reconstruction (see the single-vehicle-vs-object
                # path above), v2's speed is deliberately 0 everywhere, so
                # this silently fell through to a hardcoded "50.0" and
                # animated the barrier as a moving vehicle, which is
                # physically nonsensical (fixed objects don't move). Explicit
                # None-checks, plus a hard lock to 0 for a fixed_object V2
                # regardless of anything computed upstream.
                # Mirrors v2_sim_speed's existing precedence below -- prefer
                # the physics-reconstructed speed (the same number shown as
                # "V1 physics-derived" in the Evidence Panel) over the raw
                # claimed speed, so the animation's HUD can't show a
                # different V1 speed than the evidence sitting right next to
                # it. Previously this always used the stated speed while V2
                # already used the reconstructed one -- that asymmetry, plus
                # multi_vehicle_simulation.py separately re-deriving V1's
                # speed from a disconnected momentum formula, is what let
                # the Live Reconstruction HUD show 87 km/h while Speed
                # Analysis showed 33.5 km/h for the same claim.
                if result.computed_speed_v1_kmh is not None:
                    v1_sim_speed = result.computed_speed_v1_kmh
                elif physics_input.v1_stated_speed_kmh is not None:
                    v1_sim_speed = physics_input.v1_stated_speed_kmh
                else:
                    v1_sim_speed = 0.0
                if physics_input.v2_body_type == "fixed_object":
                    v2_sim_speed = 0.0
                elif result.computed_speed_v2_kmh is not None:
                    v2_sim_speed = result.computed_speed_v2_kmh
                elif physics_input.v2_stated_speed_kmh is not None:
                    v2_sim_speed = physics_input.v2_stated_speed_kmh
                else:
                    v2_sim_speed = 50.0

                # Real collision geometry, not the old fixed 90-degree T-bone
                # crossing every claim rendered as regardless of what
                # actually happened. impact_zone_v1 already carries a
                # left/right side signal (driver_door/rear_driver = left,
                # passenger_door/rear_passenger = right) via the same
                # zone that was resolved above -- CV-detected-from-photos
                # when available, claimant-confirmed via the FNOL
                # "other vehicle position" question when narrative-inferred,
                # narrative keyword guess otherwise. front_bumper/rear_bumper/
                # roof carry no side information (angle alone, near-parallel,
                # is symmetric either way), so default to "right" there --
                # preserves the old hardcoded visual for every claim that
                # doesn't have a real side signal, rather than silently
                # changing already-correct-looking simulations.
                _side_zones_left = ("driver_door", "rear_driver")
                _side_zones_right = ("passenger_door", "rear_passenger")
                if physics_input.impact_zone_v1 in _side_zones_left:
                    impact_side = "left"
                elif physics_input.impact_zone_v1 in _side_zones_right:
                    impact_side = "right"
                else:
                    impact_side = "right"

                sim_payload = {
                    "claim_id": claim_id,
                    "approach_angle_deg": physics_input.approach_angle_deg,
                    "impact_side": impact_side,
                    "environment_snapshot": {
                        "resolved_friction_mu": terrain.friction_coefficient,
                    },
                    "vehicles": [
                        {
                            "model_identity": physics_input.v1_model.lower().replace(" ", "_"),
                            "stated_speed_kmh": v1_sim_speed,
                            "damage": {
                                "depth_meters": (physics_input.crush_depth_mm or 30.0) / 1000,
                                "width_meters": (profile_v1.width_mm / 1000) * 0.4,
                            },
                        },
                        {
                            "model_identity": physics_input.v2_model.lower().replace(" ", "_"),
                            "stated_speed_kmh": v2_sim_speed,
                            "damage": {
                                "depth_meters": 0.05,
                                "width_meters": 0.80,
                            },
                        },
                    ],
                }

                sim_engine = ConfigurableMultiVehicleEngine(registry_json)
                timeline_output = sim_engine.generate_simulation_timeline(sim_payload)

                # ConfigurableMultiVehicleEngine models a generic two-BODY
                # collision, where both sides get a post-impact recoil
                # velocity from their own crush energy (delta_v2 above) --
                # correct for two real vehicles, but physically wrong for a
                # fixed barrier/pillar/wall, which cannot move no matter what
                # the crush-energy math says. Rather than special-case the
                # shared two-vehicle engine, force V2's entire telemetry
                # stationary here whenever it's the synthetic fixed_object
                # profile -- position pinned at the impact point, velocity
                # 0 for every frame, pre- and post-impact alike.
                if physics_input.v2_body_type == "fixed_object" and timeline_output:
                    for frame in timeline_output["v2_third_party_telemetry"]:
                        frame["position"] = [0.0, 0.0, 0.0]
                        frame["velocity_kmh"] = 0.0
                    timeline_output["simulation_metadata"]["v2_pre_impact_speed_kmh"] = 0.0
                    timeline_output["simulation_metadata"]["delta_v2_kmh"] = 0.0

                    # The generic engine's post-impact formula (v1_pos_y =
                    # v1_speed * t for t>0) assumes V1 separates from a V2
                    # that also moves out of the way -- against a rigid,
                    # immovable barrier that's physically wrong: unless
                    # delta_v1 fully removes V1's forward momentum, it kept
                    # advancing PAST the origin, visually driving straight
                    # through the wall for the rest of the clip (this is
                    # almost certainly what looked like "hitting the barrier
                    # twice" -- the car overlapping the barrier's drawn
                    # footprint again after the initial impact frame, rather
                    # than stopping at it). Clamp V1 to never cross y=0 --
                    # a car crumples against a wall and stops, it doesn't
                    # pass through it.
                    for frame in timeline_output["v1_insured_telemetry"]:
                        if frame["time_sec"] > 0 and frame["position"][1] > 0:
                            frame["position"] = [0.0, 0.0, 0.0]
                            frame["velocity_kmh"] = 0.0

                logger.info(
                    f"Timeline generated for {claim_id}: "
                    f"{timeline_output['simulation_metadata']['total_frames_generated']} frames"
                )

            except Exception as e:
                logger.warning(f"Timeline generation failed for {claim_id}: {str(e)}")

            # ── Step 8: Render simulation video (fully isolated, never blocks physics) ─
            # Lazy import here, not at the top of the function. If matplotlib is
            # missing or broken in this environment, only the video is skipped —
            # physics_fraud_score and physics_verdict above are already computed
            # and unaffected.
            video_path = None
            try:
                from render_simulation_video import render_collision_video

                if timeline_output:
                    # Compute estimated delta-V when the engine doesn't provide it.
                    # Uses simple momentum conservation as a fallback so the
                    # renderer always receives a usable float, never None.
                    _dv1 = getattr(result, "delta_v1_kmh", None)
                    _dv2 = getattr(result, "delta_v2_kmh", None)

                    if _dv1 is None or _dv2 is None:
                        try:
                            _v1  = float(physics_input.v1_stated_speed_kmh or 0)
                            _v2  = float(physics_input.v2_stated_speed_kmh or 50)
                            _m1  = float(profile_v1.effective_mass_kg or 1300)
                            _m2  = float(profile_v2.effective_mass_kg or 1500)
                            _vf  = (_m1 * _v1 + _m2 * _v2) / (_m1 + _m2)
                            _dv1 = _dv1 if _dv1 is not None else round(abs(_vf - _v1), 1)
                            _dv2 = _dv2 if _dv2 is not None else round(abs(_vf - _v2), 1)
                            logger.info(
                                f"Delta-V estimated via momentum for {claim_id}: "
                                f"ΔV1={_dv1} km/h  ΔV2={_dv2} km/h"
                            )
                        except Exception:
                            _dv1 = _dv1 or 30.0
                            _dv2 = _dv2 or 20.0

                    # matplotlib rendering + ffmpeg encoding is CPU-bound and
                    # synchronous — off the event loop for the same reason as
                    # the Ollama calls (see their comments above).
                    video_path = await asyncio.to_thread(
                        render_collision_video,
                        timeline_output=timeline_output,
                        claim_id=claim_id,
                        output_dir="./claim_videos",
                        physics_data={
                            "v1_speed_kmh":     float(physics_input.v1_stated_speed_kmh or 0),
                            "v2_speed_kmh":     float(physics_input.v2_stated_speed_kmh or 50),
                            "delta_v1_kmh":     _dv1,
                            "delta_v2_kmh":     _dv2,
                            "impact_angle_deg": float(physics_input.approach_angle_deg or 0),
                            "crush_depth_mm":   float(physics_input.crush_depth_mm or 0),
                            "physics_verdict":  result.physics_verdict,
                            "v1_make":          physics_input.v1_make,
                            "v1_model":         physics_input.v1_model,
                            "v2_make":          physics_input.v2_make,
                            "v2_model":         physics_input.v2_model,
                            "v1_length_m":      profile_v1.length_mm / 1000,
                            "v1_width_m":       profile_v1.width_mm / 1000,
                            "v2_length_m":      profile_v2.length_mm / 1000,
                            "v2_width_m":       profile_v2.width_mm / 1000,
                        }
                    )
                    logger.info(f"Simulation video rendered for {claim_id}: {video_path}")
            except ImportError as e:
                logger.warning(
                    f"Video rendering unavailable for {claim_id} — "
                    f"matplotlib/dependency not installed in this environment: {str(e)}. "
                    f"Physics score and verdict above are unaffected."
                )
            except Exception as e:
                logger.warning(f"Video render failed for {claim_id}: {str(e)}")

            # ── Step 9: Persist to DB ─────────────────────────────────────────────
            with db_manager.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    UPDATE claims SET
                        physics_fraud_score         = ?,
                        physics_verdict              = ?,
                        physics_result               = ?,
                        reconstruction_pathway        = ?,
                        physics_timeline              = ?,
                        simulation_video_path         = ?,
                        measurement_discrepancy_flag  = ?
                    WHERE claim_id = ?
                ''', (
                    result.physics_fraud_score,
                    result.physics_verdict,
                    _json.dumps(result.to_dict()),
                    result.pathway,
                    _json.dumps(timeline_output) if timeline_output else None,
                    video_path,
                    1 if measurement_flags else 0,
                    claim_id,
                ))
                conn.commit()

            logger.info(
                f"Physics reconstruction complete for {claim_id}: "
                f"{result.physics_fraud_score}/100 — {result.physics_verdict}"
            )

            return {
                "status":              "complete",
                "claim_category":      classification.get("claim_category"),
                "pathway":             result.pathway,
                "vehicle_1_key":       result.vehicle_1_key,
                "vehicle_2_key":       result.vehicle_2_key,
                # vehicle_2_key is a human-readable string ("Fixed Object
                # Barrier (body_type_fallback)"), not a structured field --
                # the frontend has no reliable way to parse "is V2 actually a
                # barrier, not a car" back out of it. Send the real
                # body_type directly so the reconstruction canvas can render
                # a wall instead of a car silhouette for V2 without guessing.
                "v2_body_type":        physics_input.v2_body_type,
                "physics_fraud_score": result.physics_fraud_score,
                "physics_verdict":     result.physics_verdict,
                "confidence":          result.confidence,
                "verdict_reason":      result.verdict_reason,
                "physics_explanation": result.physics_explanation,
                "simulation_method":   result.simulation_method,
                "inconsistencies":     result.inconsistencies,
                "warnings":            physics_input.warnings,
                "measurement_flags":   measurement_flags,
                "has_measurement_discrepancy": bool(measurement_flags),
                "timeline":            timeline_output,
                "simulation_video_path": video_path,
                # ΔV/energy/impact-force numbers the reconstruction UI's
                # Impact Analysis panel needs -- previously computed on
                # PhysicsResult but never surfaced past this function.
                # impact_force/vertex are ONLY populated on Pathway 1
                # (telemetry) claims -- None on Pathway 2, which the UI must
                # show as an honest "unavailable" state rather than a
                # fabricated value.
                "delta_v_kmh":         result.delta_v_kmh,
                "kinetic_energy_j":    result.kinetic_energy_j,
                "crush_energy_j":      result.crush_energy_j,
                "energy_consistent":   result.energy_consistent,
                "impact_force_magnitude_n": result.impact_force_magnitude_n,
                "impact_force_is_estimated": result.impact_force_is_estimated,
                "v1_impact_vertex_xyz": result.v1_impact_vertex_xyz,
                "terrain_adjusted":    result.terrain_adjusted,
                "slope_adjustment_kmh": result.slope_adjustment_kmh,
                # The actual structural zone used for this reconstruction
                # (front_bumper/rear_bumper/driver_door/etc) plus, when it
                # came from a real photo rather than a narrative guess, the
                # CV-detected part name/confidence -- lets the reconstruction
                # UI orient the animation/highlight toward where the vehicle
                # was actually damaged instead of a generic head-on default.
                "impact_zone_v1": physics_input.impact_zone_v1,
                "impact_zone_v1_source": geometry_source,
                "impact_zone_v1_detected_part": (best_photo_zone or {}).get("part") if geometry_source == "cv_detected" else None,
                # Claimed-vs-reconstructed comparison data -- computed by
                # PhysicsResult already, but previously only written to the
                # DB's standalone `physics_result` column (never read back by
                # any endpoint) instead of flowing through analysis_result.
                # Frontend's "Claimed vs Reconstructed" table (AssessorClaimDetails.tsx)
                # reads this directly.
                "comparison": {
                    "speed_v1_kmh": {
                        "claimed": result.stated_speed_v1_kmh,
                        "reconstructed": result.computed_speed_v1_kmh,
                        "delta": result.stated_vs_computed_delta_kmh,
                    },
                    "speed_v2_kmh": {
                        "claimed": result.stated_speed_v2_kmh,
                        "reconstructed": result.computed_speed_v2_kmh,
                    },
                    "v2_speed_is_inferred": result.v2_speed_is_inferred,
                    "v2_speed_confidence": result.v2_speed_confidence,
                    "crush_depth_mm": {
                        "claimed": result.stated_crush_depth_mm,
                        "reconstructed": result.expected_crush_depth_mm,
                        "delta": result.crush_depth_delta_mm,
                    },
                    "velocity_fraud_flag": result.velocity_fraud_flag,
                    "velocity_fraud_severity": result.velocity_fraud_severity,
                    "impact_consistency_score": result.impact_consistency_score,
                    "v1_speed_is_inferred": result.v1_speed_is_inferred,
                    "v1_speed_confidence": result.v1_speed_confidence,
                    # approach_angle_deg is only ever an INPUT to reconstruct()
                    # (physics_engine.py) -- never carried onto PhysicsResult
                    # itself, so it was previously invisible to every consumer
                    # of this dict. Echoing the resolved input value here (not
                    # a physics calculation) lets the UI show the real angle
                    # instead of omitting it or inventing one.
                    "approach_angle_deg": physics_input.approach_angle_deg,
                    "data_quality_score": physics_input.data_quality_score,
                },
                "data_sources": {
                    # crush_source is tracked explicitly above (assessor /
                    # narrative / vision-estimated / unavailable) rather than
                    # re-derived here, since "gemini_crush is truthy" alone
                    # can no longer distinguish a narrative figure from a
                    # vision estimate -- both populate the same variable.
                    "crush_depth":    crush_source,
                    "v1_speed":       "stationary_override" if v1_stationary else (
                                      "llm_extracted" if gemini_v1_speed else "keyword_inferred"
                                      ),
                    "v2_speed":       "stationary_override" if v2_stationary else (
                                      "llm_extracted" if gemini_v2_speed else "keyword_inferred"
                                      ),
                    "approach_angle": "assessor_measured" if assessor_approach_angle_deg is not None else (
                                      "llm_extracted" if gemini_angle is not None else "keyword_inferred"
                                      ),
                    "v1_vehicle":     "llm_extracted" if vehicles.get("v1_make") else "keyword_inferred",
                    "v2_vehicle":     "llm_extracted" if vehicles.get("v2_make") else "keyword_inferred",
                    "mchenry_ran":    gemini_crush is not None,
                },
            }

        except Exception as e:
            logger.warning(f"Physics reconstruction skipped for {claim_id}: {str(e)}")
            return {"status": "skipped", "reason": str(e)}
    
    async def _verify_cross_party_consistency(
        self,
        member_analysis: NarrativeAnalysisSchema,
        assessor_analysis: Optional[NarrativeAnalysisSchema],
        repair_analysis: Optional[NarrativeAnalysisSchema],
        photos: List[PhotoAnomalySchema]
    ) -> Dict[str, Any]:
        """
        Enhanced cross-party verification with deep content analysis
        Uses Gemini AI to identify inconsistencies between parties
        """
        
        logger.info("Starting enhanced cross-party consistency verification")
        
        inconsistencies = []
        
        # 1. Check for duplicate photos across parties
        photo_hashes_by_party = {}
        for photo in photos:
            # Get party info from anomalies
            party = "unknown"
            photo_dict = photo.dict()
            for anomaly in photo_dict.get("anomalies", []):
                if "party" in anomaly:
                    party = anomaly["party"]
                    break
            
            if party not in photo_hashes_by_party:
                photo_hashes_by_party[party] = []
            photo_hashes_by_party[party].append(photo.hash)
        
        # Check for same photo from different parties
        all_hashes = {}
        for party, hashes in photo_hashes_by_party.items():
            for h in hashes:
                if h in all_hashes:
                    logger.warning(f"Cross-party duplicate: {party} and {all_hashes[h]} share photo {h[:12]}...")
                    inconsistencies.append({
                        "type": "duplicate_photo_cross_party",
                        "severity": "critical",
                        "description": f"Same photo submitted by {party} and {all_hashes[h]} - FRAUD INDICATOR",
                        "confidence": 95
                    })
                all_hashes[h] = party
        
        # 2. AI-Powered narrative consistency check using Gemini
        if self.narrative_service.gemini_model and assessor_analysis:
            logger.info("Running AI-powered cross-party narrative verification")
            try:
                ai_verification = await self._ai_verify_narratives(
                    member_analysis,
                    assessor_analysis,
                    repair_analysis
                )
                
                if ai_verification.get("inconsistencies"):
                    logger.warning(f"AI detected {len(ai_verification['inconsistencies'])} cross-party inconsistencies")
                    inconsistencies.extend(ai_verification["inconsistencies"])
                
            except Exception as e:
                logger.error(f"AI verification failed: {str(e)}")
        
        # 3. Check basic credibility score differences
        if assessor_analysis:
            member_cred = getattr(member_analysis, 'credibility_score', 50)
            assessor_cred = getattr(assessor_analysis, 'credibility_score', 50)
            
            if abs(member_cred - assessor_cred) > 30:
                inconsistencies.append({
                    "type": "credibility_mismatch",
                    "severity": "medium",
                    "description": f"Large credibility difference: Member {member_cred}% vs Assessor {assessor_cred}%",
                    "confidence": 70
                })
        
        # 4. Check damage severity consistency across parties
        damage_claims = {}
        
        # Extract damage info from Gemini analyses
        member_gemini = getattr(member_analysis, 'gemini_analysis', {})
        if member_gemini:
            damage = member_gemini.get('extracted_information', {}).get('incident_details', {})
            damage_claims['member'] = damage.get('severity', 'unknown')
        
        if assessor_analysis:
            assessor_gemini = getattr(assessor_analysis, 'gemini_analysis', {})
            if assessor_gemini:
                damage = assessor_gemini.get('extracted_information', {}).get('incident_details', {})
                damage_claims['assessor'] = damage.get('severity', 'unknown')
        
        if repair_analysis:
            repair_gemini = getattr(repair_analysis, 'gemini_analysis', {})
            if repair_gemini:
                damage = repair_gemini.get('extracted_information', {}).get('incident_details', {})
                damage_claims['repair_shop'] = damage.get('severity', 'unknown')
        
        # Compare damage severity claims
        if len(damage_claims) >= 2:
            severities = list(damage_claims.values())
            severity_order = ['minor', 'moderate', 'severe', 'total']
            
            # Check if there's a major discrepancy
            member_sev = damage_claims.get('member', 'unknown')
            assessor_sev = damage_claims.get('assessor', 'unknown')
            
            if member_sev in severity_order and assessor_sev in severity_order:
                member_idx = severity_order.index(member_sev)
                assessor_idx = severity_order.index(assessor_sev)
                
                # If difference is more than 1 level (e.g., minor vs severe)
                if abs(member_idx - assessor_idx) > 1:
                    logger.warning(f"Damage severity mismatch: Member'{member_sev}'vs Assessor'{assessor_sev}'")
                    inconsistencies.append({
                        "type": "damage_severity_mismatch",
                        "severity": "high",
                        "description": f"Member claims '{member_sev}' damage but Assessor reports '{assessor_sev}' - significant discrepancy",
                        "confidence": 80
                    })
        
        # 5. Check photo quality expectations
        for photo in photos:
            photo_dict = photo.dict()
            party = "unknown"
            
            # Get party from anomalies
            for anomaly in photo_dict.get("anomalies", []):
                if "party" in anomaly:
                    party = anomaly["party"]
                    break
            
            # Assessor photos should be higher quality
            if party == "assessor" and photo.risk_score > 40:
                inconsistencies.append({
                    "type": "poor_assessor_photo_quality",
                    "severity": "medium",
                    "description": f"Assessor photo '{photo.filename}' has quality issues (risk: {photo.risk_score}) - professional assessment expected",
                    "confidence": 75
                })
        
        # Calculate cross-party risk score
        cross_party_risk = 0
        for inc in inconsistencies:
            if inc["severity"] == "critical":
                cross_party_risk += 35
            elif inc["severity"] == "high":
                cross_party_risk += 25
            elif inc["severity"] == "medium":
                cross_party_risk += 15
            else:
                cross_party_risk += 5
        
        cross_party_risk = min(cross_party_risk, 100)
        
        logger.info(f"Cross-party verification complete:")
        logger.info(f"Inconsistencies found: {len(inconsistencies)}")
        logger.info(f"Cross-party risk score: {cross_party_risk}/100")
        
        return {
            "inconsistencies_found": len(inconsistencies) > 0,
            "inconsistencies": inconsistencies,
            "inconsistency_count": len(inconsistencies),
            "photos_by_party": photo_hashes_by_party,
            "damage_claims_by_party": damage_claims,
            "cross_party_risk_score": cross_party_risk,
            "verification_quality": "ai_enhanced" if self.narrative_service.gemini_model else "rule_based"
        }

    # Must match nlp/scripts/build_finetune_dataset.py's SYSTEM_PROMPT exactly --
    # this is the exact task claims-advisory-v1 was LoRA fine-tuned on, and the
    # Modelfile's TEMPLATE only inserts a system message when one is actually
    # supplied (see ollama_client.generate's `system` param).
    AI_ADVISORY_SYSTEM_PROMPT = (
        "You are the Xenova AI Advisory capability for Old Mutual Kenya motor claims. "
        "Given structured claim context (policy, vehicle, driver, business rule outcomes, "
        "repair estimate, evidence summary, historical claims, and claimant narrative), "
        "produce a single JSON object with exactly these fields: "
        "narrative_intelligence_observation, computer_vision_observation, "
        "physics_math_consistency_observation, cross_validation_risk_observation, "
        "early_risk_indicator, confidence (0.0-1.0), recommended_action, explanation. "
        'Use "None" (string) for any field with no observation to report. '
        "Your output is advisory only -- a human claims handler makes the final decision. "
        "Respond with ONLY the JSON object, no other text."
    )

    async def _build_ai_advisory(
        self,
        claim_id: str,
        estimated_cost: float,
        location: str,
        member_narrative: str,
        narrative_analysis: NarrativeAnalysisSchema,
        all_photos: List[PhotoAnomalySchema],
        physics_summary: Dict[str, Any],
        cross_party_check: Dict[str, Any],
        risk_result: RiskScoringSchema,
        business_rules_result: Optional["business_rules.BusinessRulesResult"] = None,
        relationship_result: Optional["graph_relationship.RelationshipResult"] = None,
        similarity_result: Optional["narrative_similarity.SimilarityResult"] = None,
    ) -> Dict[str, Any]:
        """
        Consolidates Narrative Intelligence + Computer Vision + Physics &
        Mathematical Consistency + Cross-validation & Risk Intelligence
        signals into the Blueprint's single AI Advisory recommendation, via
        claims-advisory-v1 -- the LoRA fine-tune trained specifically to
        produce this exact JSON schema from this exact kind of multi-source
        context (see nlp/scripts/train_lora.py). This is the integration
        point that was missing entirely before: each signal used to stay
        siloed in its own section of analysis_result with no synthesis step.

        Falls back to a deterministic, rule-based advisory (no LLM call) if
        claims-advisory-v1 is unavailable, so a claim's analysis never comes
        back with a missing/broken advisory section.
        """
        photo_anomaly_lines = [
            f"- [{p.filename}] {a.get('type')} ({a.get('severity')}): {a.get('description')}"
            for p in all_photos
            for a in p.anomalies
        ]
        photo_section = "\n".join(photo_anomaly_lines) if photo_anomaly_lines else "No photo anomalies detected."

        inconsistency_lines = [
            f"- {i.get('type', 'inconsistency')} ({i.get('severity', 'medium')}): {i.get('description', '')}"
            for i in narrative_analysis.inconsistencies
        ]
        narrative_section = "\n".join(inconsistency_lines) if inconsistency_lines else "No narrative inconsistencies flagged."

        physics_verdict = physics_summary.get("physics_verdict", "UNKNOWN")
        physics_score = physics_summary.get("physics_fraud_score", 0)

        business_rules_section = (
            business_rules_result.observation_text if business_rules_result else "Not evaluated."
        )
        relationship_section = (
            relationship_result.observation_text if relationship_result else "Not evaluated."
        )
        similarity_section = (
            similarity_result.observation_text if similarity_result else "Not evaluated."
        )

        user_text = f"""CLAIM {claim_id}
Estimated cost: KES {estimated_cost:,.0f}
Location: {location}
Claimant narrative: "{member_narrative}"

Narrative Intelligence findings:
{narrative_section}
Narrative quality score: {narrative_analysis.narrative_quality_score}/100, sentiment: {narrative_analysis.sentiment}

Computer Vision findings ({len(all_photos)} photos analyzed):
{photo_section}

Physics & Mathematical Consistency: verdict={physics_verdict}, score={physics_score}/100

Cross-validation & Risk Intelligence: inconsistencies_found={cross_party_check.get('inconsistencies_found', False)}, \
cross_party_risk_score={cross_party_check.get('cross_party_risk_score', 0)}/100, \
inconsistency_count={cross_party_check.get('inconsistency_count', 0)}

Business Rules Engine findings:
{business_rules_section}

Graph-based relationship analysis (shared phone/bank/repair-shop across claimants):
{relationship_section}

Narrative-similarity search against historical claims:
{similarity_section}

Preliminary rule-based risk score: {risk_result.overall_score}/100 ({risk_result.risk_level.value})
"""

        try:
            from ollama_client import generate_json, OllamaError
            advisory = await asyncio.to_thread(
                generate_json, user_text, model="claims-advisory-v1",
                system=self.AI_ADVISORY_SYSTEM_PROMPT, timeout=90,
            )
            advisory["source"] = "claims-advisory-v1"
            advisory["business_rules_observation"] = business_rules_section
            advisory["relationship_analysis_observation"] = relationship_section
            advisory["narrative_similarity_observation"] = similarity_section
            logger.info(f"AI Advisory generated for {claim_id}: {advisory.get('recommended_action')}")
            return advisory
        except Exception as e:
            logger.error(f"AI Advisory consolidation failed for {claim_id}: {e}, using rule-based fallback")
            return {
                "narrative_intelligence_observation": narrative_section if inconsistency_lines else "None",
                "computer_vision_observation": photo_section if photo_anomaly_lines else "None",
                "physics_math_consistency_observation": f"{physics_verdict} ({physics_score}/100)",
                "cross_validation_risk_observation": (
                    f"{cross_party_check.get('inconsistency_count', 0)} cross-party inconsistencies"
                    if cross_party_check.get("inconsistencies_found") else "None"
                ),
                "business_rules_observation": business_rules_section,
                "relationship_analysis_observation": relationship_section,
                "narrative_similarity_observation": similarity_section,
                "early_risk_indicator": risk_result.risk_level.value.capitalize(),
                "confidence": 0.5,
                "recommended_action": "Refer for Manual Review",
                "explanation": f"AI Advisory model unavailable ({e}); deferring to rule-based risk score.",
                "source": "fallback_rule_based",
            }

    async def _ai_verify_narratives(
        self,
        member_analysis: NarrativeAnalysisSchema,
        assessor_analysis: Optional[NarrativeAnalysisSchema],
        repair_analysis: Optional[NarrativeAnalysisSchema]
    ) -> Dict[str, Any]:
        """
        Use the self-hosted LLM to cross-verify narratives from different parties
        """

        try:
            # Extract per-party structured analyses (field name kept for
            # backward compatibility with NarrativeAnalysisSchema)
            member_gemini = getattr(member_analysis, 'gemini_analysis', {})
            assessor_gemini = getattr(assessor_analysis, 'gemini_analysis', {}) if assessor_analysis else {}
            repair_gemini = getattr(repair_analysis, 'gemini_analysis', {}) if repair_analysis else {}
            
            # Build comparison prompt
            prompt = f"""
            You are an expert insurance fraud investigator. Compare these narratives from different parties for the SAME claim.
            
            MEMBER'S ACCOUNT:
            {json.dumps(member_gemini, indent=2) if member_gemini else "Not available"}
            
            ASSESSOR'S REPORT:
            {json.dumps(assessor_gemini, indent=2) if assessor_gemini else "Not available"}
            
            REPAIR SHOP ESTIMATE:
            {json.dumps(repair_gemini, indent=2) if repair_gemini else "Not available"}
            
            CRITICAL ANALYSIS REQUIRED:
            1. Do these narratives tell the SAME story about the incident?
            2. Are there contradictions in:
            - Damage type (front/rear/side impact)?
            - Damage severity (minor/moderate/severe)?
            - Weather conditions?
            - Time of incident?
            - Location details?
            - Accident circumstances?
            3. Does the member's story align with the professional assessor's findings?
            4. Does the repair estimate match the reported damage severity?
            5. Are there any impossible or implausible contradictions?
            
            Respond in JSON format:
            {{
                "narratives_consistent": true/false,
                "overall_consistency_score": 0-100,
                "inconsistencies": [
                    {{
                        "type": "damage_type/severity/location/timing/circumstances",
                        "severity": "low/medium/high/critical",
                        "description": "specific contradiction found",
                        "between_parties": ["member", "assessor"],
                        "confidence": 0-100
                    }}
                ],
                "major_red_flags": ["list of serious contradictions that suggest fraud"],
                "verification_recommendation": "approve/investigate/reject"
            }}
            
            Focus on FACTUAL contradictions, not stylistic differences.
            Member narratives are often emotional; assessor reports are technical - that's normal.
            Flag only SUBSTANTIVE inconsistencies that indicate potential fraud or confusion.
            """
            
            logger.info(f"Sending cross-party verification request to {TEXT_REASONING_MODEL}...")
            result = await asyncio.to_thread(
                generate_json, prompt, model=TEXT_REASONING_MODEL, retries=1, timeout=120,
            )

            logger.info(f"AI verification complete - Consistency: {result.get('overall_consistency_score', 0)}%")
            
            return result
            
        except Exception as e:
            logger.error(f"AI narrative verification failed: {str(e)}")
            return {"inconsistencies": []}
    
    def _parse_ai_verification_response(self, response_text: str) -> Dict[str, Any]:
        """Parse Gemini's cross-party verification response"""
        
        try:
            # Extract JSON
            json_start = response_text.find('{')
            json_end = response_text.rfind('}') + 1
            
            if json_start >= 0 and json_end > json_start:
                json_str = response_text[json_start:json_end]
                parsed = json.loads(json_str)
                
                # Validate and return
                if "inconsistencies" in parsed:
                    return parsed
            
        except Exception as e:
            logger.error(f"Error parsing AI verification response: {str(e)}")
        
        # Fallback
        return {
            "narratives_consistent": True,
            "overall_consistency_score": 50,
            "inconsistencies": [],
            "verification_recommendation": "investigate"
        }
    

# Factory function
def create_claim_orchestrator(gemini_api_key: str = None) -> ClaimOrchestrator:
    """Create ClaimOrchestrator with API key"""
    api_key = gemini_api_key or GEMINI_API_KEY
    logger.info(f"Creating ClaimOrchestrator with API key: ***{api_key[-4:]}")
    return ClaimOrchestrator(api_key)