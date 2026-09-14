from pydantic import BaseModel, Field, validator
from typing import List, Optional, Dict, Any, Union
from datetime import datetime
from enum import Enum


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"

class AnomalySeverity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class PhotoAnomalySchema(BaseModel):
    filename: str
    hash: str
    anomalies: List[Dict[str, Any]]
    risk_score: int = Field(..., ge=0, le=100)
    analysis_confidence: int = Field(..., ge=0, le=100)
    # Trained-YOLO-model-only severity read (low/medium/high), independent of
    # any vision-LLM text reasoning or narrative — used as the anchor signal
    # when cross-checking a party's numeric/narrative damage claims against
    # what the photos actually show.
    cv_severity: Optional[str] = None
    detected_classes: List[str] = Field(default_factory=list)
    # Per-detection bounding boxes + repair/replace recommendation, straight
    # from the trained YOLO model (damage_recommendation.py) -- the assessor
    # portal's damage-detection panel draws these on the photo and lets the
    # assessor confirm or override each one. Empty when no detections (or
    # for photos analyzed before this field existed).
    detections: List[Dict[str, Any]] = Field(default_factory=list)
    # Whole-photo multi-zone scan (part_identifier.scan_all_damage_zones) --
    # a broader vision-LLM pass that can name several distinct damaged parts
    # per photo, complementing `detections` (which only covers regions the
    # trained YOLO detector itself flagged). Approximate bounding boxes only
    # (bbox_normalized, 0-1 fractions of image size), not real segmentation.
    damage_zones: List[Dict[str, Any]] = Field(default_factory=list)
    # Visible location clues (signage, landmarks, setting) extracted from
    # photos classify_photo_purpose() ruled out as damage close-ups --
    # feeds business_rules.py's location_narrative_correlation rule, which
    # cross-checks this against the claim's stated incident location. None
    # for damage-closeup photos (never extracted) or when nothing useful
    # was visible.
    location_context: Optional[str] = None


class NarrativeAnalysisSchema(BaseModel):
    extracted_data: Dict[str, Any]
    inconsistencies: List[Dict[str, Any]]
    narrative_quality_score: int = Field(..., ge=0, le=100)
    key_entities: List[str]
    sentiment: str


class RiskScoringSchema(BaseModel):
    overall_score: int = Field(..., ge=0, le=100)
    risk_level: RiskLevel
    component_scores: Dict[str, float]
    recommendations: List[str]
    explanation: str
    weights: Dict[str, float] = Field(default_factory=dict)


class ClaimSubmissionSchema(BaseModel):
    claim_id: str = Field(..., min_length=1, max_length=50)
    narrative: str = Field(..., min_length=10, max_length=2000)
    estimated_cost: float = Field(..., gt=0)
    location: str = Field(..., min_length=1, max_length=200)
    accident_time: Optional[str] = None

    @validator('claim_id')
    def validate_claim_id(cls, v):
        import re
        if not re.match(r'^[A-Za-z0-9\-_]+$', v):
            raise ValueError('Claim ID must contain only alphanumeric characters, hyphens, or underscores')
        return v

    @validator('estimated_cost')
    def validate_cost(cls, v):
        if v > 10_000_000:
            raise ValueError('Estimated cost seems unreasonably high (>10M)')
        return v


class AnalysisResultSchema(BaseModel):
    claim_id: str
    fraud_risk_score: int = Field(..., ge=0, le=100)
    risk_level: RiskLevel
    photo_anomalies: List[PhotoAnomalySchema] = Field(default=[])
    narrative_analysis: Optional[NarrativeAnalysisSchema] = None
    risk_scoring: Optional[RiskScoringSchema] = None
    reconstruction_summary: str = ""
    recommendations: List[str] = Field(default=[])
    processing_time_ms: int = 0
    timestamp: datetime = Field(default_factory=datetime.now)
    # Optional extra fields for DB compatibility
    narrative: Optional[str] = None
    estimated_cost: Optional[float] = None
    location: Optional[str] = None


class SystemStatsSchema(BaseModel):
    total_claims_processed: int
    high_risk_claims: int
    average_processing_time_ms: float
    system_uptime: str
    fraud_detection_rate: str
    false_positive_rate: str


class DetailedMetricsSchema(BaseModel):
    performance: Dict[str, Union[int, float]]
    accuracy: Dict[str, float]
    system: Dict[str, str]
    distribution: Dict[str, int]


class HealthCheckSchema(BaseModel):
    status: str = Field(..., description="Overall system status")
    version: str = Field(..., description="API version")
    services: Dict[str, str] = Field(..., description="Individual service statuses")
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())


class ErrorResponseSchema(BaseModel):
    error: str = Field(..., description="Error type")
    message: str = Field(..., description="Error message")
    details: Optional[str] = Field(None, description="Additional error details")
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())


class DatabaseStatusSchema(BaseModel):
    status: str
    database_size_mb: float
    tables: Dict[str, Dict[str, int]]
    last_check: str


class ProcessingLogSchema(BaseModel):
    claim_id: str
    processing_step: str
    processing_time_ms: int
    status: str
    details: Optional[str] = None
    created_at: str


class MaintenanceResponseSchema(BaseModel):
    message: str
    timestamp: str


class ClaimsListResponseSchema(BaseModel):
    claims: List[AnalysisResultSchema] = Field(default=[])
    total: int = Field(default=0)
    limit: int = Field(default=10)
    offset: int = Field(default=0)


class ClaimHistorySchema(BaseModel):
    claim_id: str
    submission_date: datetime
    risk_score: int
    risk_level: RiskLevel
    status: str


class PaginationSchema(BaseModel):
    limit: int = Field(default=10, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class ClaimFilterSchema(BaseModel):
    risk_level: Optional[str] = Field(None, description="Filter by risk level")
    date_from: Optional[str] = Field(None, description="Filter claims from date (ISO format)")
    date_to: Optional[str] = Field(None, description="Filter claims to date (ISO format)")
    min_cost: Optional[float] = Field(None, ge=0)
    max_cost: Optional[float] = Field(None, ge=0)
    location: Optional[str] = None

    @validator('risk_level')
    def validate_risk_level(cls, v):
        if v and v.lower() not in ['low', 'medium', 'high']:
            raise ValueError('Risk level must be low, medium, or high')
        return v.lower() if v else v


class PhotoUploadSchema(BaseModel):
    filename: str
    content_type: str
    file_size: int = Field(..., gt=0, le=10485760, description="Max 10MB")

    @validator('content_type')
    def validate_content_type(cls, v):
        allowed = ['image/jpeg', 'image/jpg', 'image/png', 'image/webp']
        if v not in allowed:
            raise ValueError(f'Content type must be one of: {allowed}')
        return v


class BulkAnalysisRequestSchema(BaseModel):
    claim_ids: List[str] = Field(..., min_items=1, max_items=50)
    priority: Optional[str] = Field(default="normal", description="Processing priority: low/normal/high")