# Old Mutual Kenya — Motor Claims AI (PoC)

An AI-assisted motor insurance claims platform: FastAPI backend with a
Business Rules Engine + AI Intelligence Layer (photo/narrative/physics
fraud analysis), and a React/Vite frontend covering four portals — Member,
Assessor, Repair Shop, and Claims Analyst.

## Repository layout

```
motor_insurance-master/   FastAPI backend
motor_ui-main/motor_ui-main/   React + Vite + TypeScript frontend
```

## Prerequisites

- Python 3.11+
- Node.js 18+ and npm
- An [Ollama](https://ollama.com) instance (local or remote) serving the
  vision/text/coverage models used for claim analysis
- A Gmail account with 2-Step Verification enabled, for the "add photos to
  an existing claim" email notification feature (optional — the app runs
  fine without it, that one feature just won't send)

## 1. Backend setup

```bash
cd motor_insurance-master
python -m venv venv

# Windows
venv\Scripts\activate
# macOS/Linux
source venv/bin/activate

pip install -r requirements.txt
```

### Environment variables

Create `motor_insurance-master/.env` (or export these in your shell):

```bash
# Ollama connection
OLLAMA_BASE_URL=http://127.0.0.1:11434
OLLAMA_MODEL=claims-advisory-v1        # fine-tuned advisory model
OLLAMA_VISION_MODEL=gemma4:26b         # or your local vision model tag
OLLAMA_TEXT_MODEL=gemma4:26b
OLLAMA_COVERAGE_MODEL=gemma4:26b

# Gmail SMTP (optional — for "notify member to add photos" emails)
GMAIL_ADDRESS=your-address@gmail.com
GMAIL_APP_PASSWORD=your16charapppassword   # NOT your normal Gmail password —
                                            # generate one at
                                            # https://myaccount.google.com/apppasswords
                                            # (requires 2-Step Verification on)
FRONTEND_BASE_URL=http://localhost:5173    # used to build links in emails
```

The database (`claims_database.db`, SQLite) is created and seeded with
sample members/policies/analysts/assessors automatically on first run —
no manual migration step needed.

### Run the backend

```bash
python -m uvicorn main:app --host 0.0.0.0 --port 8010 --reload
```

API docs are then available at `http://localhost:8010/docs`.

## 2. Frontend setup

```bash
cd motor_ui-main/motor_ui-main
npm install
```

### Environment variables

Create `motor_ui-main/motor_ui-main/.env.local`:

```bash
VITE_API_BASE_URL=http://localhost:8010
```

Omit this file to fall back to `http://127.0.0.1:8010` by default.

### Run the frontend

```bash
npm run dev
```

Opens at `http://localhost:5173` by default. Portal entry points:

| Portal | Path |
|---|---|
| Member | `/member/login` |
| Assessor | `/assessor/login` |
| Claims Analyst | `/analyst/login` |
| Admin claim report | `/admin/claim/:claimId` |

Sample login IDs are seeded in the database — see
`motor_insurance-master/database.py`'s `populate_sample_*` functions
(members `MEM001`–`MEM008`, analysts `ANL001`–`ANL003`, assessors
`ASS001`–`ASS006`) if you need real IDs to log in with.

## 3. Production build (frontend)

```bash
cd motor_ui-main/motor_ui-main
npm run build
```

Outputs static files to `dist/` — serve with any static file host (nginx,
Apache, etc.) pointed at your deployed backend via `VITE_API_BASE_URL`.

## Notes

- Photo/document uploads, physics reconstruction, and AI advisory
  generation all depend on Ollama being reachable at `OLLAMA_BASE_URL` —
  if it's unavailable, claim analysis falls back to a deterministic
  rule-based path rather than failing outright.
- The Business Rules Engine (`business_rules.py`) runs independently of
  Ollama and always evaluates, regardless of AI availability.
