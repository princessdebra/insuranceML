"""
Seed script for Old Mutual Motor Underwriting AI
Run: python seed_db.py
"""

import sqlite3
import json
import random
from datetime import datetime, timedelta
import uuid

DB_PATH = "claims_database.db"

def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# ─────────────────────────────────────────────
# 1. ENSURE ALL TABLES EXIST
# ─────────────────────────────────────────────
def create_tables(conn):
    cursor = conn.cursor()

    cursor.executescript('''
        CREATE TABLE IF NOT EXISTS members (
            member_id TEXT PRIMARY KEY,
            name TEXT,
            email TEXT,
            phone TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

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
        );

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
        );

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
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            parties_analyzed INTEGER DEFAULT 1,
            cross_party_verification TEXT DEFAULT "{}",
            analysis_result TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS claim_photos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            claim_id TEXT NOT NULL,
            filename TEXT NOT NULL,
            file_size INTEGER NOT NULL,
            content_type TEXT NOT NULL,
            analysis_result TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (claim_id) REFERENCES claims(claim_id)
        );

        CREATE TABLE IF NOT EXISTS claim_photo_files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            claim_id TEXT NOT NULL,
            filename TEXT NOT NULL,
            party TEXT NOT NULL,
            file_data BLOB,
            file_hash TEXT,
            file_size INTEGER,
            content_type TEXT DEFAULT "image/jpeg",
            uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (claim_id) REFERENCES claims(claim_id),
            UNIQUE(claim_id, filename, party)
        );

        CREATE TABLE IF NOT EXISTS assessors (
            assessor_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            email TEXT NOT NULL UNIQUE,
            phone TEXT NOT NULL,
            license_number TEXT,
            specialization TEXT NOT NULL,
            active_status INTEGER DEFAULT 1,
            current_workload INTEGER DEFAULT 0,
            max_workload INTEGER DEFAULT 10,
            rating REAL DEFAULT 5.0,
            total_assessments INTEGER DEFAULT 0,
            location TEXT,
            company TEXT,
            avg_assessment_amount REAL DEFAULT 0,
            fraud_flag_count INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

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
        );

        CREATE TABLE IF NOT EXISTS claim_assignments (
            assignment_id TEXT PRIMARY KEY,
            claim_id TEXT NOT NULL,
            assessor_id TEXT NOT NULL,
            assigned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            status TEXT DEFAULT "pending",
            inspection_date TEXT,
            report_submitted_at TIMESTAMP,
            notes TEXT,
            FOREIGN KEY (assessor_id) REFERENCES assessors(assessor_id)
        );

        CREATE TABLE IF NOT EXISTS claim_status_history (
            history_id INTEGER PRIMARY KEY AUTOINCREMENT,
            claim_id TEXT NOT NULL,
            status TEXT NOT NULL,
            changed_by TEXT,
            changed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            notes TEXT
        );

        CREATE TABLE IF NOT EXISTS coverage_checks (
            check_id TEXT PRIMARY KEY,
            member_id TEXT,
            policy_id TEXT,
            claim_type TEXT,
            coverage_decision TEXT,
            coverage_percentage INTEGER,
            analysis_result TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS coverage_check_history (
            check_id TEXT PRIMARY KEY,
            member_id TEXT NOT NULL,
            policy_id TEXT,
            claim_type TEXT NOT NULL,
            coverage_decision TEXT NOT NULL,
            coverage_details TEXT,
            checked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            claim_id TEXT
        );

        CREATE TABLE IF NOT EXISTS system_metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            metric_name TEXT NOT NULL,
            metric_value TEXT NOT NULL,
            recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS processing_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            claim_id TEXT NOT NULL,
            processing_step TEXT NOT NULL,
            processing_time_ms INTEGER NOT NULL,
            status TEXT NOT NULL,
            details TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_photo_claim_party ON claim_photo_files(claim_id, party);
        CREATE INDEX IF NOT EXISTS idx_photo_hash ON claim_photo_files(file_hash);
    ''')

    conn.commit()
    print("✅ All tables created/verified")


# ─────────────────────────────────────────────
# 2. MEMBERS
# ─────────────────────────────────────────────
MEMBERS = [
    ("MEM001", "James Mwangi",    "j.mwangi@gmail.com",     "0722100001"),
    ("MEM002", "Esther Akinyi",   "e.akinyi@gmail.com",     "0733100002"),
    ("MEM003", "Brian Otieno",    "b.otieno@gmail.com",     "0744100003"),
    ("MEM004", "Caroline Njeri",  "c.njeri@gmail.com",      "0755100004"),
    ("MEM005", "Samuel Kipkemoi", "s.kipkemoi@gmail.com",   "0766100005"),
    ("MEM006", "Faith Wambua",    "f.wambua@gmail.com",     "0777100006"),
    ("MEM007", "Kevin Odhiambo",  "k.odhiambo@gmail.com",   "0788100007"),
    ("MEM008", "Lydia Chebet",    "l.chebet@gmail.com",     "0799100008"),
]

def seed_members(conn):
    cursor = conn.cursor()
    for m in MEMBERS:
        cursor.execute(
            "INSERT OR IGNORE INTO members (member_id, name, email, phone) VALUES (?,?,?,?)", m
        )
    conn.commit()
    print(f"✅ Seeded {len(MEMBERS)} members")


# ─────────────────────────────────────────────
# 3. POLICIES
# ─────────────────────────────────────────────
VEHICLES = [
    ("Toyota", "Premio",    2019, "KDG 123A"),
    ("Nissan", "X-Trail",   2020, "KDH 456B"),
    ("Toyota", "Hilux",     2018, "KDF 789C"),
    ("Subaru", "Forester",  2021, "KDJ 321D"),
    ("Honda",  "CR-V",      2017, "KDK 654E"),
    ("Toyota", "Land Cruiser", 2022, "KDL 987F"),
    ("Mazda",  "CX-5",      2020, "KDM 111G"),
    ("Mitsubishi", "Pajero",2019, "KDN 222H"),
]

def seed_policies(conn):
    cursor = conn.cursor()
    today = datetime.now().date()
    policies = []

    for i, (mid, _, _, _) in enumerate(MEMBERS):
        pid = f"POL{i+1:03d}"
        pnum = f"OMK/MOT/2024/{i+1:04d}"
        cover = random.choice(["Comprehensive", "Third Party Fire & Theft", "Third Party Only"])
        sum_insured = random.choice([800000, 1200000, 1500000, 2000000, 2500000])
        excess = random.choice([10000, 15000, 20000, 25000])
        start = today - timedelta(days=random.randint(30, 300))
        end   = start + timedelta(days=365)

        cursor.execute("""
            INSERT OR IGNORE INTO policies
            (policy_id, member_id, policy_type, policy_number, cover_type,
             sum_insured, excess, start_date, end_date, policy_wording)
            VALUES (?,?,?,?,?,?,?,?,?,?)
        """, (pid, mid, "motor", pnum, cover, sum_insured, excess,
              start.isoformat(), end.isoformat(),
              "Standard Old Mutual Motor Policy Wording v2024"))

        v = VEHICLES[i]
        cursor.execute("""
            INSERT OR IGNORE INTO motor_policy_details
            (policy_id, vehicle_make, vehicle_model, vehicle_year,
             registration_number, authorised_drivers, class_of_use, tracking_required)
            VALUES (?,?,?,?,?,?,?,?)
        """, (pid, v[0], v[1], v[2], v[3],
              "Policy Holder and Spouse", "Private", 1))

        policies.append(pid)

    conn.commit()
    print(f"✅ Seeded {len(policies)} policies + motor details")
    return policies


# ─────────────────────────────────────────────
# 4. ASSESSORS
# ─────────────────────────────────────────────
ASSESSORS = [
    ("ASS001", "John Kamau",     "j.kamau@assessors.co.ke",   "0722123456", "AKI-MOT-2024-001", "motor",    "Nairobi",  15, 4.8),
    ("ASS002", "Mary Wanjiku",   "m.wanjiku@assessors.co.ke", "0733234567", "AKI-MOT-2024-002", "motor",    "Nairobi",  12, 4.9),
    ("ASS003", "David Omondi",   "d.omondi@assessors.co.ke",  "0744345678", "AKI-MAR-2024-003", "marine",   "Mombasa",  10, 4.7),
    ("ASS004", "Grace Mutua",    "g.mutua@assessors.co.ke",   "0755456789", "AKI-DOM-2024-004", "domestic", "Nairobi",  20, 4.6),
    ("ASS005", "Peter Kipchoge", "p.kipchoge@assessors.co.ke","0766567890", "AKI-MOT-2024-005", "motor",    "Kisumu",   10, 4.5),
    ("ASS006", "Alice Njoroge",  "a.njoroge@assessors.co.ke", "0777678901", "AKI-MOT-2024-006", "motor",    "Nairobi",  15, 4.7),
    ("ASS007", "Paul Muthoni",   "p.muthoni@assessors.co.ke", "0788789012", "AKI-MOT-2024-007", "motor",    "Mombasa",  10, 4.4),
]

def seed_assessors(conn):
    cursor = conn.cursor()
    for a in ASSESSORS:
        cursor.execute("""
            INSERT OR IGNORE INTO assessors
            (assessor_id, name, email, phone, license_number,
             specialization, location, max_workload, rating)
            VALUES (?,?,?,?,?,?,?,?,?)
        """, a)
    conn.commit()
    print(f"✅ Seeded {len(ASSESSORS)} assessors")


# ─────────────────────────────────────────────
# 5. REPAIR SHOPS
# ─────────────────────────────────────────────
SHOPS = [
    ("SHP001", "Nairobi Auto Repairs",      "Westlands, Nairobi",   "0720111001", 45,  85000,  2, 12.5),
    ("SHP002", "Mombasa Panel Beaters",     "Mombasa CBD",          "0720111002", 30,  72000,  0,  5.0),
    ("SHP003", "Quick Fix Garage",          "Industrial Area, NBI",  "0720111003", 60,  95000,  5, 35.0),
    ("SHP004", "Premier Auto Centre",       "Upperhill, Nairobi",   "0720111004", 25,  120000, 0,  3.0),
    ("SHP005", "Safari Motors",             "Nakuru Town",          "0720111005", 20,  65000,  1,  8.0),
]

def seed_repair_shops(conn):
    cursor = conn.cursor()
    for s in SHOPS:
        cursor.execute("""
            INSERT OR IGNORE INTO repair_shops
            (shop_id, name, location, phone, total_claims,
             avg_claim_amount, fraud_flag_count, fraud_score)
            VALUES (?,?,?,?,?,?,?,?)
        """, s)
    conn.commit()
    print(f"✅ Seeded {len(SHOPS)} repair shops")


# ─────────────────────────────────────────────
# 6. CLAIMS
# ─────────────────────────────────────────────
NARRATIVES = [
    ("I was driving along Thika Road when a matatu suddenly cut in front of me. I braked hard but couldn't avoid the collision. The front bumper and bonnet were damaged.", 35, "low"),
    ("Vehicle was parked outside my office in Upper Hill. Returned to find the side mirror broken and a deep scratch along the driver's door. No witnesses.", 45, "medium"),
    ("Rear-ended at a traffic light on Mombasa Road. The other driver initially stopped but drove off before we could exchange details. Rear bumper badly damaged.", 55, "medium"),
    ("My vehicle was broken into at night while parked at home. The stereo system and two tyres were stolen. Gate was not forced so the watchman may be involved.", 72, "high"),
    ("Flooding caused water to enter the engine bay while driving through a flooded section of Jogoo Road during the rains. Engine seized shortly after.", 48, "medium"),
    ("A lorry reversed into my vehicle at a loading bay in Industrial Area. The lorry driver gave me his number but is now unresponsive. Front left wing crumpled.", 40, "low"),
    ("Tyre blowout on the highway caused loss of control. Vehicle swerved off the road and hit a road sign. Front axle and two rims damaged.", 30, "low"),
    ("Vehicle caught fire in the parking lot. Cause undetermined. Extensive damage to the interior and wiring. Fire brigade report attached.", 85, "high"),
    ("Collision at the Westlands roundabout. Both vehicles sustained damage. Third party has admitted liability. Police abstract obtained.", 38, "low"),
    ("Vehicle was involved in a hit and run near Karen. CCTV footage from a nearby petrol station shows the incident. Rear end heavily damaged.", 50, "medium"),
    ("Driver hit a pothole on Ngong Road that damaged the front suspension and two alloy rims. Workshop assessment confirms impact damage.", 28, "low"),
    ("Vehicle stolen from a shopping mall car park. Recovered by police three days later with engine removed and airbags deployed.", 91, "high"),
]

LOCATIONS = [
    "Westlands, Nairobi", "Upper Hill, Nairobi", "Mombasa Road, Nairobi",
    "Thika Road, Nairobi", "Ngong Road, Nairobi", "Industrial Area, Nairobi",
    "Karen, Nairobi", "Mombasa CBD", "Kisumu Town", "Nakuru CBD",
]

def seed_claims(conn):
    cursor = conn.cursor()
    year = datetime.now().year
    claim_ids = []

    for i in range(len(NARRATIVES)):
        mid_idx = i % len(MEMBERS)
        member_id = MEMBERS[mid_idx][0]
        policy_id = f"POL{mid_idx+1:03d}"
        claim_id = f"CLM-{year}-{i+1:06d}"

        narrative, fraud_score, risk_level = NARRATIVES[i]
        estimated_cost = random.choice([45000, 75000, 120000, 180000, 250000, 320000, 95000, 450000, 60000, 85000, 35000, 580000])
        location = random.choice(LOCATIONS)
        accident_time = (datetime.now() - timedelta(days=random.randint(1, 90))).strftime("%Y-%m-%d %H:%M")
        proc_time = random.randint(2800, 8500)

        analysis = {
            "claim_id": claim_id,
            "member_id": member_id,
            "policy_id": policy_id,
            "narrative": narrative,
            "estimated_cost": estimated_cost,
            "location": location,
            "accident_time": accident_time,
            "fraud_risk_score": fraud_score,
            "risk_level": risk_level,
            "processing_time_ms": proc_time,
            "fraud_indicators": [],
            "recommendation": "APPROVE" if fraud_score < 60 else "INVESTIGATE",
            "ai_summary": f"Analysis complete. Risk score: {fraud_score}/100.",
            "created_at": datetime.now().isoformat()
        }

        cursor.execute("""
            INSERT OR IGNORE INTO claims
            (claim_id, member_id, policy_id, narrative, estimated_cost, location,
             accident_time, fraud_risk_score, risk_level, processing_time_ms,
             parties_analyzed, cross_party_verification, analysis_result)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            claim_id, member_id, policy_id, narrative, estimated_cost, location,
            accident_time, fraud_score, risk_level, proc_time,
            1, "{}",
            json.dumps(analysis)
        ))

        claim_ids.append((claim_id, member_id, policy_id, location, risk_level))

    conn.commit()
    print(f"✅ Seeded {len(claim_ids)} claims")
    return claim_ids


# ─────────────────────────────────────────────
# 7. CLAIM ASSIGNMENTS + STATUS HISTORY
# ─────────────────────────────────────────────
def seed_assignments(conn, claim_ids):
    cursor = conn.cursor()
    motor_assessors = ["ASS001", "ASS002", "ASS006"]
    statuses = ["pending", "pending", "in_progress", "in_progress", "completed"]

    for claim_id, _, _, _, _ in claim_ids:
        assessor_id = random.choice(motor_assessors)
        assignment_id = f"ASG-{uuid.uuid4().hex[:8].upper()}"
        status = random.choice(statuses)

        cursor.execute("""
            INSERT OR IGNORE INTO claim_assignments
            (assignment_id, claim_id, assessor_id, status)
            VALUES (?,?,?,?)
        """, (assignment_id, claim_id, assessor_id, status))

        # Status history
        cursor.execute("""
            INSERT INTO claim_status_history (claim_id, status, changed_by, notes)
            VALUES (?,?,?,?)
        """, (claim_id, "submitted", "system", "Claim received"))

        cursor.execute("""
            INSERT INTO claim_status_history (claim_id, status, changed_by, notes)
            VALUES (?,?,?,?)
        """, (claim_id, "assessor_assigned", "system", f"Assigned to {assessor_id}"))

        if status in ("in_progress", "completed"):
            cursor.execute("""
                INSERT INTO claim_status_history (claim_id, status, changed_by, notes)
                VALUES (?,?,?,?)
            """, (claim_id, "inspection_scheduled", assessor_id, "Site visit booked"))

        if status == "completed":
            cursor.execute("""
                INSERT INTO claim_status_history (claim_id, status, changed_by, notes)
                VALUES (?,?,?,?)
            """, (claim_id, "assessment_complete", assessor_id, "Report submitted"))

    conn.commit()
    print(f"✅ Seeded assignments and status history for {len(claim_ids)} claims")


# ─────────────────────────────────────────────
# 8. SYSTEM METRICS
# ─────────────────────────────────────────────
def seed_metrics(conn):
    cursor = conn.cursor()
    metrics = [
        ("fraud_detection_rate",    "87.3"),
        ("false_positive_rate",     "12.7"),
        ("avg_processing_time_ms",  "4250"),
        ("system_uptime_pct",       "99.9"),
        ("total_claims_today",      "7"),
    ]
    for name, value in metrics:
        cursor.execute(
            "INSERT INTO system_metrics (metric_name, metric_value) VALUES (?,?)",
            (name, value)
        )
    conn.commit()
    print(f"✅ Seeded {len(metrics)} system metrics")


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
if __name__ == "__main__":
    print("\n🌱 Seeding Old Mutual Underwriting AI database...\n")
    conn = get_conn()

    create_tables(conn)
    seed_members(conn)
    seed_policies(conn)
    seed_assessors(conn)
    seed_repair_shops(conn)
    claim_ids = seed_claims(conn)
    seed_assignments(conn, claim_ids)
    seed_metrics(conn)

    conn.close()
    print("\n✅ Database seeding complete!")
    print(f"   Members: {len(MEMBERS)}")
    print(f"   Policies: {len(MEMBERS)}")
    print(f"   Assessors: {len(ASSESSORS)}")
    print(f"   Repair Shops: {len(SHOPS)}")
    print(f"   Claims: {len(NARRATIVES)} (realistic Kenyan motor scenarios)")