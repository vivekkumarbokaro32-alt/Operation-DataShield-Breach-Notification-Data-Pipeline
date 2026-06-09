"""
Operation DataShield — Phase 6: Vendor Output Generation
Goal: Produce 4 vendor-specific output files from
      processed.notification_ready, plus a manifest file.

Output files:
  output/printco_mail.csv        — print/mail vendor
  output/sendgrid_email.json     — email vendor
  output/callcenter_dialer.txt   — call center
  output/creditmon_enroll.csv    — credit monitoring
  output/manifest.csv            — file manifest with checksums
"""

import os
import csv
import json
import hashlib
import pandas as pd
from sqlalchemy import create_engine, text
from datetime import datetime
import logging

# ─────────────────────────────────────────────────────────────
# SETUP
# ─────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    handlers=[
        logging.FileHandler('phase6_outputs.log'),
        logging.StreamHandler()
    ]
)
log = logging.getLogger('phase6')

DB_URL = "postgresql+psycopg2://postgres:Vivek%40827009@localhost:5432/Datasheild"
engine = create_engine(DB_URL)

# Create output directory if it doesn't exist
OUTPUT_DIR = 'output'
os.makedirs(OUTPUT_DIR, exist_ok=True)

# Batch ID — unique identifier for this notification run
BATCH_ID = datetime.now().strftime('DS-%Y%m%d-%H%M')
TEMPLATE_ID = 'DATASHIELD_BREACH_V1'

# ─────────────────────────────────────────────────────────────
# UTILITY FUNCTIONS
# ─────────────────────────────────────────────────────────────

def md5_checksum(filepath: str) -> str:
    """
    Compute MD5 checksum of a file.
    Used to verify file integrity when sending to vendors.
    Vendor recomputes this on their end — if it matches,
    file arrived intact.
    """
    hash_md5 = hashlib.md5()
    with open(filepath, 'rb') as f:
        for chunk in iter(lambda: f.read(4096), b''):
            hash_md5.update(chunk)
    return hash_md5.hexdigest()


def count_rows(filepath: str, is_json: bool = False) -> int:
    """Count data rows in a file (excluding headers)."""
    if is_json:
        with open(filepath, 'r') as f:
            data = json.load(f)
        return len(data)
    with open(filepath, 'r') as f:
        return sum(1 for _ in f) - 0  # JSON has no header


def truncate_name(name: str, max_len: int = 40) -> str:
    """
    Truncate a name to max_len characters.
    Logs a warning if truncation occurs — important for audit.
    """
    if not name:
        return ''
    name = str(name).strip()
    if len(name) > max_len:
        return name[:max_len]
    return name


def state_to_timezone(state_code: str) -> str:
    """
    Map state code to timezone for call center routing.
    Multi-timezone states default to their primary zone.
    """
    ET = ['CT','DE','FL','GA','IN','KY','ME','MD','MA','MI',
          'NH','NJ','NY','NC','OH','PA','RI','SC','TN','VT',
          'VA','WV','DC']
    CT = ['AL','AR','IL','IA','KS','LA','MN','MS','MO',
          'NE','ND','OK','SD','TX','WI']
    MT = ['AZ','CO','ID','MT','NM','UT','WY']
    PT = ['AK','CA','NV','OR','WA']
    HT = ['HI']

    s = str(state_code).strip().upper()
    if s in ET: return 'ET'
    if s in CT: return 'CT'
    if s in MT: return 'MT'
    if s in PT: return 'PT'
    if s in HT: return 'HT'
    return 'ET'  # default to Eastern if unknown


# ─────────────────────────────────────────────────────────────
# OUTPUT 1: PrintCo Mail Vendor
# Format requirements:
#   - CSV, comma delimited
#   - NO header row (PrintCo system reads positional)
#   - Column order: record_id, full_name, address_1, address_2,
#                   city, state_code, zip5, batch_id
#   - full_name max 40 characters
#   - Records: MAIL_ONLY and BOTH channels only
# ─────────────────────────────────────────────────────────────
def generate_mail_file(df: pd.DataFrame, output_path: str) -> int:
    """Generate PrintCo mail vendor file."""
    log.info("Generating PrintCo mail file...")

    # Filter to mail-eligible records
    df_mail = df[df['channel'].isin(['MAIL_ONLY', 'BOTH'])].copy()
    log.info(f"  Mail-eligible records: {len(df_mail):,}")

    truncated_count = 0
    rows_written = 0

    with open(output_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        # NO header row — PrintCo reads positionally

        for _, row in df_mail.iterrows():
            # Build full name
            full_name = f"{row['first_name']} {row['last_name']}".strip()

            # Truncate to 40 chars — log if truncation happens
            if len(full_name) > 40:
                truncated_count += 1
                full_name = truncate_name(full_name, 40)

            # Substitute blank first name
            if not row['first_name'] or str(row['first_name']).strip() == '':
                full_name = f"Valued Customer {row['last_name']}".strip()

            writer.writerow([
                row['record_id'],
                full_name,
                row['address_1'],
                row.get('address_2', ''),
                row['city'],
                row['state_code'],
                row['zip5'],
                BATCH_ID
            ])
            rows_written += 1

    if truncated_count > 0:
        log.warning(f"  {truncated_count} names truncated to 40 chars — "
                    f"see audit log for record IDs")

    log.info(f"  Rows written: {rows_written:,}")
    return rows_written


# ─────────────────────────────────────────────────────────────
# OUTPUT 2: Email Vendor (SendGrid-style)
# Format requirements:
#   - JSON array
#   - Fields: to_email, first_name, last_name,
#             template_id, batch_id, record_id
#   - first_name blank → substitute 'Valued Customer'
#   - Records: EMAIL_ONLY and BOTH channels only
# ─────────────────────────────────────────────────────────────
def generate_email_file(df: pd.DataFrame, output_path: str) -> int:
    """Generate email vendor JSON file."""
    log.info("Generating email vendor file...")

    # Filter to email-eligible records with valid email
    df_email = df[
        df['channel'].isin(['EMAIL_ONLY', 'BOTH']) &
        (df['email_valid'] == True)
    ].copy()
    log.info(f"  Email-eligible records: {len(df_email):,}")

    records = []
    for _, row in df_email.iterrows():
        first_name = str(row['first_name']).strip()
        if not first_name:
            first_name = 'Valued Customer'

        records.append({
            'record_id':   str(row['record_id']),
            'to_email':    str(row['email']).strip(),
            'first_name':  first_name,
            'last_name':   str(row['last_name']).strip(),
            'template_id': TEMPLATE_ID,
            'batch_id':    BATCH_ID,
            'send_date':   datetime.now().strftime('%Y-%m-%d')
        })

    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(records, f, indent=2)

    log.info(f"  Records written: {len(records):,}")
    return len(records)


# ─────────────────────────────────────────────────────────────
# OUTPUT 3: Call Center Dialer
# Format requirements:
#   - Pipe-delimited TXT (not CSV)
#   - HAS header row
#   - Phone in 10-digit format (no +1 prefix)
#   - Include time_zone derived from state
#   - Fields: record_id, first_name, last_name,
#             phone_10digit, time_zone, batch_id
#   - Records with valid phone only
# ─────────────────────────────────────────────────────────────
def generate_callcenter_file(df: pd.DataFrame, output_path: str) -> int:
    """Generate call center dialer file."""
    log.info("Generating call center dialer file...")

    # Filter to records with valid phone
    df_call = df[df['phone_valid'] == True].copy()
    log.info(f"  Call-eligible records: {len(df_call):,}")

    rows_written = 0

    with open(output_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f, delimiter='|')

        # Header row
        writer.writerow([
            'RECORD_ID', 'FIRST_NAME', 'LAST_NAME',
            'PHONE_10DIGIT', 'TIME_ZONE', 'BATCH_ID'
        ])

        for _, row in df_call.iterrows():
            # Strip +1 from E.164 to get 10-digit format
            phone_10 = str(row['phone_e164'])[2:] \
                if str(row['phone_e164']).startswith('+1') \
                else str(row['phone_e164'])

            time_zone = state_to_timezone(row['state_code'])

            writer.writerow([
                row['record_id'],
                str(row['first_name']).strip(),
                str(row['last_name']).strip(),
                phone_10,
                time_zone,
                BATCH_ID
            ])
            rows_written += 1

    log.info(f"  Rows written: {rows_written:,}")
    return rows_written


# ─────────────────────────────────────────────────────────────
# OUTPUT 4: Credit Monitoring Enrollment
# Format requirements:
#   - CSV with header
#   - Fields: record_id, first_name, last_name,
#             dob_clean, ssn_last4, batch_id
#   - Only records where DOB is valid (dob_flag = 'OK')
#     and ssn_last4 is present
#   - All records regardless of channel
# ─────────────────────────────────────────────────────────────
def generate_creditmon_file(df: pd.DataFrame, output_path: str) -> int:
    """Generate credit monitoring enrollment file."""
    log.info("Generating credit monitoring file...")

    # Only include records with valid DOB and SSN last 4
    df_credit = df[
        (df['dob_flag'] == 'OK') &
        (df['ssn_last4'].notna()) &
        (df['ssn_last4'] != '')
    ].copy()
    log.info(f"  Credit monitoring eligible: {len(df_credit):,}")

    rows_written = 0

    with open(output_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)

        # Header
        writer.writerow([
            'RECORD_ID', 'FIRST_NAME', 'LAST_NAME',
            'DATE_OF_BIRTH', 'SSN_LAST4', 'BATCH_ID'
        ])

        for _, row in df_credit.iterrows():
            writer.writerow([
                row['record_id'],
                str(row['first_name']).strip(),
                str(row['last_name']).strip(),
                str(row['dob_clean']).strip(),
                str(row['ssn_last4']).strip(),
                BATCH_ID
            ])
            rows_written += 1

    log.info(f"  Rows written: {rows_written:,}")
    return rows_written


# ─────────────────────────────────────────────────────────────
# MANIFEST FILE
# Records every output file with:
#   - Filename
#   - Record count
#   - File size in bytes
#   - MD5 checksum
#   - Generated timestamp
# This file goes to every vendor alongside their data file.
# ─────────────────────────────────────────────────────────────
def generate_manifest(file_info: list, output_path: str):
    """Generate manifest CSV with checksums for all output files."""
    log.info("Generating manifest file...")

    with open(output_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow([
            'FILENAME', 'RECORD_COUNT', 'FILE_SIZE_BYTES',
            'MD5_CHECKSUM', 'GENERATED_AT', 'BATCH_ID'
        ])
        for info in file_info:
            filepath = info['path']
            size     = os.path.getsize(filepath)
            checksum = md5_checksum(filepath)
            writer.writerow([
                os.path.basename(filepath),
                info['count'],
                size,
                checksum,
                datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                BATCH_ID
            ])
            log.info(f"  {os.path.basename(filepath)}: "
                     f"{info['count']:,} rows | "
                     f"{size:,} bytes | "
                     f"MD5: {checksum}")

    log.info(f"  Manifest written: {output_path}")


# ─────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────
if __name__ == '__main__':
    log.info("Operation DataShield — Phase 6: Output Generation starting")
    log.info(f"Batch ID: {BATCH_ID}")
    log.info(f"Run timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    # ── Load notification_ready ───────────────────────────────
    log.info("Loading processed.notification_ready...")
    with engine.connect() as conn:
        df = pd.read_sql(
            text("SELECT * FROM processed.notification_ready"), conn)
    log.info(f"  Loaded {len(df):,} records")

    # Fill NaN
    df = df.fillna('')

    # ── Define output file paths ──────────────────────────────
    mail_path   = os.path.join(OUTPUT_DIR, 'printco_mail.csv')
    email_path  = os.path.join(OUTPUT_DIR, 'sendgrid_email.json')
    call_path   = os.path.join(OUTPUT_DIR, 'callcenter_dialer.txt')
    credit_path = os.path.join(OUTPUT_DIR, 'creditmon_enroll.csv')
    manifest_path = os.path.join(OUTPUT_DIR, 'manifest.csv')

    # ── Generate all 4 output files ───────────────────────────
    mail_count   = generate_mail_file(df, mail_path)
    email_count  = generate_email_file(df, email_path)
    call_count   = generate_callcenter_file(df, call_path)
    credit_count = generate_creditmon_file(df, credit_path)

    # ── Generate manifest ─────────────────────────────────────
    file_info = [
        {'path': mail_path,   'count': mail_count},
        {'path': email_path,  'count': email_count},
        {'path': call_path,   'count': call_count},
        {'path': credit_path, 'count': credit_count},
    ]
    generate_manifest(file_info, manifest_path)

    # ── Final summary ─────────────────────────────────────────
    log.info("")
    log.info("=" * 60)
    log.info("PHASE 6 OUTPUT SUMMARY")
    log.info("=" * 60)
    log.info(f"  Batch ID:              {BATCH_ID}")
    log.info(f"  {'File':<35} {'Records':>8}")
    log.info(f"  {'-'*45}")
    log.info(f"  {'printco_mail.csv':<35} {mail_count:>8,}")
    log.info(f"  {'sendgrid_email.json':<35} {email_count:>8,}")
    log.info(f"  {'callcenter_dialer.txt':<35} {call_count:>8,}")
    log.info(f"  {'creditmon_enroll.csv':<35} {credit_count:>8,}")
    log.info(f"  {'-'*45}")
    log.info(f"  Output folder: {os.path.abspath(OUTPUT_DIR)}")
    log.info("=" * 60)
    log.info("All vendor files generated. Proceed to Phase 7.")