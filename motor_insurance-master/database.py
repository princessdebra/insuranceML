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

            # claim_assignments predates this DatabaseManager class (created by
            # an earlier seed/migration script) and has no inspection_location
            # column -- the assessor's chosen inspection venue was previously
            # only ever string-concatenated into `notes`, making it
            # unqueryable. ALTER TABLE ADD COLUMN has no "IF NOT EXISTS" in
            # SQLite, so this is guarded instead -- safe to run on every startup.
            try:
                cursor.execute('ALTER TABLE claim_assignments ADD COLUMN inspection_location TEXT')
                conn.commit()
                logger.info("Added inspection_location column to claim_assignments")
            except sqlite3.OperationalError as e:
                msg = str(e).lower()
                if "duplicate column" not in msg and "no such table" not in msg:
                    raise

            # Marks whether physics reconstruction found an assessor's
            # measured crush depth/angle to be materially inconsistent with
            # the narrative-extracted value or the CV-detected photo
            # severity -- used to build a per-assessor historical flag rate
            # (get_assessor_track_record) as a collusion-pattern signal.
            try:
                cursor.execute('ALTER TABLE claims ADD COLUMN measurement_discrepancy_flag INTEGER DEFAULT 0')
                conn.commit()
                logger.info("Added measurement_discrepancy_flag column to claims")
            except sqlite3.OperationalError as e:
                msg = str(e).lower()
                if "duplicate column" not in msg and "no such table" not in msg:
                    raise

            self.create_photo_storage_table()
            self.create_document_storage_table()

            # Admin-configurable overrides for business_rules.py's numeric
            # thresholds (e.g. the photo-metadata GPS variance distance, the
            # cost-to-sum-insured ratio bands) -- a simple key/value store
            # rather than one column per threshold, since business_rules.py
            # owns the actual set of keys and their defaults; this table just
            # persists whichever ones an admin has overridden. Missing keys
            # fall back to BusinessRulesEngine's DEFAULT_CONFIG.
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS system_config (
                    config_key TEXT PRIMARY KEY,
                    config_value TEXT NOT NULL,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_by TEXT
                )
            ''')

            # Lets the member/assessor who uploaded a document correct fields
            # OCR got wrong -- original raw_text/parsed_fields are never
            # overwritten (kept as the OCR ground truth for audit), the
            # correction is stored alongside it so admin can see both and a
            # self-serving "correction" that changes a fraud-relevant field
            # (e.g. an OB number) is visible, not silently trusted.
            for column, coltype in (
                ("corrected_fields", "TEXT"),
                ("corrected_by", "TEXT"),
                ("corrected_at", "TIMESTAMP"),
            ):
                try:
                    cursor.execute(f'ALTER TABLE claim_documents ADD COLUMN {column} {coltype}')
                    conn.commit()
                    logger.info(f"Added {column} column to claim_documents")
                except sqlite3.OperationalError as e:
                    msg = str(e).lower()
                    if "duplicate column" not in msg and "no such table" not in msg:
                        raise

            # Claims analysts -- Old Mutual staff who file a claim on a
            # member's behalf (e.g. taken over the phone), as distinct from
            # a member self-filing through their own app session.
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS analysts (
                    analyst_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    email TEXT NOT NULL UNIQUE,
                    phone TEXT NOT NULL,
                    department TEXT,
                    active_status INTEGER DEFAULT 1,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            # Traces who actually filed a claim -- a member through their
            # own session, or a staff analyst on the member's behalf (e.g.
            # phoned-in claims). Both filed_via values coexist with the
            # existing member_id/policy_id columns, which always identify
            # whose claim it is regardless of who typed it in.
            for column, coltype in (
                ("filed_by_analyst_id", "TEXT"),
                ("filed_via", "TEXT DEFAULT 'member_self'"),
            ):
                try:
                    cursor.execute(f'ALTER TABLE claims ADD COLUMN {column} {coltype}')
                    conn.commit()
                    logger.info(f"Added {column} column to claims")
                except sqlite3.OperationalError as e:
                    msg = str(e).lower()
                    if "duplicate column" not in msg and "no such table" not in msg:
                        raise

            # Final human triage decision on a claim -- PAY / DENY / ESCALATE.
            # `final_assessment.decision` in the admin full-report is a
            # recomputed AI suggestion (never persisted); this table is the
            # actual recorded business outcome, distinct from AI-ROL's
            # proceed/clarify/escalate/override vocabulary (which is about
            # how a handler treats one AI recommendation, not the claim's
            # final disposition). One claim can be re-decided (e.g. an
            # escalation later resolved to pay/deny), so this is append-only
            # -- callers read the latest row per claim_id.
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS claim_decisions (
                    decision_id TEXT PRIMARY KEY,
                    claim_id TEXT NOT NULL,
                    decision TEXT NOT NULL,          -- PAY, DENY, ESCALATE
                    reason TEXT,
                    payout_amount REAL,
                    decided_by TEXT NOT NULL,
                    decided_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (claim_id) REFERENCES claims (claim_id)
                )
            ''')
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_claim_decisions_claim_id ON claim_decisions (claim_id)
            ''')

            # Tracks who actually uploaded a photo (member_id or analyst_id),
            # distinct from `party` (which stays 'member' even when an
            # analyst adds photos on a member's behalf) -- needed for the
            # add-photos-to-an-existing-claim flow.
            try:
                cursor.execute('ALTER TABLE claim_photo_files ADD COLUMN uploaded_by TEXT')
                conn.commit()
                logger.info("Added uploaded_by column to claim_photo_files")
            except sqlite3.OperationalError as e:
                msg = str(e).lower()
                if "duplicate column" not in msg and "no such table" not in msg:
                    raise

            # Claim field keys the paper form left blank that the member is
            # being asked to fill in themselves (JSON list, e.g.
            # '["estimated_cost"]') -- set when an analyst's claim-form
            # upload notifies the member, cleared field-by-field as they
            # answer via update_claim_member_fields().
            try:
                cursor.execute('ALTER TABLE claims ADD COLUMN pending_member_fields TEXT')
                conn.commit()
                logger.info("Added pending_member_fields column to claims")
            except sqlite3.OperationalError as e:
                msg = str(e).lower()
                if "duplicate column" not in msg and "no such table" not in msg:
                    raise

            # AI's own independent repair-cost estimate from detected damage
            # zones (see part_identifier.estimate_damage_cost / the
            # cost_reasonableness business rule) -- kept separate from the
            # human-entered estimated_cost so the two can be compared.
            try:
                cursor.execute('ALTER TABLE claims ADD COLUMN ai_estimated_cost REAL')
                conn.commit()
                logger.info("Added ai_estimated_cost column to claims")
            except sqlite3.OperationalError as e:
                msg = str(e).lower()
                if "duplicate column" not in msg and "no such table" not in msg:
                    raise

            self.populate_sample_analysts(conn)

            # ── Enrichment: gives the stalled business rules (repair-shop
            # identity, cost revision, driver eligibility) and the new
            # relationship/benchmarking capabilities real synthetic data to
            # check against, instead of skipping themselves on every claim.
            for table, coltype in (
                ("claims", "repair_shop_id TEXT"),
                ("claims", "initial_estimated_cost REAL"),
                ("claims", "assessor_estimated_cost REAL"),
                ("claim_assignments", "return_reason TEXT"),
            ):
                try:
                    cursor.execute(f'ALTER TABLE {table} ADD COLUMN {coltype}')
                    conn.commit()
                    logger.info(f"Added {coltype.split()[0]} column to {table}")
                except sqlite3.OperationalError as e:
                    msg = str(e).lower()
                    if "duplicate column" not in msg and "no such table" not in msg:
                        raise
            try:
                cursor.execute('ALTER TABLE members ADD COLUMN bank_account TEXT')
                conn.commit()
                logger.info("Added bank_account column to members")
            except sqlite3.OperationalError as e:
                msg = str(e).lower()
                if "duplicate column" not in msg and "no such table" not in msg:
                    raise

            # PoC blueprint stress-test rule support: licence expiry
            # (BR-DRV-004), cover-upgrade timing (BR-UPG-006), and photo/
            # incident capture metadata for location+time variance
            # (BR-MET-008) -- none of these had a data source before.
            for table, coltype in (
                ("policy_drivers", "licence_expiry DATE"),
                ("motor_policy_details", "cover_upgrade_date DATE"),
                ("claim_photo_files", "capture_lat REAL"),
                ("claim_photo_files", "capture_lon REAL"),
                ("claim_photo_files", "capture_time TIMESTAMP"),
                ("claims", "incident_lat REAL"),
                ("claims", "incident_lon REAL"),
            ):
                try:
                    cursor.execute(f'ALTER TABLE {table} ADD COLUMN {coltype}')
                    conn.commit()
                    logger.info(f"Added {coltype.split()[0]} column to {table}")
                except sqlite3.OperationalError as e:
                    msg = str(e).lower()
                    if "duplicate column" not in msg and "no such table" not in msg:
                        raise

            # Structured authorised-driver data (age, licence class) --
            # motor_policy_details.authorised_drivers is just a free-text
            # string ("Policy Holder and Spouse"), not enough for the
            # driver-eligibility rule to check anything against.
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS policy_drivers (
                    driver_id TEXT PRIMARY KEY,
                    policy_id TEXT NOT NULL,
                    driver_name TEXT NOT NULL,
                    age INTEGER,
                    licence_class TEXT,
                    min_permitted_age INTEGER DEFAULT 18,
                    FOREIGN KEY (policy_id) REFERENCES policies (policy_id)
                )
            ''')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_policy_drivers_policy_id ON policy_drivers (policy_id)')

            # Reference corpus for narrative-similarity search -- deliberately
            # separate from the live `claims` table so it never shows up on
            # dashboards/claim lists, just as comparison material.
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS historical_narrative_corpus (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    narrative TEXT NOT NULL,
                    fraud_score INTEGER NOT NULL,
                    pattern_label TEXT,
                    claim_type TEXT DEFAULT 'motor'
                )
            ''')

            # Assessor's own repair/replace call per AI-detected damage
            # component -- "AI recommends, assessor decides." One row per
            # (claim, photo, detection); upserted so re-deciding overwrites
            # rather than accumulating duplicate rows.
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS photo_damage_decisions (
                    claim_id TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    detection_index INTEGER NOT NULL,
                    component TEXT,
                    ai_recommendation TEXT,
                    assessor_decision TEXT NOT NULL,
                    assessor_id TEXT NOT NULL,
                    decided_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (claim_id, filename, detection_index)
                )
            ''')

            # ------------------------------------------------------------
            # Domestic Package / Marine Hull / Marine Cargo / Goods in
            # Transit foundation -- new product lines alongside Motor. Each
            # *_policy_details table follows the same 1:1 policy_id PK/FK
            # convention as motor_policy_details above. marine_policy_details
            # and domestic_policy_details already exist on deployed DBs
            # (created outside this file's own init path historically) --
            # CREATE TABLE IF NOT EXISTS here is a no-op against their
            # existing shape there, and the correct place for a genuinely
            # fresh DB to get them; new columns for both are added via the
            # guarded ALTER loop further below rather than in the CREATE
            # statement, since IF NOT EXISTS won't retrofit an already-live
            # table.
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

            # Marine Hull -- vessel/navigation/survey data. Distinct table
            # from marine_policy_details (which is Cargo-shaped) since Hull's
            # fields (vessel, navigation area, survey/operator certificates)
            # have almost nothing in common with a shipment's.
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS marine_hull_policy_details (
                    policy_id TEXT PRIMARY KEY,
                    vessel_id TEXT,
                    registration_number TEXT,
                    description TEXT,
                    vessel_use TEXT,
                    navigation_area_description TEXT,
                    navigation_zone TEXT,
                    survey_valid_to DATE,
                    operator_certificate_valid_to DATE,
                    insured_value REAL,
                    deductible REAL,
                    FOREIGN KEY (policy_id) REFERENCES policies(policy_id)
                )
            ''')

            # Goods in Transit -- approved vehicle/driver/transporter and
            # conveyance limit, distinct from Marine Cargo (owner's-goods
            # interest over a defined transit vs a carrier/road-transport
            # policy insuring a specific conveyance).
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS goods_in_transit_policy_details (
                    policy_id TEXT PRIMARY KEY,
                    transit_id TEXT,
                    approved_vehicle_reg TEXT,
                    approved_driver_name TEXT,
                    approved_transporter_name TEXT,
                    commodity TEXT,
                    conveyance_limit REAL,
                    overnight_parking_warranty TEXT,
                    route_description TEXT,
                    FOREIGN KEY (policy_id) REFERENCES policies(policy_id)
                )
            ''')

            # Generalizes "separately selected/purchased" cover components --
            # Domestic's selectable sections (Buildings/Contents/All Risks/
            # Burglary/Liability/Domestic employees) and Marine's separately
            # purchased extensions (P&I, war risk, temperature extension)
            # both fit this one shape rather than needing per-product schema.
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS policy_sections (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    policy_id TEXT NOT NULL,
                    section_name TEXT NOT NULL,
                    limit_amount REAL,
                    excess REAL,
                    selected BOOLEAN DEFAULT 1,
                    effective_date DATE,
                    FOREIGN KEY (policy_id) REFERENCES policies(policy_id)
                )
            ''')

            # Individually scheduled Domestic items (e.g. a specified
            # laptop) -- a claimed item must match one of these on
            # description/serial/value, the same role motor_policy_details'
            # vehicle fields play for a Motor claim.
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS domestic_specified_items (
                    item_id TEXT PRIMARY KEY,
                    policy_id TEXT NOT NULL,
                    description TEXT,
                    serial_number TEXT,
                    scheduled_value REAL,
                    FOREIGN KEY (policy_id) REFERENCES policies(policy_id)
                )
            ''')

            # AIS/GPS tracks, telematics logs, temperature-logger data --
            # evidence files for Hull collision/grounding, Goods in Transit
            # overturning, and Cargo temperature-excursion claims. Same
            # raw-file-plus-extracted-JSON shape as claim_documents/
            # claim_photo_files rather than a dedicated time-series store.
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS claim_tracking_data (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    claim_id TEXT NOT NULL,
                    party TEXT NOT NULL,
                    data_type TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    file_data BLOB,
                    parsed_summary TEXT,
                    uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (claim_id) REFERENCES claims(claim_id)
                )
            ''')

            # New columns on the two pre-existing *_policy_details tables --
            # same guarded-ALTER pattern as the block below (duplicate-column
            # and no-such-table are both fine to swallow).
            for table, coltype in (
                ("domestic_policy_details", "fuel_storage_wording TEXT"),
                ("domestic_policy_details", "contents_limit_changed_at DATE"),
                ("domestic_policy_details", "contents_limit_previous REAL"),
                ("marine_policy_details", "cover_type TEXT"),
                ("marine_policy_details", "clause TEXT"),
                ("marine_policy_details", "shipment_id TEXT"),
                ("marine_policy_details", "declaration_reference TEXT"),
            ):
                try:
                    cursor.execute(f'ALTER TABLE {table} ADD COLUMN {coltype}')
                    conn.commit()
                    logger.info(f"Added {coltype.split()[0]} column to {table}")
                except sqlite3.OperationalError as e:
                    msg = str(e).lower()
                    if "duplicate column" not in msg and "no such table" not in msg:
                        raise
            # ------------------------------------------------------------

            # These depend on `policies`/`members` existing, which are only
            # created by the separate migrate.py seeding script, not by
            # init_database() itself -- guard so a fresh/unusual DB state
            # doesn't crash the whole startup.
            for enrich_fn in (
                self.populate_policy_drivers,
                self.populate_member_bank_accounts,
                self.populate_historical_narrative_corpus,
            ):
                try:
                    enrich_fn(conn)
                except sqlite3.OperationalError as e:
                    logger.warning(f"Skipped {enrich_fn.__name__} — {e}")

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
                #
                # narrative/estimated_cost/location additionally CASE-guard against
                # overwriting a real value with a blank one: the background
                # analysis pipeline captures these at submission time and can take
                # minutes (physics reconstruction), so if a member answers a
                # pending_member_fields question (update_claim_member_fields)
                # WHILE it's still running, this upsert landing afterward would
                # otherwise silently clobber that answer back to blank/0 using its
                # now-stale in-memory copy.
                cursor.execute('''
                    INSERT INTO claims
                    (claim_id, narrative, estimated_cost, location, accident_time,
                     fraud_risk_score, risk_level, processing_time_ms, analysis_result, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(claim_id) DO UPDATE SET
                        narrative           = CASE WHEN excluded.narrative != '' THEN excluded.narrative ELSE claims.narrative END,
                        estimated_cost      = CASE WHEN excluded.estimated_cost > 0 THEN excluded.estimated_cost ELSE claims.estimated_cost END,
                        location            = CASE WHEN excluded.location != '' THEN excluded.location ELSE claims.location END,
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
                
                # SELECT * rather than an explicit column list -- the
                # explicit list silently dropped every column added to
                # `claims` after it was written (repair_shop_id,
                # assessor_estimated_cost, initial_estimated_cost,
                # filed_by_analyst_id, filed_via, the physics columns...),
                # so every caller of get_claim() got None back for those
                # fields even when the database genuinely had a value.
                cursor.execute('SELECT * FROM claims WHERE claim_id = ?', (claim_id,))
                
                row = cursor.fetchone()
                
                if not row:
                    return None
                
                return dict(row)
                
        except Exception as e:
            logger.error(f"Error retrieving claim: {str(e)}")
            return None

    def get_system_config_overrides(self) -> Dict[str, Any]:
        """
        Raw admin-set overrides for business_rules.py's thresholds -- just
        the keys an admin has actually changed, not merged with defaults
        (BusinessRulesEngine does that merge itself, since it owns the
        default values). Values are stored as JSON so both numbers and
        strings round-trip without extra parsing here.
        """
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT config_key, config_value FROM system_config")
                return {row["config_key"]: json.loads(row["config_value"]) for row in cursor.fetchall()}
        except Exception as e:
            logger.error(f"Error retrieving system config overrides: {str(e)}")
            return {}

    def set_system_config_overrides(self, updates: Dict[str, Any], updated_by: Optional[str] = None) -> None:
        """Upserts one or more business-rule threshold overrides -- called
        from the admin settings page. A value of None removes that key's
        override, reverting it back to BusinessRulesEngine's default."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            for key, value in updates.items():
                if value is None:
                    cursor.execute("DELETE FROM system_config WHERE config_key = ?", (key,))
                else:
                    cursor.execute(
                        """
                        INSERT INTO system_config (config_key, config_value, updated_at, updated_by)
                        VALUES (?, ?, CURRENT_TIMESTAMP, ?)
                        ON CONFLICT(config_key) DO UPDATE SET
                            config_value = excluded.config_value,
                            updated_at = excluded.updated_at,
                            updated_by = excluded.updated_by
                        """,
                        (key, json.dumps(value), updated_by),
                    )
            conn.commit()

    def list_assessor_capacity(self) -> List[Dict[str, Any]]:
        """Assessor roster with workload/capacity, for the Settings page's
        capacity table -- lets an analyst see who's maxed out before it
        silently blocks auto-assignment."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT assessor_id, name, location, specialization,
                       active_status, current_workload, max_workload, rating
                FROM assessors
                ORDER BY specialization, location, name
            ''')
            return [dict(row) for row in cursor.fetchall()]

    def update_assessor_capacity(self, assessor_id: str, max_workload: int) -> Dict[str, Any]:
        """Raises/lowers one assessor's max_workload cap -- called from the
        analyst Settings page when auto-assignment dead-ends because every
        assessor for a location+specialization is at capacity."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT assessor_id FROM assessors WHERE assessor_id = ?", (assessor_id,))
            if not cursor.fetchone():
                return {"success": False, "reason": f"Assessor {assessor_id} not found"}
            cursor.execute(
                "UPDATE assessors SET max_workload = ? WHERE assessor_id = ?",
                (max_workload, assessor_id),
            )
            conn.commit()
            return {"success": True, "assessor_id": assessor_id, "max_workload": max_workload}

    MEMBER_EDITABLE_CLAIM_FIELDS = {"estimated_cost", "location", "narrative"}

    def update_claim_member_fields(self, claim_id: str, member_id: str, fields: Dict[str, Any]) -> Dict[str, Any]:
        """
        Member answers questions the paper claim form left blank (e.g.
        Estimated Repair Cost). Restricted to MEMBER_EDITABLE_CLAIM_FIELDS --
        the same columns notify_member_to_add_photos can flag as pending --
        so this can't be used to rewrite arbitrary claim state.
        """
        claim = self.get_claim(claim_id)
        if not claim:
            raise ValueError(f"Claim {claim_id} not found")
        if claim.get("member_id") != member_id:
            raise PermissionError("This claim does not belong to you")

        updates = {
            k: v for k, v in fields.items()
            if k in self.MEMBER_EDITABLE_CLAIM_FIELDS and v not in (None, "")
        }

        try:
            pending = json.loads(claim.get("pending_member_fields") or "[]")
        except (json.JSONDecodeError, TypeError):
            pending = []
        remaining = [f for f in pending if f not in updates]

        if not updates:
            return {"updated": [], "pending_member_fields": remaining}

        with self.get_connection() as conn:
            cursor = conn.cursor()
            set_clause = ", ".join(f"{k} = ?" for k in updates)
            cursor.execute(
                f'UPDATE claims SET {set_clause}, pending_member_fields = ?, updated_at = CURRENT_TIMESTAMP WHERE claim_id = ?',
                (*updates.values(), json.dumps(remaining), claim_id),
            )
            conn.commit()

        return {"updated": list(updates.keys()), "pending_member_fields": remaining}

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
    
    def search_members(self, query: str, limit: int = 10) -> List[Dict]:
        """
        Look up members by partial name, phone, email, or exact member_id --
        needed for an analyst filing a claim on behalf of a caller, who
        won't know their own member_id. Self-filing members never need this
        (they're already logged in as themselves).
        """
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                like = f"%{query}%"
                cursor.execute('''
                    SELECT member_id, name, email, phone
                    FROM members
                    WHERE member_id = ? OR name LIKE ? OR phone LIKE ? OR email LIKE ?
                    ORDER BY name
                    LIMIT ?
                ''', (query, like, like, like, limit))
                return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"Error searching members for '{query}': {str(e)}")
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
                logger.info("Multi-line insurance tables created successfully")
                
        except Exception as e:
            logger.error(f"Error creating multi-line tables: {str(e)}")

    
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
        logger.info("Lifecycle tables created successfully")

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
        
        logger.info(f"Generated claim ID: {claim_id}")
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
        logger.info(f"Coverage check stored: {check_id}")


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
        logger.info(f"Claim {claim_id} status → {status}")


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

        # claim_type's vocabulary (motor/marine_hull/marine_cargo/
        # goods_in_transit/domestic -- see routes.py's _resolve_claim_type)
        # is more specific than assessors.specialization currently is --
        # the assessor roster only has broad motor/marine/domestic
        # specialists seeded. A strict equality match here would find zero
        # assessors for every Marine sub-type. Accept an assessor whose
        # specialization matches the claim type exactly (a true Hull/Cargo/
        # GIT specialist, if one's ever added) OR the broader "marine"
        # specialization as a fallback, same spirit as the existing
        # Nairobi-fallback for location -- and rank an exact specialist
        # ahead of the generic fallback when both are available.
        _SPECIALIZATION_FALLBACKS = {
            "marine_hull": ("marine_hull", "marine"),
            "marine_cargo": ("marine_cargo", "marine"),
            "goods_in_transit": ("goods_in_transit", "marine"),
        }
        acceptable_specializations = _SPECIALIZATION_FALLBACKS.get(claim_type, (claim_type,))
        placeholders = ", ".join("?" for _ in acceptable_specializations)

        # Find best assessor
        cursor.execute(f'''
            SELECT assessor_id, name, current_workload, rating, location, specialization
            FROM assessors
            WHERE active_status = 1
            AND specialization IN ({placeholders})
            AND (location LIKE ? OR location LIKE '%Nairobi%')
            AND current_workload < max_workload
            ORDER BY
                CASE WHEN specialization = ? THEN 0 ELSE 1 END,  -- Prefer an exact specialist
                CASE WHEN location LIKE ? THEN 0 ELSE 1 END,  -- Prefer same location
                current_workload ASC,
                rating DESC
            LIMIT 1
        ''', (*acceptable_specializations, f'%{city}%', claim_type, f'%{city}%'))

        assessor = cursor.fetchone()
        
        if not assessor:
            logger.warning(f"No available assessors for {claim_type} in {city}")
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
        
        logger.info(f"Claim {claim_id} assigned to {assessor_name} ({assessor_id})")
        
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
                ca.inspection_location,
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

    def get_assessor_dashboard_overview(self, assessor_id: str) -> Dict[str, Any]:
        """
        Operational widgets for the assessor's own dashboard -- deliberately
        no fraud/risk figures (those stay out of the assessor's view). Status
        model in use: 'pending' (not yet reported on) -> 'completed' (report
        submitted), with 'returned_for_review' as a side-branch an admin can
        send a submitted report back into. inspection_date is free text set
        by the assessor when scheduling, so "awaiting submission"/"overdue"
        are derived by comparing it to today rather than a strict status.
        """
        def _parse_dt(s):
            if not s:
                return None
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
                try:
                    return datetime.strptime(s, fmt)
                except (ValueError, TypeError):
                    continue
            try:
                return datetime.strptime(str(s)[:10], "%Y-%m-%d")
            except Exception:
                return None

        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT ca.status, ca.assigned_at, ca.inspection_date, ca.report_submitted_at,
                           c.estimated_cost
                    FROM claim_assignments ca
                    JOIN claims c ON ca.claim_id = c.claim_id
                    WHERE ca.assessor_id = ?
                ''', (assessor_id,))
                rows = [dict(r) for r in cursor.fetchall()]

            today = datetime.now().date()
            pending_inspections = 0
            scheduled_today = 0
            awaiting_submission = 0
            overdue = 0
            completed = 0
            returned_for_review = 0
            turnaround_hours = []
            claims_by_status: Dict[str, int] = {}
            total_value = 0.0

            for r in rows:
                status = r["status"] or "pending"
                claims_by_status[status] = claims_by_status.get(status, 0) + 1
                total_value += r["estimated_cost"] or 0
                insp_dt = _parse_dt(r["inspection_date"])

                if status == "completed":
                    completed += 1
                    assigned_dt = _parse_dt(r["assigned_at"])
                    submitted_dt = _parse_dt(r["report_submitted_at"])
                    if assigned_dt and submitted_dt:
                        turnaround_hours.append((submitted_dt - assigned_dt).total_seconds() / 3600)
                elif status == "returned_for_review":
                    returned_for_review += 1
                elif status in ("pending", "in_progress"):
                    if insp_dt is None or insp_dt.date() > today:
                        pending_inspections += 1
                    elif insp_dt.date() == today:
                        scheduled_today += 1
                    else:
                        awaiting_submission += 1
                        if (today - insp_dt.date()).days >= 2:
                            overdue += 1

            avg_turnaround = round(sum(turnaround_hours) / len(turnaround_hours), 1) if turnaround_hours else None

            return {
                "success": True,
                "total_claims": len(rows),
                "pending_inspections": pending_inspections,
                "inspections_scheduled_today": scheduled_today,
                "reports_awaiting_submission": awaiting_submission,
                "reports_returned_for_review": returned_for_review,
                "completed_assessments": completed,
                "overdue_assessments": overdue,
                "avg_turnaround_hours": avg_turnaround,
                "estimated_claim_value_total": round(total_value, 2),
                "estimated_claim_value_avg": round(total_value / len(rows), 2) if rows else 0.0,
                "claims_by_status": claims_by_status,
            }
        except Exception as e:
            logger.error(f"Error computing assessor dashboard overview for {assessor_id}: {str(e)}")
            return {"success": False, "error": str(e)}

    def return_assignment_for_review(self, assignment_id: str, reason: str, returned_by: str) -> bool:
        """Admin sends a submitted report back to the assessor for revision."""
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    UPDATE claim_assignments
                    SET status = 'returned_for_review', return_reason = ?
                    WHERE assignment_id = ?
                ''', (reason, assignment_id))
                conn.commit()
                logger.info(f"Assignment {assignment_id} returned for review by {returned_by}: {reason}")
                return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"Error returning assignment {assignment_id} for review: {str(e)}")
            return False

    def get_assessor_track_record(self, assessor_id: str) -> Dict[str, Any]:
        """
        Historical measurement-discrepancy rate for an assessor, across every
        claim they've assessed that physics has actually run on. Used as a
        collusion-pattern signal: a single flagged claim might be a genuine
        edge case, but an assessor whose overrides are flagged unusually
        often, across many claims, is a fraud signal in its own right.
        """
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT c.physics_fraud_score, c.measurement_discrepancy_flag
                    FROM claims c
                    JOIN claim_assignments ca ON c.claim_id = ca.claim_id
                    WHERE ca.assessor_id = ? AND c.physics_fraud_score IS NOT NULL
                ''', (assessor_id,))
                rows = cursor.fetchall()

            total = len(rows)
            flagged = sum(1 for r in rows if r["measurement_discrepancy_flag"])
            avg_fraud_score = (sum(r["physics_fraud_score"] or 0 for r in rows) / total) if total else 0.0

            return {
                "assessor_id": assessor_id,
                "total_assessed_claims": total,
                "flagged_measurement_discrepancies": flagged,
                "flagged_rate_pct": round(flagged / total * 100, 1) if total else 0.0,
                "avg_physics_fraud_score": round(avg_fraud_score, 1),
            }
        except Exception as e:
            logger.error(f"Error computing assessor track record for {assessor_id}: {str(e)}")
            return {
                "assessor_id": assessor_id,
                "total_assessed_claims": 0,
                "flagged_measurement_discrepancies": 0,
                "flagged_rate_pct": 0.0,
                "avg_physics_fraud_score": 0.0,
            }

    def update_assignment_status(
        self,
        conn: sqlite3.Connection,
        assignment_id: str,
        status: str,
        inspection_date: str = None,
        notes: str = None,
        inspection_location: str = None
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
                    notes = ?,
                    inspection_location = ?
                WHERE assignment_id = ?
            ''', (status, inspection_date, notes, inspection_location, assignment_id))
        else:
            cursor.execute('''
                UPDATE claim_assignments
                SET status = ?,
                    notes = ?
                WHERE assignment_id = ?
            ''', (status, notes, assignment_id))

        conn.commit()
        logger.info(f"Assignment {assignment_id} status → {status}")

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
            logger.info("Photo storage table created/verified")

    def create_document_storage_table(self):
        """
        Create table for storing supporting documents (police abstracts, ID
        documents, garage quotes) and their OCR-extracted data, uploaded by
        either the member or the assessor.
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS claim_documents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    claim_id TEXT NOT NULL,
                    party TEXT NOT NULL,  -- 'member', 'assessor', 'repair_shop'
                    document_type TEXT NOT NULL,  -- 'police_abstract', 'id_document', 'garage_quote', 'other'
                    filename TEXT NOT NULL,
                    file_data BLOB,
                    content_type TEXT DEFAULT 'image/jpeg',
                    raw_text TEXT,
                    parsed_fields TEXT,  -- JSON string
                    extraction_confidence REAL DEFAULT 0,
                    extraction_method TEXT,
                    uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (claim_id) REFERENCES claims(claim_id)
                )
            ''')

            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_document_claim
                ON claim_documents(claim_id)
            ''')

            conn.commit()
            logger.info("Document storage table created/verified")

    def store_document(
        self,
        claim_id: str,
        party: str,
        document_type: str,
        filename: str,
        file_data: bytes,
        ocr_result: Dict[str, Any],
        content_type: str = 'image/jpeg',
    ) -> int:
        """Store an uploaded document plus its OCR extraction result."""
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT INTO claim_documents
                    (claim_id, party, document_type, filename, file_data, content_type,
                     raw_text, parsed_fields, extraction_confidence, extraction_method)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    claim_id, party, document_type, filename, file_data, content_type,
                    ocr_result.get("raw_text", ""),
                    json.dumps(ocr_result.get("parsed_fields", {}), ensure_ascii=False),
                    ocr_result.get("extraction_confidence", 0),
                    ocr_result.get("extraction_method", "unknown"),
                ))
                conn.commit()
                doc_id = cursor.lastrowid
                logger.info(
                    f"Stored document: {filename} ({document_type}) for {party}"
                    f"in claim {claim_id} (ID: {doc_id}, confidence: {ocr_result.get('extraction_confidence', 0)}%)"
                )
                return doc_id
        except Exception as e:
            logger.error(f"Error storing document {filename}: {str(e)}")
            raise

    def get_documents_by_claim(self, claim_id: str, party: Optional[str] = None) -> List[Dict]:
        """Retrieve all documents (with parsed OCR data) for a claim, optionally filtered by party."""
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                query = '''
                    SELECT id, claim_id, party, document_type, filename, raw_text,
                           parsed_fields, extraction_confidence, extraction_method, uploaded_at,
                           corrected_fields, corrected_by, corrected_at
                    FROM claim_documents
                    WHERE claim_id = ?
                '''
                params = [claim_id]
                if party:
                    query += ' AND party = ?'
                    params.append(party)
                query += ' ORDER BY uploaded_at ASC'

                cursor.execute(query, params)
                documents = []
                for row in cursor.fetchall():
                    doc = dict(row)
                    try:
                        doc['parsed_fields'] = json.loads(doc['parsed_fields']) if doc['parsed_fields'] else {}
                    except (json.JSONDecodeError, TypeError):
                        doc['parsed_fields'] = {}
                    try:
                        doc['corrected_fields'] = json.loads(doc['corrected_fields']) if doc['corrected_fields'] else None
                    except (json.JSONDecodeError, TypeError):
                        doc['corrected_fields'] = None
                    documents.append(doc)

                return documents
        except Exception as e:
            logger.error(f"Error retrieving documents for claim {claim_id}: {str(e)}")
            return []

    def get_document_by_id(self, document_id: int) -> Optional[Dict]:
        """Fetch a single document row (for ownership checks before allowing a correction)."""
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    'SELECT id, claim_id, party, document_type, filename FROM claim_documents WHERE id = ?',
                    (document_id,)
                )
                row = cursor.fetchone()
                return dict(row) if row else None
        except Exception as e:
            logger.error(f"Error fetching document {document_id}: {str(e)}")
            return None

    def correct_document_fields(self, document_id: int, corrected_fields: Dict[str, Any], corrected_by: str) -> bool:
        """
        Store a human correction to a document's OCR-extracted fields.
        The original raw_text/parsed_fields columns are left untouched --
        the correction is stored separately so a reviewer can always see
        what OCR actually read versus what the uploader says it should be.
        """
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    UPDATE claim_documents
                    SET corrected_fields = ?, corrected_by = ?, corrected_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                ''', (json.dumps(corrected_fields, ensure_ascii=False), corrected_by, document_id))
                conn.commit()
                logger.info(f"Document {document_id} fields corrected by {corrected_by}")
                return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"Error correcting document {document_id}: {str(e)}")
            raise

    def store_tracking_data(
        self,
        claim_id: str,
        party: str,
        data_type: str,
        filename: str,
        file_data: bytes,
        parsed_summary: Optional[Dict[str, Any]] = None,
    ) -> int:
        """
        Store an AIS/GPS track, telematics log, or temperature-logger file --
        evidence for Hull collision/grounding (BR-MET-008's metadata
        comparison), Goods in Transit overturning, and Cargo temperature-
        excursion claims. Same raw-file-plus-extracted-JSON shape as
        store_document, deliberately not a dedicated time-series store --
        actual row-by-row parsing of the CSV into structured points is a
        later phase; this just gets the file safely attached to the claim
        so the file itself is retrievable, with parsed_summary left null
        until that parsing exists.
        """
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT INTO claim_tracking_data (claim_id, party, data_type, filename, file_data, parsed_summary)
                    VALUES (?, ?, ?, ?, ?, ?)
                ''', (
                    claim_id, party, data_type, filename, file_data,
                    json.dumps(parsed_summary, ensure_ascii=False) if parsed_summary else None,
                ))
                conn.commit()
                tracking_id = cursor.lastrowid
                logger.info(f"Stored tracking data: {filename} ({data_type}) for {party} in claim {claim_id} (ID: {tracking_id})")
                return tracking_id
        except Exception as e:
            logger.error(f"Error storing tracking data {filename}: {str(e)}")
            raise

    def get_tracking_data_by_claim(self, claim_id: str, party: Optional[str] = None) -> List[Dict]:
        """Retrieve tracking-data records (without the raw file bytes) for a claim, optionally filtered by party."""
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                query = '''
                    SELECT id, claim_id, party, data_type, filename, parsed_summary, uploaded_at
                    FROM claim_tracking_data
                    WHERE claim_id = ?
                '''
                params = [claim_id]
                if party:
                    query += ' AND party = ?'
                    params.append(party)
                query += ' ORDER BY uploaded_at ASC'

                cursor.execute(query, params)
                records = []
                for row in cursor.fetchall():
                    rec = dict(row)
                    try:
                        rec['parsed_summary'] = json.loads(rec['parsed_summary']) if rec['parsed_summary'] else None
                    except (json.JSONDecodeError, TypeError):
                        rec['parsed_summary'] = None
                    records.append(rec)
                return records
        except Exception as e:
            logger.error(f"Error retrieving tracking data for claim {claim_id}: {str(e)}")
            return []

    def store_photo_file(
        self,
        claim_id: str,
        filename: str,
        party: str,
        file_data: bytes,
        file_hash: Optional[str] = None,
        content_type: str = 'image/jpeg',
        uploaded_by: Optional[str] = None,
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
            uploaded_by: member_id or analyst_id of whoever actually did the
                upload -- distinct from `party`, since an analyst adding
                photos on a member's behalf still counts as the member's
                evidence (party='member') but isn't literally the member
                doing the uploading.

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
                    (claim_id, filename, party, file_data, file_hash, file_size, content_type, uploaded_by)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ''', (claim_id, filename, party, file_data, file_hash, file_size, content_type, uploaded_by))

                conn.commit()
                photo_id = cursor.lastrowid

                logger.info(f"Stored photo: {filename} for {party} in claim {claim_id} (ID: {photo_id}, Size: {file_size:,} bytes)")

                return photo_id

        except Exception as e:
            logger.error(f"Error storing photo {filename}: {str(e)}")
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
                
                logger.info(f"Retrieved {len(photos)} photos for {party} in claim {claim_id}")
                
                return photos
                
        except Exception as e:
            logger.error(f"Error retrieving photos for claim {claim_id}, party {party}: {str(e)}")
            return []
    
    def get_photos_metadata_by_claim(self, claim_id: str, party: Optional[str] = None) -> List[Dict]:
        """
        Lightweight photo listing (no file_data blob) for the
        add-photos-to-an-existing-claim flow -- callers that need the raw
        bytes should use get_all_photos_by_claim instead.
        """
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                query = '''
                    SELECT id, claim_id, filename, party, file_size, content_type, uploaded_at, uploaded_by
                    FROM claim_photo_files
                    WHERE claim_id = ?
                '''
                params = [claim_id]
                if party:
                    query += ' AND party = ?'
                    params.append(party)
                query += ' ORDER BY uploaded_at ASC'
                cursor.execute(query, params)
                return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"Error fetching photo metadata for claim {claim_id}: {str(e)}")
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
                logger.info(f"Retrieved {total} total photos for claim {claim_id} across all parties")
                
                return photos_by_party
                
        except Exception as e:
            logger.error(f"Error retrieving all photos for claim {claim_id}: {str(e)}")
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
                    logger.warning(f"DUPLICATE PHOTO DETECTED: Hash {file_hash[:12]}... from {current_party} matches {row['party']}")
                    return {
                        'duplicate_found': True,
                        'original_party': row['party'],
                        'original_filename': row['filename'],
                        'original_uploaded_at': row['uploaded_at']
                    }
                
                return None
                
        except Exception as e:
            logger.error(f"Error checking duplicate photo: {str(e)}")
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
            logger.error(f"Error getting photo counts: {str(e)}")
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
                
                logger.info(f"Deleted {deleted_count} photos for claim {claim_id}")
                
                return deleted_count
                
        except Exception as e:
            logger.error(f"Error deleting photos for claim {claim_id}: {str(e)}")
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
        logger.info(f"Populated {len(assessors)} sample assessors")

    def populate_sample_analysts(self, conn: sqlite3.Connection):
        """Populate database with sample claims analysts"""
        analysts = [
            {"analyst_id": "ANL001", "name": "Susan Achieng", "email": "s.achieng@oldmutual.co.ke", "phone": "0711000201", "department": "Motor Claims"},
            {"analyst_id": "ANL002", "name": "Brian Otieno", "email": "b.otieno@oldmutual.co.ke", "phone": "0711000202", "department": "Motor Claims"},
            {"analyst_id": "ANL003", "name": "Faith Chebet", "email": "f.chebet@oldmutual.co.ke", "phone": "0711000203", "department": "Contact Centre"},
        ]
        cursor = conn.cursor()
        for a in analysts:
            try:
                cursor.execute('''
                    INSERT OR IGNORE INTO analysts (analyst_id, name, email, phone, department)
                    VALUES (?, ?, ?, ?, ?)
                ''', (a['analyst_id'], a['name'], a['email'], a['phone'], a['department']))
            except Exception as e:
                logger.error(f"Error inserting analyst {a['analyst_id']}: {str(e)}")
        conn.commit()
        logger.info(f"Populated {len(analysts)} sample analysts")

    def populate_policy_drivers(self, conn: sqlite3.Connection):
        """
        Structured authorised-driver data (age, licence class) for the
        driver-eligibility business rule to check against. The existing
        motor_policy_details.authorised_drivers is just the free-text
        string "Policy Holder and Spouse" on every policy -- this gives
        each policy real named drivers, deliberately including a couple
        of scenarios (under-age, mismatched licence class) the rule
        should actually catch.
        """
        cursor = conn.cursor()
        cursor.execute('SELECT COUNT(*) as cnt FROM policy_drivers')
        if cursor.fetchone()['cnt'] > 0:
            return

        drivers = [
            ("POL001", "James Mwangi", 41, "BCE"),
            ("POL001", "Grace Mwangi", 38, "BCE"),
            ("POL002", "Esther Akinyi", 34, "BCE"),
            ("POL003", "Brian Otieno", 29, "BCE"),
            # Deliberately under the standard 18-year minimum -- exercises
            # the driver_eligibility rule's age check.
            ("POL003", "Kevin Otieno", 17, "F"),
            ("POL004", "Caroline Njeri", 45, "BCE"),
            ("POL005", "Samuel Kipkemoi", 52, "BCE"),
            # Licence class F is provisional/learner -- not a class that
            # should be driving unsupervised; mismatched against a policy
            # that only permits BCE.
            ("POL006", "Faith Wambua", 22, "F"),
            ("POL007", "Kevin Odhiambo", 31, "BCE"),
            ("POL008", "Lydia Chebet", 27, "BCE"),
        ]
        for policy_id, name, age, licence_class in drivers:
            driver_id = f"DRV-{uuid.uuid4().hex[:10].upper()}"
            try:
                cursor.execute('''
                    INSERT INTO policy_drivers (driver_id, policy_id, driver_name, age, licence_class, min_permitted_age)
                    VALUES (?, ?, ?, ?, ?, 18)
                ''', (driver_id, policy_id, name, age, licence_class))
            except Exception as e:
                logger.error(f"Error inserting policy driver {name} on {policy_id}: {str(e)}")
        conn.commit()
        logger.info(f"Populated {len(drivers)} policy drivers")

    def save_photo_damage_decision(
        self, claim_id: str, filename: str, detection_index: int,
        component: str, ai_recommendation: str, assessor_decision: str, assessor_id: str,
    ) -> bool:
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT INTO photo_damage_decisions
                    (claim_id, filename, detection_index, component, ai_recommendation, assessor_decision, assessor_id, decided_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(claim_id, filename, detection_index) DO UPDATE SET
                        assessor_decision = excluded.assessor_decision,
                        assessor_id       = excluded.assessor_id,
                        decided_at        = excluded.decided_at
                ''', (claim_id, filename, detection_index, component, ai_recommendation, assessor_decision, assessor_id))
                conn.commit()
                return True
        except Exception as e:
            logger.error(f"Error saving photo damage decision for {claim_id}/{filename}#{detection_index}: {str(e)}")
            return False

    def get_photo_damage_decisions(self, claim_id: str) -> List[Dict]:
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT filename, detection_index, component, ai_recommendation, assessor_decision, assessor_id, decided_at
                    FROM photo_damage_decisions WHERE claim_id = ?
                ''', (claim_id,))
                return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"Error fetching photo damage decisions for {claim_id}: {str(e)}")
            return []

    def get_policy_drivers(self, policy_id: str) -> List[Dict]:
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT * FROM policy_drivers WHERE policy_id = ?', (policy_id,))
                return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"Error fetching policy drivers for {policy_id}: {str(e)}")
            return []

    def populate_member_bank_accounts(self, conn: sqlite3.Connection):
        """
        Synthetic bank account numbers for the graph/relationship-analysis
        capability (shared bank account across different policyholders).
        Deliberately gives MEM003 and MEM007 the SAME account number --
        a demo-able "hidden relationship" finding -- everyone else gets a
        distinct one.
        """
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) as cnt FROM members WHERE bank_account IS NOT NULL")
        if cursor.fetchone()['cnt'] > 0:
            return

        shared_account = "KE-EQTY-0041882201"
        accounts = {
            "MEM001": "KE-KCB-0019283746",
            "MEM002": "KE-COOP-0055621190",
            "MEM003": shared_account,
            "MEM004": "KE-NCBA-0072819345",
            "MEM005": "KE-ABSA-0038475620",
            "MEM006": "KE-DTB-0091827364",
            "MEM007": shared_account,   # shared with MEM003, deliberately
            "MEM008": "KE-STANCHART-0064738291",
        }
        for member_id, account in accounts.items():
            try:
                cursor.execute('UPDATE members SET bank_account = ? WHERE member_id = ?', (account, member_id))
            except Exception as e:
                logger.error(f"Error setting bank_account for {member_id}: {str(e)}")
        conn.commit()
        logger.info(f"Populated bank accounts for {len(accounts)} members (1 deliberately shared pair)")

    def find_members_sharing_bank_account(self, bank_account: str, exclude_member_id: str) -> List[Dict]:
        if not bank_account:
            return []
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT member_id, name FROM members
                    WHERE bank_account = ? AND member_id != ?
                ''', (bank_account, exclude_member_id))
                return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"Error finding members sharing bank account: {str(e)}")
            return []

    def populate_historical_narrative_corpus(self, conn: sqlite3.Connection):
        """
        Reference corpus for narrative-similarity search. Expands well
        beyond the ~12 narratives seeded onto live sample claims (migrate.py)
        so similarity comparisons have enough variety to be meaningful, and
        keeps them in a separate table so this reference data never shows
        up as a real claim on any dashboard.
        """
        cursor = conn.cursor()
        cursor.execute('SELECT COUNT(*) as cnt FROM historical_narrative_corpus')
        if cursor.fetchone()['cnt'] > 0:
            return

        corpus = [
            ("I was driving along Thika Road when a matatu suddenly cut in front of me. I braked hard but couldn't avoid the collision. The front bumper and bonnet were damaged.", 35, "genuine_collision"),
            ("Vehicle was parked outside my office in Upper Hill. Returned to find the side mirror broken and a deep scratch along the driver's door. No witnesses.", 45, "unwitnessed_parking_damage"),
            ("Rear-ended at a traffic light on Mombasa Road. The other driver initially stopped but drove off before we could exchange details. Rear bumper badly damaged.", 55, "hit_and_run"),
            ("My vehicle was broken into at night while parked at home. The stereo system and two tyres were stolen. Gate was not forced so the watchman may be involved.", 72, "insider_theft_suspicion"),
            ("Flooding caused water to enter the engine bay while driving through a flooded section of Jogoo Road during the rains. Engine seized shortly after.", 48, "weather_damage"),
            ("A lorry reversed into my vehicle at a loading bay in Industrial Area. The lorry driver gave me his number but is now unresponsive. Front left wing crumpled.", 40, "genuine_collision"),
            ("Tyre blowout on the highway caused loss of control. Vehicle swerved off the road and hit a road sign. Front axle and two rims damaged.", 30, "single_vehicle_mechanical"),
            ("Vehicle caught fire in the parking lot. Cause undetermined. Extensive damage to the interior and wiring. Fire brigade report attached.", 85, "suspicious_fire"),
            ("Collision at the Westlands roundabout. Both vehicles sustained damage. Third party has admitted liability. Police abstract obtained.", 38, "genuine_collision"),
            ("Vehicle was involved in a hit and run near Karen. CCTV footage from a nearby petrol station shows the incident. Rear end heavily damaged.", 50, "hit_and_run"),
            ("Driver hit a pothole on Ngong Road that damaged the front suspension and two alloy rims. Workshop assessment confirms impact damage.", 28, "single_vehicle_mechanical"),
            ("Vehicle stolen from a shopping mall car park. Recovered by police three days later with engine removed and airbags deployed.", 91, "suspicious_theft_recovery"),
            # Additional patterns not covered above -- broadens what
            # similarity search can actually match against.
            ("Two vehicles collided at low speed at a junction, both drivers claim significant whiplash injuries despite minimal visible vehicle damage. No independent witnesses.", 68, "exaggerated_injury_claim"),
            ("The vehicle was reversed into another car in an empty car park with no other vehicles nearby. Both drivers are acquainted with each other.", 75, "staged_low_speed_collision"),
            ("Vehicle sustained water damage claimed to be from flooding, but the incident occurred during a week with no recorded rainfall in the area.", 80, "weather_damage_inconsistent"),
            ("A passenger in the vehicle at the time of the accident is now claiming a separate injury payout, but was not listed on the original incident report.", 70, "phantom_passenger"),
            ("The vehicle was declared stolen but was found abandoned undamaged two streets away with no signs of forced entry or hot-wiring.", 78, "suspicious_theft_recovery"),
            ("Claim submitted for hail damage across the entire vehicle body, but the region has no record of a hailstorm in the claimed period.", 82, "weather_damage_inconsistent"),
            ("Vehicle was allegedly hit by an unknown third party who fled the scene; the described damage pattern is inconsistent with the claimed direction of impact.", 66, "hit_and_run"),
            ("Repair estimate for the claimed damage significantly exceeds typical costs for the described collision type, per garage cross-checks.", 60, "inflated_repair_estimate"),
            ("Multiple claims filed by the same policyholder for similar low-speed parking damage within a short time span, each involving a different but nearby location.", 74, "repeat_similar_claims"),
        ]
        for narrative, fraud_score, pattern_label in corpus:
            try:
                cursor.execute('''
                    INSERT INTO historical_narrative_corpus (narrative, fraud_score, pattern_label, claim_type)
                    VALUES (?, ?, ?, 'motor')
                ''', (narrative, fraud_score, pattern_label))
            except Exception as e:
                logger.error(f"Error inserting historical narrative corpus row: {str(e)}")
        conn.commit()
        logger.info(f"Populated {len(corpus)} historical narrative corpus entries")

    def get_historical_narrative_corpus(self) -> List[Dict]:
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT narrative, fraud_score, pattern_label, claim_type FROM historical_narrative_corpus')
                return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"Error fetching historical narrative corpus: {str(e)}")
            return []

    def get_analyst_claims(self, analyst_id: str) -> List[Dict]:
        """Claims filed by this analyst on behalf of members (phoned-in claims)."""
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT c.claim_id, c.member_id, c.location, c.estimated_cost,
                           c.fraud_risk_score, c.risk_level, c.created_at,
                           m.name as member_name, m.phone as member_phone
                    FROM claims c
                    LEFT JOIN members m ON c.member_id = m.member_id
                    WHERE c.filed_by_analyst_id = ?
                    ORDER BY c.created_at DESC
                ''', (analyst_id,))
                return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"Error fetching claims filed by analyst {analyst_id}: {str(e)}")
            return []

    def record_claim_decision(
        self, claim_id: str, decision: str, decided_by: str,
        reason: Optional[str] = None, payout_amount: Optional[float] = None,
    ) -> str:
        """
        Record a human's final PAY/DENY/ESCALATE call on a claim. Append-only
        -- a claim can be re-decided later (e.g. an escalation resolved
        afterward), callers should read the latest row via
        get_latest_claim_decision.
        """
        decision_id = f"DEC-{uuid.uuid4().hex[:12].upper()}"
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO claim_decisions
                (decision_id, claim_id, decision, reason, payout_amount, decided_by)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (decision_id, claim_id, decision, reason, payout_amount, decided_by))
            conn.commit()
        logger.info(f"Claim {claim_id} decision recorded: {decision} by {decided_by}")
        return decision_id

    def get_latest_claim_decision(self, claim_id: str) -> Optional[Dict]:
        """Most recent triage decision for a claim, or None if never decided."""
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT decision_id, claim_id, decision, reason, payout_amount, decided_by, decided_at
                    FROM claim_decisions
                    WHERE claim_id = ?
                    ORDER BY decided_at DESC
                    LIMIT 1
                ''', (claim_id,))
                row = cursor.fetchone()
                return dict(row) if row else None
        except Exception as e:
            logger.error(f"Error fetching decision for claim {claim_id}: {str(e)}")
            return None

    def get_claim_decision_history(self, claim_id: str) -> List[Dict]:
        """Full decision history for a claim, oldest first."""
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT decision_id, claim_id, decision, reason, payout_amount, decided_by, decided_at
                    FROM claim_decisions
                    WHERE claim_id = ?
                    ORDER BY decided_at ASC
                ''', (claim_id,))
                return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"Error fetching decision history for claim {claim_id}: {str(e)}")
            return []

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