"""
Operation DataShield — Phase 1: Raw Data Ingestion
Goal: Read all 3 vendor CSV files and load them into PostgreSQL raw schema
      exactly as they are — no cleaning, no transformation.
"""

import os
import pandas as pd
from sqlalchemy import create_engine, text
import logging
from datetime import datetime

# ─────────────────────────────────────────────────────────────
# STEP 1: Set up logging
# Every important action gets recorded in a log file AND
# printed to your terminal so you can watch it run live.
# ─────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    handlers=[
        logging.FileHandler('phase1_ingestion.log'),  # saves to file
        logging.StreamHandler()                        # prints to terminal
    ]
)
log = logging.getLogger('phase1')

# ─────────────────────────────────────────────────────────────
# STEP 2: Database connection
# Replace YOUR_PASSWORD with your actual PostgreSQL password.
# ─────────────────────────────────────────────────────────────
DB_URL = "postgresql+psycopg2://postgres:Vivek%40827009@localhost:5432/Datasheild"

engine = create_engine(DB_URL)

# Quick connection test — fail early if credentials are wrong
try:
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    log.info("Database connection successful.")
except Exception as e:
    log.error(f"Could not connect to database: {e}")
    raise SystemExit("Fix your DB connection before proceeding.")

# ─────────────────────────────────────────────────────────────
# STEP 3: Vendor configuration
# Each vendor file has different quirks.
# We define ALL those quirks here in one place — not scattered
# through the code. This is called a config-driven approach.
# ─────────────────────────────────────────────────────────────
VENDOR_CONFIG = {
    'vendor_a': {
        'file':       'vendor_a_raw.csv',
        'delimiter':  '|',           # pipe-delimited, NOT comma
        'encoding':   'utf-8-sig',   # utf-8-sig strips the BOM character
        'skipfooter': 0,             # no footer rows
        'schema_table': 'raw.vendor_a',
        'expected_cols': [
            'FIRST_NAME','LAST_NAME','EMAIL_ADDRESS','PHONE_NUMBER',
            'STREET_ADDRESS','CITY','STATE','ZIP_CODE',
            'DATE_OF_BIRTH','SSN_LAST4','RECORD_ID'
        ]
    },
    'vendor_b': {
        'file':       'vendor_b_raw.csv',
        'delimiter':  ',',
        'encoding':   'utf-8',
        'skipfooter': 3,             # last 3 rows are garbage footer rows
        'schema_table': 'raw.vendor_b',
        'expected_cols': [
            'fname','lname','contact_email','contact_phone',
            'address','city_name','state_name','postal_code',
            'birthdate','last4_ssn','src_id'
        ]
    },
    'vendor_c': {
        'file':       'vendor_c_raw.csv',
        'delimiter':  ',',
        'encoding':   'utf-8',
        'skipfooter': 0,
        'schema_table': 'raw.vendor_c',
        'expected_cols': [
            'RecordID','LastName','FirstName','MiddleInit',
            'DOB','SSN4','PrimaryPhone','SecondaryPhone',
            'EmailAddr','AddressLine1','AddressLine2',
            'City','St','Zipcode','LoadDate'
        ]
    }
}

# ─────────────────────────────────────────────────────────────
# STEP 4: The ingestion function
# This function handles ONE vendor at a time.
# It reads the file in chunks so large files don't crash memory.
# ─────────────────────────────────────────────────────────────
def ingest_vendor(vendor_name: str, config: dict, chunksize: int = 2000) -> dict:
    """
    Reads a vendor CSV file in chunks and loads into PostgreSQL raw schema.
    Returns a summary dict with row counts and status.
    """
    log.info(f"{'='*50}")
    log.info(f"Starting ingestion: {vendor_name}")
    log.info(f"File: {config['file']}")

    filepath = config['file']

    # ── Guard: does the file actually exist? ──────────────────
    if not os.path.exists(filepath):
        log.error(f"File not found: {filepath}")
        return {'vendor': vendor_name, 'status': 'FAILED', 'rows_loaded': 0}

    # ── Read the file in chunks ───────────────────────────────
    # Why chunks? If the file had 2 million rows, loading it all
    # at once could use 2-3 GB of RAM and crash your script.
    # With chunksize=2000, we only hold 2000 rows in memory at
    # a time, process them, then move to the next 2000.
    total_rows_loaded = 0
    chunk_number = 0
    first_chunk = True

    try:
        # Special handling for files with footer rows
        # skipfooter is incompatible with chunksize in pandas
        # So we read the full file first, strip footer, then chunk manually
        if config['skipfooter'] > 0:
            log.info(f"  Footer rows detected ({config['skipfooter']}) — using full-read method")
            df_full = pd.read_csv(
                filepath,
                delimiter=config['delimiter'],
                encoding=config['encoding'],
                skipfooter=config['skipfooter'],
                engine='python',
                dtype=str
            )
            # Strip whitespace from column names and values
            df_full.columns = df_full.columns.str.strip()
            df_full = df_full.apply(
                lambda col: col.str.strip() if col.dtype == 'object' else col
            )
            # Add metadata columns
            df_full['_source_vendor'] = vendor_name
            df_full['_load_timestamp'] = datetime.now()
            df_full['_chunk_number'] = 1

            # Now write in manual chunks
            for i in range(0, len(df_full), chunksize):
                chunk = df_full.iloc[i:i+chunksize]
                chunk['_chunk_number'] = (i // chunksize) + 1
                chunk.to_sql(
                    name=config['schema_table'].split('.')[1],
                    con=engine,
                    schema=config['schema_table'].split('.')[0],
                    if_exists='replace' if i == 0 else 'append',
                    index=False,
                    method='multi',
                    chunksize=500
                )
                rows_in_chunk = len(chunk)
                total_rows_loaded += rows_in_chunk
                chunk_number += 1
                log.info(
                    f"  Chunk {chunk_number:03d}: "
                    f"{rows_in_chunk:,} rows written | "
                    f"Running total: {total_rows_loaded:,}"
                )

        else:
            # Normal chunked reading for files without footer rows
            chunk_iterator = pd.read_csv(
                filepath,
                delimiter=config['delimiter'],
                encoding=config['encoding'],
                engine='python',
                dtype=str,
                chunksize=chunksize
            )

            for chunk in chunk_iterator:
                chunk_number += 1
                chunk.columns = chunk.columns.str.strip()
                chunk = chunk.apply(
                    lambda col: col.str.strip() if col.dtype == 'object' else col
                )
                chunk['_source_vendor'] = vendor_name
                chunk['_load_timestamp'] = datetime.now()
                chunk['_chunk_number'] = chunk_number

                chunk.to_sql(
                    name=config['schema_table'].split('.')[1],
                    con=engine,
                    schema=config['schema_table'].split('.')[0],
                    if_exists='replace' if first_chunk else 'append',
                    index=False,
                    method='multi',
                    chunksize=500
                )

                rows_in_chunk = len(chunk)
                total_rows_loaded += rows_in_chunk
                first_chunk = False

                log.info(
                    f"  Chunk {chunk_number:03d}: "
                    f"{rows_in_chunk:,} rows written | "
                    f"Running total: {total_rows_loaded:,}"
                )

        log.info(f"Ingestion complete: {vendor_name} — {total_rows_loaded:,} total rows loaded")

        return {
            'vendor':      vendor_name,
            'file':        config['file'],
            'status':      'SUCCESS',
            'rows_loaded': total_rows_loaded,
            'chunks':      chunk_number,
            'table':       config['schema_table'],
            'loaded_at':   datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }

    except Exception as e:
        log.error(f"Ingestion FAILED for {vendor_name}: {e}")
        return {
            'vendor':      vendor_name,
            'status':      'FAILED',
            'rows_loaded': total_rows_loaded,
            'error':       str(e)
        }


# ─────────────────────────────────────────────────────────────
# STEP 5: Validation queries
# After loading, we verify the row counts in PostgreSQL match
# what we loaded. Never trust a load without verifying.
# ─────────────────────────────────────────────────────────────
def verify_load(schema_table: str) -> int:
    """Run a COUNT(*) on the loaded table and return the count."""
    with engine.connect() as conn:
        result = conn.execute(text(f"SELECT COUNT(*) FROM {schema_table}"))
        count = result.fetchone()[0]
    return count


# ─────────────────────────────────────────────────────────────
# STEP 6: Ingestion summary report
# After all vendors are loaded, print a clean summary table
# so you have one place to confirm everything worked.
# ─────────────────────────────────────────────────────────────
def print_summary(results: list):
    log.info("")
    log.info("=" * 60)
    log.info("PHASE 1 INGESTION SUMMARY")
    log.info("=" * 60)
    log.info(f"{'Vendor':<12} {'Status':<10} {'Rows Loaded':>12} {'DB Count':>10} {'Match?':>8}")
    log.info("-" * 60)

    total_loaded = 0
    all_success = True

    for r in results:
        if r['status'] == 'SUCCESS':
            db_count = verify_load(r['table'])
            match = "✓ YES" if db_count == r['rows_loaded'] else "✗ NO"
            if db_count != r['rows_loaded']:
                all_success = False
            log.info(
                f"{r['vendor']:<12} {r['status']:<10} "
                f"{r['rows_loaded']:>12,} {db_count:>10,} {match:>8}"
            )
            total_loaded += r['rows_loaded']
        else:
            all_success = False
            log.info(
                f"{r['vendor']:<12} {'FAILED':<10} "
                f"{'---':>12} {'---':>10} {'---':>8}"
            )

    log.info("-" * 60)
    log.info(f"{'TOTAL':<12} {'':<10} {total_loaded:>12,}")
    log.info("=" * 60)

    if all_success:
        log.info("All vendors loaded and verified successfully.")
        log.info("Raw schema is ready. Proceed to Phase 2.")
    else:
        log.warning("One or more vendors had issues. Review log before proceeding.")


# ─────────────────────────────────────────────────────────────
# MAIN — runs when you execute this script
# ─────────────────────────────────────────────────────────────
if __name__ == '__main__':
    log.info("Operation DataShield — Phase 1: Ingestion starting")
    log.info(f"Run timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    results = []
    for vendor_name, config in VENDOR_CONFIG.items():
        result = ingest_vendor(vendor_name, config)
        results.append(result)

    print_summary(results)
    log.info("Phase 1 complete. Check phase1_ingestion.log for full details.")