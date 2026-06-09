# Operation DataShield — Breach Notification Data Pipeline

## Project Overview
End-to-end data pipeline simulating a large-scale healthcare breach 
notification program — the type of work performed by legal notification 
firms like EPIQ Systems for HIPAA-mandated patient outreach.

**Scenario:** A healthcare company suffered a data breach exposing 
21,500 patient records across 3 internal systems. All affected 
individuals must be notified within 60 days (HIPAA requirement). 
This pipeline ingests the raw vendor files, cleans and deduplicates 
the data, validates contact information, and produces vendor-ready 
notification files.

## Tech Stack
- **Python** — Pandas, SQLAlchemy, openpyxl, re
- **PostgreSQL** — raw, staging, processed, output schemas
- **SQL** — window functions, CTEs, deduplication patterns

## Pipeline Phases

| Phase | Script | Description |
|---|---|---|
| 1 | phase1_ingestion.py | Chunked ingestion of 3 messy vendor CSV files |
| 2 | phase2_profiling.py | Data quality profiling — Excel report |
| 3 | phase3_normalize.py | Name, phone, DOB, state, ZIP normalization |
| 4 | phase4_dedup.py | 3-pass dedup: exact email → exact phone → fuzzy |
| 5 | phase5_validate.py | Validation + channel classification |
| 6 | phase6_outputs.py | 4 vendor output files + manifest |
| 7 | phase7_report.py | 5-sheet Excel client report |

## Key Results
- **21,500** raw records ingested from 3 vendor files
- **6,992** duplicate records removed (32.5% reduction)
- **14,508** unique individuals identified
- **14,486** records notification-ready
- **22** NO_CONTACT exceptions escalated to client
- **4** vendor output files generated with MD5 checksums

## Deduplication Strategy
Three sequential passes:
1. Exact match on email (ROW_NUMBER + PARTITION BY)
2. Exact match on phone (E.164 format)
3. Fuzzy match on name + address (Soundex blocking + 
   token sort ratio, threshold = 85)

## Setup Instructions

### Prerequisites
- Python 3.7+
- PostgreSQL
- pip packages: see requirements.txt

### Database Setup
```sql
CREATE DATABASE datashield;
\c datashield
CREATE SCHEMA raw;
CREATE SCHEMA staging;
CREATE SCHEMA processed;
CREATE SCHEMA output;
```

### Environment
```bash
export DATABASE_URL="postgresql+psycopg2://user:password@localhost:5432/datashield"
```

### Run
```bash
python phase1_ingestion.py
python phase2_profiling.py
python phase3_normalize.py
python phase4_dedup.py
python phase5_validate.py
python phase6_outputs.py
python phase7_report.py
```

## Data
Sample vendor files are included in the repository 
(vendor_a_raw.csv, vendor_b_raw.csv, vendor_c_raw.csv) — 
synthetically generated for practice purposes.
All data is fictional. No real PII is present.

## Domain Context
This project covers concepts directly relevant to:
- Legal notification services (HIPAA breach response)
- PII data handling and governance
- Multi-vendor data reconciliation
- SLA-bound operational data pipelines
