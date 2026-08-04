import sqlite3
import json
import logging
from typing import Dict, List, Optional, Any
from datetime import datetime
import time
from contextlib import contextmanager
from enum import Enum
import uuid
import hashlib

logger = logging.getLogger(__name__)

class DatabaseManager:
    def __init__(self, db_path: str = "claims_database.db"):
        self.db_path = db_path
        self.init_database()
    
    def init_database(self):
        """Initialize database with required tables"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            # Claims table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS claims (
                    claim_id TEXT PRIMARY KEY,
                    member_id TEXT,
                    policy_id TEXT,
                    narrative TEXT NOT NULL,
                    estimated_cost REAL NOT NULL,
                    location TEXT NOT NULL,
                    accident_time TEXT,
                    fraud_risk_score INTEGER NOT NULL,
                    risk_level TEXT NOT NULL,
                    processing_time_ms INTEGER NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    analysis_result TEXT NOT NULL,  -- JSON string of full analysis
                    simulation_video_path TEXT,
                    physics_fraud_score REAL,
                    physics_verdict TEXT,
                    physics_result TEXT,            -- JSON string of PhysicsResult
                    reconstruction_pathway TEXT,
                    physics_timeline TEXT            -- JSON string of simulation timeline
                )
            ''')
            
            # Photos table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS claim_photos (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    claim_id TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    file_size INTEGER NOT NULL,
                    content_type TEXT NOT NULL,
                    analysis_result TEXT,  -- JSON string of photo analysis
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (claim_id) REFERENCES claims (claim_id)
                )
            ''')
            
            # System metrics table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS system_metrics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    metric_name TEXT NOT NULL,
                    metric_value TEXT NOT NULL,
                    recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Processing logs table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS processing_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    claim_id TEXT NOT NULL,
                    processing_step TEXT NOT NULL,
                    processing_time_ms INTEGER NOT NULL,
                    status TEXT NOT NULL,  -- success, error, warning
                    details TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            # AI-ROL (AI Governance & Regulatory Layer) audit table.
            # One row per AI-generated recommendation (narrative, computer
            # vision, physics, cross-validation, AI advisory, etc), plus
            # a second row type for the claims handler's resulting action
            # (proceed/clarify/escalate/override) linked back to it -- this
            # is the single audit trail the Blueprint requires to underpin
            # every AI capability with explainability and traceability.
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS ai_rol_records (
                    record_id TEXT PRIMARY KEY,
                    claim_id TEXT NOT NULL,
                    capability TEXT NOT NULL,       -- narrative_intelligence, computer_vision,
                                                     -- physics_consistency, cross_validation,
                                                     -- ai_advisory, business_rules
                    recommendation TEXT NOT NULL,   -- short human-readable summary
                    confidence REAL,                -- 0.0-1.0, NULL if not applicable
                    evidence TEXT,                  -- JSON: supporting evidence for this recommendation
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    handler_action TEXT,            -- proceed, clarify, escalate, override -- NULL until actioned
                    handler_id TEXT,
                    override_reason TEXT,
                    decided_at TIMESTAMP,
                    FOREIGN KEY (claim_id) REFERENCES claims (claim_id)
                )
            ''')
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_ai_rol_claim_id ON ai_rol_records (claim_id)
            ''')
            self.create_photo_storage_table()
            conn.commit()
            logger.info("Database initialized successfully")
    
    @contextmanager
    def get_connection(self):
        """Get database connection with automatic cleanup"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row  # Enable dict-like access
        try:
            yield conn
        finally:
            conn.close()
    
    def store_claim(self, claim_data: Dict[str, Any]) -> bool:
        """Store claim analysis result in database"""
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()

                logger.debug(f"Serializing claim data for {claim_data.get('claim_id', 'unknown')}")
                serializable_data = self._make_json_serializable(claim_data)

                # Use final adjusted risk_level from analysis_result — not a stale intermediate value
                risk_level = claim_data.get('risk_level') or 'unknown'
                fraud_risk_score = claim_data.get('fraud_risk_score')
                if fraud_risk_score is None:
                    logger.warning(
                        f"fraud_risk_score missing/None for {claim_data.get('claim_id', 'unknown')} "
                        f"— defaulting to 0 rather than failing the whole write"
                    )
                    fraud_risk_score = 0

                # Defensive re-check in case caller passed pre-adjustment values
                if isinstance(fraud_risk_score, (int, float)):
                    if fraud_risk_score >= 75:
                        risk_level = 'high'
                    elif fraud_risk_score >= 45:
                        risk_level = 'medium'
                    else:
                        risk_level = 'low'

                # Every NOT NULL column below gets a real default if the caller
                # passed None (present-but-null keys bypass dict.get()'s default,
                # which is exactly what silently failed the whole INSERT before —
                # see CLM-2026-000014, where a None estimated_cost caused a NOT
                # NULL constraint failure and the ENTIRE claim summary
                # (narrative, risk score, analysis_result) failed to persist).
                narrative_val = claim_data.get('narrative') or ''
                estimated_cost_val = claim_data.get('estimated_cost')
                if estimated_cost_val is None:
                    logger.warning(
                        f"estimated_cost missing/None for {claim_data.get('claim_id', 'unknown')} "
                        f"— defaulting to 0.0 rather than failing the whole write"
                    )
                    estimated_cost_val = 0.0
                location_val = claim_data.get('location') or ''
                processing_time_val = claim_data.get('processing_time_ms')
                if processing_time_val is None:
                    processing_time_val = 0

                # Upsert, not INSERT OR REPLACE: REPLACE deletes the whole row and
                # reinserts only the columns listed here, silently wiping every
                # other column (physics_fraud_score, physics_verdict, member_id,
                # simulation_video_path, etc.) back to NULL. ON CONFLICT DO UPDATE
                # only ever touches the columns actually listed below.
                cursor.execute('''
                    INSERT INTO claims
                    (claim_id, narrative, estimated_cost, location, accident_time,
                     fraud_risk_score, risk_level, processing_time_ms, analysis_result, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(claim_id) DO UPDATE SET
                        narrative           = excluded.narrative,
                        estimated_cost      = excluded.estimated_cost,
                        location            = excluded.location,
                        accident_time       = excluded.accident_time,
                        fraud_risk_score    = excluded.fraud_risk_score,
                        risk_level          = excluded.risk_level,
                        processing_time_ms  = excluded.processing_time_ms,
                        analysis_result     = excluded.analysis_result,
                        updated_at          = excluded.updated_at
                ''', (
                    claim_data['claim_id'],
                    narrative_val,
                    estimated_cost_val,
                    location_val,
                    claim_data.get('accident_time'),
                    fraud_risk_score,
                    risk_level,
                    processing_time_val,
                    json.dumps(serializable_data, default=str, ensure_ascii=False)
                ))

                conn.commit()
                logger.info(f"Claim {claim_data['claim_id']} stored successfully")
                return True

        except Exception as e:
            logger.error(f"Error storing claim {claim_data.get('claim_id', 'unknown')}: {str(e)}")
            logger.debug(f"Claim data that failed: {str(claim_data)[:500]}...")
            return False
    
    def _make_json_serializable(self, obj: Any) -> Any:
        """Convert objects to JSON-serializable format with enhanced handling for Pydantic models"""
        try:
            # Handle None
            if obj is None:
                return None
            
            # Handle basic types
            if isinstance(obj, (str, int, float, bool)):
                return obj
                
            # Handle datetime objects
            if isinstance(obj, datetime):
                return obj.isoformat()
            
            # Handle Enum objects
            if isinstance(obj, Enum):
                return obj.value
                
            # Handle UUID objects
            if hasattr(obj, 'hex') and len(str(obj)) == 36:  # Basic UUID detection
                return str(obj)
            
            # Handle dictionaries
            if isinstance(obj, dict):
                result = {}
                for key, value in obj.items():
                    # Convert key to string if needed
                    str_key = str(key) if not isinstance(key, str) else key
                    result[str_key] = self._make_json_serializable(value)
                return result
            
            # Handle lists and tuples
            if isinstance(obj, (list, tuple)):
                return [self._make_json_serializable(item) for item in obj]
                
            # Handle sets
            if isinstance(obj, set):
                return list(obj)  # Convert set to list
            
            # Handle Pydantic models (they have .dict() method)
            if hasattr(obj, 'dict') and callable(obj.dict):
                logger.debug(f"Converting Pydantic model: {type(obj).__name__}")
                try:
                    # Use Pydantic's dict() method
                    pydantic_dict = obj.dict()
                    return self._make_json_serializable(pydantic_dict)
                except Exception as e:
                    logger.warning(f"Failed to use .dict() on {type(obj).__name__}: {e}")
                    # Fallback to __dict__
                    if hasattr(obj, '__dict__'):
                        return self._make_json_serializable(obj.__dict__)
            
            # Handle objects with __dict__ attribute
            if hasattr(obj, '__dict__'):
                logger.debug(f"Converting object with __dict__: {type(obj).__name__}")
                return self._make_json_serializable(obj.__dict__)
            
            # Handle mappingproxy objects (common in Pydantic)
            if str(type(obj)).find('mappingproxy') != -1:
                logger.debug(f"Converting mappingproxy object")
                return self._make_json_serializable(dict(obj))
            
            # Try to convert to dict if it has items()
            if hasattr(obj, 'items') and callable(obj.items):
                logger.debug(f"Converting object with items(): {type(obj).__name__}")
                return self._make_json_serializable(dict(obj.items()))
            
            # Try to convert to list if it's iterable (but not string)
            if hasattr(obj, '__iter__') and not isinstance(obj, (str, bytes)):
                try:
                    logger.debug(f"Converting iterable object: {type(obj).__name__}")
                    return [self._make_json_serializable(item) for item in obj]
                except Exception:
                    pass
            
            # Last resort: convert to string
            logger.warning(f"Converting unknown type {type(obj).__name__} to string: {str(obj)[:100]}...")
            return str(obj)
            
        except Exception as e:
            logger.error(f"Error in _make_json_serializable for {type(obj).__name__}: {str(e)}")
            # Final fallback
            return f"<Serialization Error: {type(obj).__name__}>"
    
    def get_claim(self, claim_id: str) -> Optional[Dict]:
        """Retrieve claim from database"""
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                
                # ✅ MUST include analysis_result in SELECT
                cursor.execute('''
                    SELECT 
                        claim_id,
                        member_id,
                        policy_id,
                        narrative,
                        estimated_cost,
                        location,
                        accident_time,
                        fraud_risk_score,
                        risk_level,
                        processing_time_ms,
                        analysis_result,          -- ← ADD THIS LINE!
                        created_at,
                        updated_at,
                        timestamp,
                        parties_analyzed,
                        cross_party_verification
                    FROM claims
                    WHERE claim_id = ?
                ''', (claim_id,))
                
                row = cursor.fetchone()
                
                if not row:
                    return None
                
                return dict(row)
                
        except Exception as e:
            logger.error(f"Error retrieving claim: {str(e)}")
            return None
    
    def list_claims(self, limit: int = 10, offset: int = 0, risk_level: Optional[str] = None) -> Dict[str, Any]:
        """List claims with pagination and filtering"""
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                
                # Build query with optional risk level filter
                base_query = 'FROM claims'
                params = []
                
                if risk_level:
                    base_query += ' WHERE risk_level = ?'
                    params.append(risk_level.lower())
                
                # Get total count
                count_query = f'SELECT COUNT(*) {base_query}'
                cursor.execute(count_query, params)
                total = cursor.fetchone()[0]
                
                # Get paginated results
                data_query = f'''
                    SELECT analysis_result {base_query} 
                    ORDER BY created_at DESC 
                    LIMIT ? OFFSET ?
                '''
                cursor.execute(data_query, params + [limit, offset])
                rows = cursor.fetchall()
                
                claims = [json.loads(row['analysis_result']) for row in rows]
                
                return {
                    "claims": claims,
                    "total": total,
                    "limit": limit,
                    "offset": offset
                }
                
        except Exception as e:
            logger.error(f"Error listing claims: {str(e)}")
            return {
                "claims": [],
                "total": 0,
                "limit": limit,
                "offset": offset
            }
    
    def store_photo_analysis(self, claim_id: str, filename: str, file_size: int, 
                           content_type: str, analysis_result: Dict[str, Any]) -> bool:
        """Store photo analysis result"""
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                
                # Serialize the analysis result
                serializable_result = self._make_json_serializable(analysis_result)
                
                cursor.execute('''
                    INSERT INTO claim_photos 
                    (claim_id, filename, file_size, content_type, analysis_result)
                    VALUES (?, ?, ?, ?, ?)
                ''', (
                    claim_id,
                    filename,
                    file_size,
                    content_type,
                    json.dumps(serializable_result, default=str, ensure_ascii=False)
                ))
                
                conn.commit()
                logger.debug(f"Photo analysis stored for {claim_id}/{filename}")
                return True
                
        except Exception as e:
            logger.error(f"Error storing photo analysis for {claim_id}: {str(e)}")
            return False
    
    def get_system_metrics(self) -> Dict[str, Any]:
        """Get system performance metrics from database"""
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                
                # Total claims processed
                cursor.execute('SELECT COUNT(*) FROM claims')
                total_claims = cursor.fetchone()[0]
                
                # High risk claims
                cursor.execute('SELECT COUNT(*) FROM claims WHERE fraud_risk_score >= 70')
                high_risk_claims = cursor.fetchone()[0]
                
                # Average processing time
                cursor.execute('SELECT AVG(processing_time_ms) FROM claims')
                avg_processing_time = cursor.fetchone()[0] or 0
                
                # Processing times for percentiles
                cursor.execute('SELECT processing_time_ms FROM claims ORDER BY processing_time_ms')
                processing_times = [row[0] for row in cursor.fetchall()]
                
                # Calculate percentiles
                if processing_times:
                    n = len(processing_times)
                    p95_time = processing_times[int(n * 0.95)] if n > 0 else 0
                    p99_time = processing_times[int(n * 0.99)] if n > 0 else 0
                else:
                    p95_time = p99_time = 0
                
                return {
                    "total_claims_processed": total_claims,
                    "high_risk_claims": high_risk_claims,
                    "average_processing_time_ms": float(avg_processing_time),
                    "p95_processing_time_ms": p95_time,
                    "p99_processing_time_ms": p99_time,
                    "processing_times": processing_times,
                    "fraud_detection_rate": 87.3,  # This could be calculated from historical data
                    "false_positive_rate": 12.7,   # This could be calculated from historical data
                    "system_uptime": "99.9%"
                }
                
        except Exception as e:
            logger.error(f"Error getting system metrics: {str(e)}")
            return {
                "total_claims_processed": 0,
                "high_risk_claims": 0,
                "average_processing_time_ms": 0.0,
                "p95_processing_time_ms": 0,
                "p99_processing_time_ms": 0,
                "processing_times": [],
                "fraud_detection_rate": 0.0,
                "false_positive_rate": 0.0,
                "system_uptime": "0%"
            }
    
    def log_processing_step(self, claim_id: str, step: str, processing_time_ms: int, 
                          status: str, details: str = None):
        """Log processing step for monitoring"""
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                
                cursor.execute('''
                    INSERT INTO processing_logs 
                    (claim_id, processing_step, processing_time_ms, status, details)
                    VALUES (?, ?, ?, ?, ?)
                ''', (claim_id, step, processing_time_ms, status, details))
                
                conn.commit()
                
        except Exception as e:
            logger.error(f"Error logging processing step: {str(e)}")
    
    def get_member_info(self, member_id: str) -> Optional[Dict]:
        """Get member information"""
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT * FROM members WHERE member_id = ?
                ''', (member_id,))
                
                row = cursor.fetchone()
                return dict(row) if row else None
        except Exception as e:
            logger.error(f"Error retrieving member info: {str(e)}")
            return None
    
    def get_member_policies(self, member_id: str) -> List[Dict]:
        """Get all policies for a member"""
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT 
                        policy_id,
                        policy_number,
                        policy_type,
                        cover_type,
                        sum_insured,
                        start_date,
                        end_date,
                        CASE 
                            WHEN DATE('now') BETWEEN start_date AND end_date THEN 'ACTIVE'
                            WHEN DATE('now') < start_date THEN 'PENDING'
                            ELSE 'EXPIRED'
                        END as status
                    FROM policies
                    WHERE member_id = ?
                    ORDER BY start_date DESC
                ''', (member_id,))
                
                return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"Error retrieving member policies: {str(e)}")
            return []
    
    def store_coverage_check(self, coverage_result: Dict) -> bool:
        """Store coverage check result for audit trail"""
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT INTO coverage_checks (
                        check_id, member_id, policy_id, claim_type,
                        coverage_decision, coverage_percentage,
                        analysis_result, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    f"COV_{int(time.time())}_{coverage_result.get('member_id', 'unknown')}",
                    coverage_result.get('member_id'),
                    coverage_result.get('policy_id'),
                    coverage_result.get('claim_type'),
                    coverage_result.get('coverage_decision'),
                    coverage_result.get('coverage_percentage'),
                    json.dumps(coverage_result),
                    datetime.now()
                ))
                conn.commit()
                return True
        except Exception as e:
            logger.error(f"Error storing coverage check: {str(e)}")
            return False
    
    def create_multi_line_tables(self):
        """Create tables for multi-line insurance (Motor, Marine, Domestic)"""
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                
                # Members table
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS members (
                        member_id TEXT PRIMARY KEY,
                        name TEXT,
                        email TEXT,
                        phone TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
                
                # Policies table
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS policies (
                        policy_id TEXT PRIMARY KEY,
                        member_id TEXT,
                        policy_type TEXT,
                        policy_number TEXT,
                        cover_type TEXT,
                        sum_insured REAL,
                        excess REAL,
                        start_date DATE,
                        end_date DATE,
                        policy_wording TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (member_id) REFERENCES members(member_id)
                    )
                ''')
                
                # Motor policy details
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS motor_policy_details (
                        policy_id TEXT PRIMARY KEY,
                        vehicle_make TEXT,
                        vehicle_model TEXT,
                        vehicle_year INTEGER,
                        registration_number TEXT,
                        authorised_drivers TEXT,
                        class_of_use TEXT,
                        tracking_required BOOLEAN,
                        FOREIGN KEY (policy_id) REFERENCES policies(policy_id)
                    )
                ''')
                
                # Marine policy details
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS marine_policy_details (
                        policy_id TEXT PRIMARY KEY,
                        cargo_description TEXT,
                        origin TEXT,
                        destination TEXT,
                        conveyance_type TEXT,
                        icc_clause TEXT,
                        packing_warranty BOOLEAN,
                        FOREIGN KEY (policy_id) REFERENCES policies(policy_id)
                    )
                ''')
                
                # Domestic policy details
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS domestic_policy_details (
                        policy_id TEXT PRIMARY KEY,
                        premises_address TEXT,
                        buildings_sum_insured REAL,
                        contents_sum_insured REAL,
                        valuables_limit REAL,
                        security_warranty TEXT,
                        FOREIGN KEY (policy_id) REFERENCES policies(policy_id)
                    )
                ''')
                
                # Coverage checks audit trail
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS coverage_checks (
                        check_id TEXT PRIMARY KEY,
                        member_id TEXT,
                        policy_id TEXT,
                        claim_type TEXT,
                        coverage_decision TEXT,
                        coverage_percentage INTEGER,
                        analysis_result TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (member_id) REFERENCES members(member_id),
                        FOREIGN KEY (policy_id) REFERENCES policies(policy_id)
                    )
                ''')
                
                # Assessors table
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS assessors (
                        assessor_id TEXT PRIMARY KEY,
                        name TEXT,
                        company TEXT,
                        specialization TEXT,
                        phone TEXT,
                        email TEXT,
                        total_assessments INTEGER DEFAULT 0,
                        avg_assessment_amount REAL DEFAULT 0,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
                
                # Repair shops table
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS repair_shops (
                        shop_id TEXT PRIMARY KEY,
                        name TEXT,
                        location TEXT,
                        phone TEXT,
                        total_claims INTEGER DEFAULT 0,
                        avg_claim_amount REAL DEFAULT 0,
                        fraud_flag_count INTEGER DEFAULT 0,
                        fraud_score REAL DEFAULT 0,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
                
                conn.commit()
                logger.info("✅ Multi-line insurance tables created successfully")
                
        except Exception as e:
            logger.error(f"❌ Error creating multi-line tables: {str(e)}")

    
    def create_lifecycle_tables(self, conn: sqlite3.Connection):
        """Create tables for claim lifecycle management"""
        cursor = conn.cursor() 
        # Assessors table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS assessors (
                assessor_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                email TEXT NOT NULL UNIQUE,
                phone TEXT NOT NULL,
                license_number TEXT,
                specialization TEXT NOT NULL,  -- motor/marine/domestic
                active_status INTEGER DEFAULT 1,
                current_workload INTEGER DEFAULT 0,
                max_workload INTEGER DEFAULT 10,
                rating REAL DEFAULT 5.0,
                total_assessments INTEGER DEFAULT 0,
                location TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Claim assignments table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS claim_assignments (
                assignment_id TEXT PRIMARY KEY,
                claim_id TEXT NOT NULL,
                assessor_id TEXT NOT NULL,
                assigned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                status TEXT DEFAULT 'pending',
                inspection_date TEXT,
                report_submitted_at TIMESTAMP,
                notes TEXT,
                FOREIGN KEY (assessor_id) REFERENCES assessors(assessor_id)
            )
        ''')
        
        # Claim status history table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS claim_status_history (
                history_id INTEGER PRIMARY KEY AUTOINCREMENT,
                claim_id TEXT NOT NULL,
                status TEXT NOT NULL,
                changed_by TEXT,
                changed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                notes TEXT
            )
        ''')
        
        # Coverage check history (link to claims)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS coverage_check_history (
                check_id TEXT PRIMARY KEY,
                member_id TEXT NOT NULL,
                policy_id TEXT,
                claim_type TEXT NOT NULL,
                coverage_decision TEXT NOT NULL,
                coverage_details TEXT,
                checked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                claim_id TEXT,  -- NULL until claim created
                FOREIGN KEY (claim_id) REFERENCES claims(claim_id)
            )
        ''')
        
        conn.commit()
        logger.info("✅ Lifecycle tables created successfully")

    def generate_claim_id(self, conn: sqlite3.Connection) -> str:
        """
        Generate unique claim ID
        Format: CLM-{YEAR}-{SEQUENCE}
        Example: CLM-2026-001234
        """
        current_year = datetime.now().year
        
        cursor = conn.cursor()
        cursor.execute('''
            SELECT COUNT(*) FROM claims 
            WHERE claim_id LIKE ?
        ''', (f'CLM-{current_year}-%',))
        
        count = cursor.fetchone()[0]
        sequence = count + 1
        
        claim_id = f"CLM-{current_year}-{sequence:06d}"
        
        logger.info(f"📋 Generated claim ID: {claim_id}")
        return claim_id


    def store_coverage_check_result(
        self,
        conn: sqlite3.Connection,
        check_id: str,
        member_id: str,
        policy_id: str,
        claim_type: str,
        coverage_decision: str,
        coverage_details: Dict
    ):
        """Store coverage check result"""
        
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO coverage_check_history 
            (check_id, member_id, policy_id, claim_type, coverage_decision, coverage_details)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (
            check_id,
            member_id,
            policy_id,
            claim_type,
            coverage_decision,
            json.dumps(coverage_details)
        ))
        
        conn.commit()
        logger.info(f"✅ Coverage check stored: {check_id}")


    def link_coverage_to_claim(
        self,
        conn: sqlite3.Connection,
        coverage_check_id: str,
        claim_id: str
    ):
        """Link coverage check to created claim"""
        
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE coverage_check_history
            SET claim_id = ?
            WHERE check_id = ?
        ''', (claim_id, coverage_check_id))
        
        conn.commit()


    def add_claim_status(
        self,
        conn: sqlite3.Connection,
        claim_id: str,
        status: str,
        changed_by: str = "system",
        notes: str = None
    ):
        """Add claim status change to history"""
        
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO claim_status_history (claim_id, status, changed_by, notes)
            VALUES (?, ?, ?, ?)
        ''', (claim_id, status, changed_by, notes))
        
        conn.commit()
        logger.info(f"📊 Claim {claim_id} status → {status}")


    def assign_assessor_to_claim(
        self,
        conn: sqlite3.Connection,
        claim_id: str,
        incident_location: str,
        claim_type: str
    ) -> Dict[str, Any]:
        """
        Auto-assign best available assessor
        Returns: {assigned: bool, assessor_id: str, assessor_name: str}
        """
        
        cursor = conn.cursor()
        
        # Extract city from location
        city = incident_location.split(',')[0].strip()
        
        # Find best assessor
        cursor.execute('''
            SELECT assessor_id, name, current_workload, rating, location
            FROM assessors
            WHERE active_status = 1
            AND specialization = ?
            AND (location LIKE ? OR location LIKE '%Nairobi%')
            AND current_workload < max_workload
            ORDER BY 
                CASE WHEN location LIKE ? THEN 0 ELSE 1 END,  -- Prefer same location
                current_workload ASC,
                rating DESC
            LIMIT 1
        ''', (claim_type, f'%{city}%', f'%{city}%'))
        
        assessor = cursor.fetchone()
        
        if not assessor:
            logger.warning(f"⚠️ No available assessors for {claim_type} in {city}")
            return {
                "assigned": False,
                "reason": f"No available {claim_type} assessors in {city}"
            }
        
        assessor_id = assessor['assessor_id']
        assessor_name = assessor['name']
        
        # Generate assignment ID
        import uuid
        assignment_id = f"ASG-{uuid.uuid4().hex[:8].upper()}"
        
        # Create assignment
        cursor.execute('''
            INSERT INTO claim_assignments (assignment_id, claim_id, assessor_id, status)
            VALUES (?, ?, ?, 'pending')
        ''', (assignment_id, claim_id, assessor_id))
        
        # Update assessor workload
        cursor.execute('''
            UPDATE assessors 
            SET current_workload = current_workload + 1
            WHERE assessor_id = ?
        ''', (assessor_id,))
        
        # Add status
        self.add_claim_status(conn, claim_id, 'assessor_assigned', 'system', 
                        f"Assigned to {assessor_name} ({assessor_id})")
        
        conn.commit()
        
        logger.info(f"✅ Claim {claim_id} assigned to {assessor_name} ({assessor_id})")
        
        return {
            "assigned": True,
            "assignment_id": assignment_id,
            "assessor_id": assessor_id,
            "assessor_name": assessor_name,
            "assessor_email": assessor['email'] if 'email' in assessor.keys() else None
        }


    def get_assessor_claims(
        self,
        conn: sqlite3.Connection,
        assessor_id: str,
        status: str = None
    ) -> List[Dict]:
        """Get claims assigned to an assessor"""
        
        cursor = conn.cursor()
        
        query = '''
            SELECT 
                ca.claim_id,
                ca.assignment_id,
                ca.assigned_at,
                ca.status as assignment_status,
                ca.inspection_date,
                c.location,
                c.estimated_cost,
                c.fraud_risk_score,
                c.risk_level,
                c.created_at,
                m.name as member_name,
                m.phone as member_phone
            FROM claim_assignments ca
            JOIN claims c ON ca.claim_id = c.claim_id
            LEFT JOIN members m ON c.member_id = m.member_id
            WHERE ca.assessor_id = ?
        '''
        
        params = [assessor_id]
        
        if status:
            query += ' AND ca.status = ?'
            params.append(status)
        
        query += ' ORDER BY ca.assigned_at DESC'
        
        cursor.execute(query, params)
        
        claims = []
        for row in cursor.fetchall():
            claims.append(dict(row))
        
        return claims


    def update_assignment_status(
        self,
        conn: sqlite3.Connection,
        assignment_id: str,
        status: str,
        inspection_date: str = None,
        notes: str = None
    ):
        """Update claim assignment status"""
        
        cursor = conn.cursor()
        
        if status == 'completed':
            cursor.execute('''
                UPDATE claim_assignments
                SET status = ?,
                    report_submitted_at = CURRENT_TIMESTAMP,
                    notes = ?
                WHERE assignment_id = ?
            ''', (status, notes, assignment_id))
            
            # Decrease assessor workload
            cursor.execute('''
                UPDATE assessors
                SET current_workload = current_workload - 1,
                    total_assessments = total_assessments + 1
                WHERE assessor_id = (
                    SELECT assessor_id FROM claim_assignments WHERE assignment_id = ?
                )
            ''', (assignment_id,))
            
        elif inspection_date:
            cursor.execute('''
                UPDATE claim_assignments
                SET status = ?,
                    inspection_date = ?,
                    notes = ?
                WHERE assignment_id = ?
            ''', (status, inspection_date, notes, assignment_id))
        else:
            cursor.execute('''
                UPDATE claim_assignments
                SET status = ?,
                    notes = ?
                WHERE assignment_id = ?
            ''', (status, notes, assignment_id))
        
        conn.commit()
        logger.info(f"✅ Assignment {assignment_id} status → {status}")

    def create_photo_storage_table(self):
        """Create table for storing photo files with party tracking"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS claim_photo_files (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    claim_id TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    party TEXT NOT NULL,  -- 'member', 'assessor', 'repair_shop'
                    file_data BLOB,  -- Store actual photo bytes
                    file_hash TEXT,  -- Store perceptual hash for quick lookup
                    file_size INTEGER,
                    content_type TEXT DEFAULT 'image/jpeg',
                    uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (claim_id) REFERENCES claims(claim_id),
                    UNIQUE(claim_id, filename, party)  -- Prevent duplicate uploads
                )
            ''')
            
            # Create index for faster lookups
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_photo_claim_party 
                ON claim_photo_files(claim_id, party)
            ''')
            
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_photo_hash 
                ON claim_photo_files(file_hash)
            ''')
            
            conn.commit()
            logger.info("✅ Photo storage table created/verified")
    
    def store_photo_file(
        self, 
        claim_id: str, 
        filename: str, 
        party: str, 
        file_data: bytes,
        file_hash: Optional[str] = None,
        content_type: str = 'image/jpeg'
    ) -> int:
        """
        Store photo file in database with party tracking
        
        Args:
            claim_id: Claim identifier
            filename: Original filename
            party: 'member', 'assessor', or 'repair_shop'
            file_data: Raw photo bytes
            file_hash: Optional perceptual hash (will compute if not provided)
            content_type: MIME type
            
        Returns:
            Photo ID
        """
        try:
            # Compute hash if not provided
            if not file_hash:
                file_hash = hashlib.md5(file_data).hexdigest()
            
            file_size = len(file_data)
            
            with self.get_connection() as conn:
                cursor = conn.cursor()
                
                # Insert or replace (handles duplicates)
                cursor.execute('''
                    INSERT OR REPLACE INTO claim_photo_files 
                    (claim_id, filename, party, file_data, file_hash, file_size, content_type)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (claim_id, filename, party, file_data, file_hash, file_size, content_type))
                
                conn.commit()
                photo_id = cursor.lastrowid
                
                logger.info(f"💾 Stored photo: {filename} for {party} in claim {claim_id} (ID: {photo_id}, Size: {file_size:,} bytes)")
                
                return photo_id
                
        except Exception as e:
            logger.error(f"❌ Error storing photo {filename}: {str(e)}")
            raise
    
    def get_photos_by_claim_and_party(self, claim_id: str, party: str) -> List[Dict]:
        """
        Retrieve all photos for a claim from specific party
        
        Args:
            claim_id: Claim identifier
            party: 'member', 'assessor', or 'repair_shop'
            
        Returns:
            List of photo dictionaries with file_data, filename, hash
        """
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT id, filename, file_data, file_hash, file_size, content_type, uploaded_at
                    FROM claim_photo_files
                    WHERE claim_id = ? AND party = ?
                    ORDER BY uploaded_at ASC
                ''', (claim_id, party))
                
                photos = []
                for row in cursor.fetchall():
                    photos.append({
                        'id': row['id'],
                        'filename': row['filename'],
                        'file_data': row['file_data'],
                        'file_hash': row['file_hash'],
                        'file_size': row['file_size'],
                        'content_type': row['content_type'],
                        'uploaded_at': row['uploaded_at']
                    })
                
                logger.info(f"📸 Retrieved {len(photos)} photos for {party} in claim {claim_id}")
                
                return photos
                
        except Exception as e:
            logger.error(f"❌ Error retrieving photos for claim {claim_id}, party {party}: {str(e)}")
            return []
    
    def get_all_photos_by_claim(self, claim_id: str) -> Dict[str, List[Dict]]:
        """
        Retrieve ALL photos for a claim, grouped by party
        
        Args:
            claim_id: Claim identifier
            
        Returns:
            Dictionary with keys 'member', 'assessor', 'repair_shop' containing photo lists
        """
        try:
            photos_by_party = {
                'member': [],
                'assessor': [],
                'repair_shop': []
            }
            
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT id, filename, file_data, file_hash, file_size, content_type, party, uploaded_at
                    FROM claim_photo_files
                    WHERE claim_id = ?
                    ORDER BY party, uploaded_at ASC
                ''', (claim_id,))
                
                for row in cursor.fetchall():
                    party = row['party']
                    if party in photos_by_party:
                        photos_by_party[party].append({
                            'id': row['id'],
                            'filename': row['filename'],
                            'file_data': row['file_data'],
                            'file_hash': row['file_hash'],
                            'file_size': row['file_size'],
                            'content_type': row['content_type'],
                            'uploaded_at': row['uploaded_at']
                        })
                
                total = sum(len(photos) for photos in photos_by_party.values())
                logger.info(f"📸 Retrieved {total} total photos for claim {claim_id} across all parties")
                
                return photos_by_party
                
        except Exception as e:
            logger.error(f"❌ Error retrieving all photos for claim {claim_id}: {str(e)}")
            return {'member': [], 'assessor': [], 'repair_shop': []}
    
    def check_duplicate_photo_across_parties(self, claim_id: str, file_hash: str, current_party: str) -> Optional[Dict]:
        """
        Check if this photo hash exists from a DIFFERENT party (fraud indicator)
        
        Args:
            claim_id: Claim identifier
            file_hash: Photo perceptual hash
            current_party: Party submitting this photo
            
        Returns:
            Dict with duplicate info if found, None otherwise
        """
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT id, filename, party, uploaded_at
                    FROM claim_photo_files
                    WHERE claim_id = ? AND file_hash = ? AND party != ?
                    LIMIT 1
                ''', (claim_id, file_hash, current_party))
                
                row = cursor.fetchone()
                
                if row:
                    logger.warning(f"🚨 DUPLICATE PHOTO DETECTED: Hash {file_hash[:12]}... from {current_party} matches {row['party']}")
                    return {
                        'duplicate_found': True,
                        'original_party': row['party'],
                        'original_filename': row['filename'],
                        'original_uploaded_at': row['uploaded_at']
                    }
                
                return None
                
        except Exception as e:
            logger.error(f"❌ Error checking duplicate photo: {str(e)}")
            return None
    
    def get_photo_count_by_party(self, claim_id: str) -> Dict[str, int]:
        """
        Get count of photos submitted by each party
        
        Args:
            claim_id: Claim identifier
            
        Returns:
            Dictionary with party counts
        """
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT party, COUNT(*) as count
                    FROM claim_photo_files
                    WHERE claim_id = ?
                    GROUP BY party
                ''', (claim_id,))
                
                counts = {'member': 0, 'assessor': 0, 'repair_shop': 0}
                
                for row in cursor.fetchall():
                    counts[row['party']] = row['count']
                
                return counts
                
        except Exception as e:
            logger.error(f"❌ Error getting photo counts: {str(e)}")
            return {'member': 0, 'assessor': 0, 'repair_shop': 0}
    
    def delete_photos_by_claim(self, claim_id: str) -> int:
        """
        Delete all photos for a claim (cleanup)
        
        Args:
            claim_id: Claim identifier
            
        Returns:
            Number of photos deleted
        """
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('DELETE FROM claim_photo_files WHERE claim_id = ?', (claim_id,))
                deleted_count = cursor.rowcount
                conn.commit()
                
                logger.info(f"🗑️ Deleted {deleted_count} photos for claim {claim_id}")
                
                return deleted_count
                
        except Exception as e:
            logger.error(f"❌ Error deleting photos for claim {claim_id}: {str(e)}")
            return 0

    def get_claim_status_history(
        self,
        conn: sqlite3.Connection,
        claim_id: str
    ) -> List[Dict]:
        """Get complete status history for a claim"""
        
        cursor = conn.cursor()
        cursor.execute('''
            SELECT status, changed_by, changed_at, notes
            FROM claim_status_history
            WHERE claim_id = ?
            ORDER BY changed_at ASC
        ''', (claim_id,))
        
        history = []
        for row in cursor.fetchall():
            history.append(dict(row))
        
        return history


    def populate_sample_assessors(self, conn: sqlite3.Connection):
        """Populate database with sample assessors"""
        
        assessors = [
            {
                "assessor_id": "ASS001",
                "name": "John Kamau",
                "email": "j.kamau@assessors.co.ke",
                "phone": "0722123456",
                "license_number": "AKI-MOT-2024-001",
                "specialization": "motor",
                "location": "Nairobi",
                "max_workload": 15,
                "rating": 4.8
            },
            {
                "assessor_id": "ASS002",
                "name": "Mary Wanjiku",
                "email": "m.wanjiku@assessors.co.ke",
                "phone": "0733234567",
                "license_number": "AKI-MOT-2024-002",
                "specialization": "motor",
                "location": "Nairobi",
                "max_workload": 12,
                "rating": 4.9
            },
            {
                "assessor_id": "ASS003",
                "name": "David Omondi",
                "email": "d.omondi@assessors.co.ke",
                "phone": "0744345678",
                "license_number": "AKI-MAR-2024-003",
                "specialization": "marine",
                "location": "Mombasa",
                "max_workload": 10,
                "rating": 4.7
            },
            {
                "assessor_id": "ASS004",
                "name": "Grace Mutua",
                "email": "g.mutua@assessors.co.ke",
                "phone": "0755456789",
                "license_number": "AKI-DOM-2024-004",
                "specialization": "domestic",
                "location": "Nairobi",
                "max_workload": 20,
                "rating": 4.6
            },
            {
                "assessor_id": "ASS005",
                "name": "Peter Kipchoge",
                "email": "p.kipchoge@assessors.co.ke",
                "phone": "0766567890",
                "license_number": "AKI-MOT-2024-005",
                "specialization": "motor",
                "location": "Kisumu",
                "max_workload": 10,
                "rating": 4.5
            }
        ]
        
        cursor = conn.cursor()
        
        for assessor in assessors:
            try:
                cursor.execute('''
                    INSERT OR IGNORE INTO assessors 
                    (assessor_id, name, email, phone, license_number, 
                    specialization, location, max_workload, rating)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    assessor['assessor_id'],
                    assessor['name'],
                    assessor['email'],
                    assessor['phone'],
                    assessor['license_number'],
                    assessor['specialization'],
                    assessor['location'],
                    assessor['max_workload'],
                    assessor['rating']
                ))
            except Exception as e:
                logger.error(f"Error inserting assessor {assessor['assessor_id']}: {str(e)}")
        
        conn.commit()
        logger.info(f"✅ Populated {len(assessors)} sample assessors")


    def cleanup_old_data(self, days: int = 30):
        """Clean up old data (useful for maintenance)"""
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                
                # Clean up old processing logs
                cursor.execute('''
                    DELETE FROM processing_logs 
                    WHERE created_at < datetime('now', '-{} days')
                '''.format(days))
                
                # Clean up old metrics
                cursor.execute('''
                    DELETE FROM system_metrics 
                    WHERE recorded_at < datetime('now', '-{} days')
                '''.format(days))
                
                conn.commit()
                logger.info(f"Cleaned up data older than {days} days")
                
        except Exception as e:
            logger.error(f"Error cleaning up old data: {str(e)}")
    
    def test_serialization(self, test_data: Any) -> bool:
        """Test if data can be serialized properly - useful for debugging"""
        try:
            serializable = self._make_json_serializable(test_data)
            json_str = json.dumps(serializable, default=str, ensure_ascii=False)
            logger.info(f"Serialization test passed. JSON length: {len(json_str)}")
            return True
        except Exception as e:
            logger.error(f"Serialization test failed: {str(e)}")
            return False


# Singleton instance
db_manager = DatabaseManager()