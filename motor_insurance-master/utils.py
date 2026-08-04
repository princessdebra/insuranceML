import hashlib
import re
import os
import logging
from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime, timedelta
import cv2
import numpy as np
from PIL import Image
import imagehash
import io

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class ImageProcessor:
    """Utility class for image processing operations"""
    
    @staticmethod
    def compute_file_hash(image_data: bytes) -> str:
        """Compute SHA256 hash of image file"""
        return hashlib.sha256(image_data).hexdigest()
    
    @staticmethod
    def compute_perceptual_hash(image: Image.Image) -> str:
        """Compute perceptual hash for duplicate detection"""
        return str(imagehash.phash(image))
    
    @staticmethod
    def analyze_lighting_conditions(image_data: bytes) -> Dict[str, Any]:
        """Analyze lighting conditions in image"""
        try:
            # Convert to OpenCV format
            nparr = np.frombuffer(image_data, np.uint8)
            img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            
            # Convert to grayscale for brightness analysis
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            
            # Calculate brightness statistics
            brightness_mean = np.mean(gray)
            brightness_std = np.std(gray)
            
            # Analyze histogram
            hist = cv2.calcHist([gray], [0], None, [256], [0, 256])
            
            # Determine lighting conditions
            if brightness_mean < 50:
                condition = "very_low_light"
            elif brightness_mean < 100:
                condition = "low_light"
            elif brightness_mean < 180:
                condition = "normal_light"
            else:
                condition = "bright_light"
                
            return {
                "condition": condition,
                "brightness_mean": float(brightness_mean),
                "brightness_std": float(brightness_std),
                "contrast_ratio": float(brightness_std / brightness_mean) if brightness_mean > 0 else 0
            }
            
        except Exception as e:
            logger.error(f"Error analyzing lighting: {str(e)}")
            return {
                "condition": "unknown",
                "brightness_mean": 0,
                "brightness_std": 0,
                "contrast_ratio": 0
            }
    
    @staticmethod
    def extract_exif_metadata(image: Image.Image) -> Dict[str, Any]:
        """Extract EXIF metadata from image"""
        try:
            exif_data = image._getexif() if hasattr(image, '_getexif') else None
            
            metadata = {
                "has_gps": False,
                "timestamp": None,
                "camera_make": None,
                "camera_model": None
            }
            
            if exif_data:
                # GPS information (tags 34853)
                if 34853 in exif_data:
                    metadata["has_gps"] = True
                
                # Timestamp (tag 36868 - DateTimeOriginal)
                if 36868 in exif_data:
                    metadata["timestamp"] = exif_data[36868]
                
                # Camera make (tag 271)
                if 271 in exif_data:
                    metadata["camera_make"] = exif_data[271]
                    
                # Camera model (tag 272)
                if 272 in exif_data:
                    metadata["camera_model"] = exif_data[272]
            
            return metadata
            
        except Exception as e:
            logger.error(f"Error extracting EXIF: {str(e)}")
            return {
                "has_gps": False,
                "timestamp": None,
                "camera_make": None,
                "camera_model": None
            }

class TextProcessor:
    """Utility class for text processing operations"""
    
    @staticmethod
    def extract_keywords(text: str) -> List[str]:
        """Extract key terms from narrative text"""
        # Common insurance/accident keywords
        keywords = [
            "collision", "crash", "accident", "impact", "hit", "struck",
            "rear", "front", "side", "damage", "injured", "hospital",
            "police", "traffic", "lights", "intersection", "highway",
            "speeding", "brake", "turn", "overtake", "stationary",
            "rain", "fog", "dark", "night", "morning", "evening"
        ]
        
        text_lower = text.lower()
        found_keywords = []
        
        for keyword in keywords:
            if keyword in text_lower:
                found_keywords.append(keyword)
        
        return found_keywords
    
    @staticmethod
    def detect_contradictions(text: str) -> List[Dict[str, str]]:
        """Detect logical contradictions in narrative"""
        contradictions = []
        text_lower = text.lower()
        
        # Define contradiction patterns
        contradiction_patterns = [
            {
                "pattern1": ["stationary", "stopped", "parked"],
                "pattern2": ["overtaking", "changing lanes", "turning"],
                "description": "Claims vehicle was stationary but also mentions active maneuvers"
            },
            {
                "pattern1": ["rear", "behind", "back"],
                "pattern2": ["head-on", "front", "frontal"],
                "description": "Inconsistent impact direction described"
            },
            {
                "pattern1": ["slow", "crawling", "traffic jam"],
                "pattern2": ["high speed", "fast", "speeding"],
                "description": "Contradictory speed descriptions"
            }
        ]
        
        for pattern in contradiction_patterns:
            has_pattern1 = any(p in text_lower for p in pattern["pattern1"])
            has_pattern2 = any(p in text_lower for p in pattern["pattern2"])
            
            if has_pattern1 and has_pattern2:
                contradictions.append({
                    "type": "logical_contradiction",
                    "description": pattern["description"],
                    "severity": "high"
                })
        
        return contradictions
    
    @staticmethod
    def calculate_narrative_quality(text: str) -> int:
        """Calculate quality score for narrative (0-100)"""
        score = 100
        
        # Length check
        word_count = len(text.split())
        if word_count < 10:
            score -= 30
        elif word_count < 20:
            score -= 15
            
        # Detail check (presence of specific details)
        detail_indicators = ["time", "location", "road", "weather", "speed", "direction"]
        details_found = sum(1 for indicator in detail_indicators if indicator in text.lower())
        detail_score = min(details_found * 10, 30)
        
        # Coherence check (basic grammar and structure)
        sentence_count = len([s for s in text.split('.') if s.strip()])
        if sentence_count < 2:
            score -= 20
            
        return max(0, min(100, score - 30 + detail_score))

class ValidationUtils:
    """Data validation utilities"""
    
    @staticmethod
    def validate_claim_id(claim_id: str) -> bool:
        """Validate claim ID format"""
        pattern = r"^MC-[A-Z]{2}-\d{3,6}$"
        return bool(re.match(pattern, claim_id))
    
    @staticmethod
    def validate_kenyan_location(location: str) -> bool:
        """Basic validation for Kenyan location"""
        kenyan_indicators = [
            "nairobi", "mombasa", "kisumu", "nakuru", "eldoret", "thika",
            "ngong", "karen", "westlands", "kilimani", "kibera", "kasarani",
            "road", "avenue", "street", "highway", "superhighway"
        ]
        
        location_lower = location.lower()
        return any(indicator in location_lower for indicator in kenyan_indicators)
    
    @staticmethod
    def sanitize_narrative(narrative: str) -> str:
        """Clean and sanitize narrative text"""
        # Remove excessive whitespace
        cleaned = re.sub(r'\s+', ' ', narrative.strip())
        
        cleaned = re.sub(r'<[^>]+>', '', cleaned)
        cleaned = re.sub(r'javascript:', '', cleaned, flags=re.IGNORECASE)
        
        return cleaned

class MetricsCalculator:
    """Utility class for calculating system metrics"""
    
    @staticmethod
    def calculate_fraud_detection_rate(total_claims: int, detected_fraud: int) -> float:
        """Calculate fraud detection rate percentage"""
        if total_claims == 0:
            return 0.0
        return (detected_fraud / total_claims) * 100
    
    @staticmethod
    def calculate_processing_time_stats(processing_times: List[int]) -> Dict[str, float]:
        """Calculate processing time statistics"""
        if not processing_times:
            return {"mean": 0, "median": 0, "p95": 0, "p99": 0}
        
        times = sorted(processing_times)
        n = len(times)
        
        return {
            "mean": sum(times) / n,
            "median": times[n // 2],
            "p95": times[int(n * 0.95)] if n > 0 else 0,
            "p99": times[int(n * 0.99)] if n > 0 else 0
        }
    
    @staticmethod
    def calculate_risk_distribution(scores: List[int]) -> Dict[str, int]:
        """Calculate distribution of risk scores"""
        distribution = {"low": 0, "medium": 0, "high": 0}
        
        for score in scores:
            if score < 40:
                distribution["low"] += 1
            elif score < 70:
                distribution["medium"] += 1
            else:
                distribution["high"] += 1
        
        return distribution

class ConfigManager:
    """Configuration management utilities"""
    
    @staticmethod
    def get_env_var(key: str, default: Any = None) -> Any:
        """Get environment variable with default"""
        return os.getenv(key, default)
    
    @staticmethod
    def get_database_url() -> str:
        """Get database connection URL"""
        return os.getenv(
            "DATABASE_URL", 
            "sqlite:///./motor_underwriting.db"
        )
    
    @staticmethod
    def get_model_config() -> Dict[str, Any]:
        """Get ML model configuration"""
        return {
            "photo_similarity_threshold": float(os.getenv("PHOTO_SIMILARITY_THRESHOLD", "0.95")),
            "fraud_score_threshold": int(os.getenv("FRAUD_SCORE_THRESHOLD", "70")),
            "processing_timeout_seconds": int(os.getenv("PROCESSING_TIMEOUT", "30")),
            "max_file_size_mb": int(os.getenv("MAX_FILE_SIZE_MB", "10"))
        }

def format_currency(amount: float, currency: str = "KES") -> str:
    """Format currency for display"""
    return f"{currency} {amount:,.2f}"

def calculate_time_difference(start_time: datetime, end_time: datetime) -> Dict[str, int]:
    """Calculate time difference in various units"""
    diff = end_time - start_time
    
    return {
        "total_seconds": int(diff.total_seconds()),
        "milliseconds": int(diff.total_seconds() * 1000),
        "minutes": int(diff.total_seconds() / 60),
        "hours": int(diff.total_seconds() / 3600)
    }

def generate_unique_id(prefix: str = "MC-KE") -> str:
    """Generate unique claim ID"""
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    return f"{prefix}-{timestamp}"