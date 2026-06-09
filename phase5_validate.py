"""
Operation DataShield — Phase 5: Validation & Exception Handling
Goal: Validate every record, classify notification channel,
      split into notification_ready and exceptions tables.
"""

import re
import pandas as pd
from sqlalchemy import create_engine, text
import logging
from datetime import datetime

# ─────────────────────────────────────────────────────────────
# SETUP
# ─────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    handlers=[
        logging.FileHandler('phase5_validate.log'),
        logging.StreamHandler()
    ]
)
log = logging.getLogger('phase5')

DB_URL = "postgresql+psycopg2://postgres:Vivek%40@localhost:5432/Datasheild"
engine = create_engine(DB_URL)

# ─────────────────────────────────────────────────────────────
# VALIDATION FUNCTIONS
# ─────────────────────────────────────────────────────────────

# ── 1. Email validation ───────────────────────────────────────
# Known fake/test emails that pass regex but are not real
FAKE_EMAILS = {
    'test@test.com', 'fake@fake.com', 'noreply@domain.com',
    'no@no.com', 'none@none.com', 'test@example.com',
    'user@test.com', 'admin@test.com'
}

# Basic email regex — must have: something @ something . something
EMAIL_REGEX = re.compile(r'^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$')

def validate_email(email: str) -> tuple:
    """
    Validate an email address.
    Returns (is_valid: bool, reason: str)
    """
    if not email or pd.isna(email) or str(email).strip() == '':
        return (False, 'MISSING')

    email = str(email).strip().lower()

    # Check against known fakes
    if email in FAKE_EMAILS:
        return (False, 'FAKE_EMAIL')

    # Check regex format
    if not EMAIL_REGEX.match(email):
        return (False, 'INVALID_FORMAT')

    # Check for obviously disposable/test domains
    domain = email.split('@')[1] if '@' in email else ''
    test_domains = {'test.com', 'fake.com', 'example.com',
                    'mailinator.com', 'guerrillamail.com'}
    if domain in test_domains:
        return (False, 'TEST_DOMAIN')

    return (True, 'OK')


# ── 2. Phone validation ───────────────────────────────────────
def validate_phone(phone: str) -> tuple:
    """
    Validate a phone number already in E.164 format.
    Returns (is_valid: bool, reason: str)
    """
    if not phone or pd.isna(phone) or str(phone).strip() == '':
        return (False, 'MISSING')

    phone = str(phone).strip()

    # Must be in E.164 format: +1 followed by 10 digits
    if not re.match(r'^\+1\d{10}$', phone):
        return (False, 'INVALID_FORMAT')

    # Extract digits for fake number check
    digits = phone[2:]  # strip +1

    FAKE_PATTERNS = {
        '5555555555', '0000000000', '1234567890',
        '1111111111', '2222222222', '9999999999'
    }
    if digits in FAKE_PATTERNS:
        return (False, 'FAKE_NUMBER')

    # Area code cannot start with 0 or 1
    if digits[0] in ('0', '1'):
        return (False, 'INVALID_AREA_CODE')

    return (True, 'OK')


# ── 3. Address validation ─────────────────────────────────────
def validate_address(address_1: str, city: str,
                     state_code: str, zip5: str) -> tuple:
    """
    Validate that a mailing address is complete enough to use.
    Returns (is_valid: bool, reason: str)
    """
    # All 4 fields must be present
    if not address_1 or str(address_1).strip() == '':
        return (False, 'MISSING_STREET')

    if not city or str(city).strip() == '':
        return (False, 'MISSING_CITY')

    if not state_code or str(state_code).strip() == '':
        return (False, 'MISSING_STATE')

    if not zip5 or str(zip5).strip() == '':
        return (False, 'MISSING_ZIP')

    # ZIP must be 5 digits
    if not re.match(r'^\d{5}$', str(zip5).strip()):
        return (False, 'INVALID_ZIP')

    # State must be 2 letters
    if not re.match(r'^[A-Z]{2}$', str(state_code).strip()):
        return (False, 'INVALID_STATE')

    return (True, 'OK')


# ── 4. Channel classification ─────────────────────────────────
def classify_channel(email_valid: bool, address_valid: bool) -> str:
    """
    Assign notification channel based on what contact
    information is valid for this record.
    """
    if email_valid and address_valid:
        return 'BOTH'
    elif email_valid and not address_valid:
        return 'EMAIL_ONLY'
    elif not email_valid and address_valid:
        return 'MAIL_ONLY'
    else:
        return 'NO_CONTACT'


# ─────────────────────────────────────────────────────────────
# MAIN VALIDATION RUNNER
# Applies all validations to every record and adds
# result columns to the DataFrame.
# ─────────────────────────────────────────────────────────────
def run_validation(df: pd.DataFrame) -> pd.DataFrame:
    """
    Run all validations on the deduped DataFrame.
    Adds columns: email_valid, email_flag, phone_valid,
    phone_flag, address_valid, address_flag, channel
    """
    log.info("Running email validation...")
    email_results = df['email'].apply(validate_email)
    df['email_valid'] = email_results.apply(lambda x: x[0])
    df['email_flag']  = email_results.apply(lambda x: x[1])

    log.info("Running phone validation...")
    phone_results = df['phone_e164'].apply(validate_phone)
    df['phone_valid'] = phone_results.apply(lambda x: x[0])
    df['phone_flag']  = phone_results.apply(lambda x: x[1])

    log.info("Running address validation...")
    addr_results = df.apply(
        lambda row: validate_address(
            row['address_1'], row['city'],
            row['state_code'], row['zip5']
        ), axis=1
    )
    df['address_valid'] = addr_results.apply(lambda x: x[0])
    df['address_flag']  = addr_results.apply(lambda x: x[1])

    log.info("Classifying notification channels...")
    df['channel'] = df.apply(
        lambda row: classify_channel(
            row['email_valid'], row['address_valid']
        ), axis=1
    )

    return df


# ─────────────────────────────────────────────────────────────
# EXCEPTION REPORT
# Build a breakdown of exceptions by type and by vendor.
# This tells the client which vendor contributed bad data.
# ─────────────────────────────────────────────────────────────
def build_exception_report(df_exceptions: pd.DataFrame) -> pd.DataFrame:
    """
    Summarize exceptions by reason code and vendor source.
    """
    if df_exceptions.empty:
        return pd.DataFrame()

    rows = []

    # Count by exception type
    for col, flag_col in [('email', 'email_flag'),
                           ('phone', 'phone_flag'),
                           ('address', 'address_flag')]:
        if flag_col in df_exceptions.columns:
            counts = df_exceptions[flag_col].value_counts()
            for reason, count in counts.items():
                if reason != 'OK':
                    rows.append({
                        'Exception Type': f'{col.upper()}: {reason}',
                        'Count': count,
                        'Pct of Exceptions': round(
                            count / len(df_exceptions) * 100, 1)
                    })

    return pd.DataFrame(rows).sort_values('Count', ascending=False)


# ─────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────
if __name__ == '__main__':
    log.info("Operation DataShield — Phase 5: Validation starting")
    log.info(f"Run timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    # ── Load deduped records ──────────────────────────────────
    log.info("Loading staging.deduped...")
    with engine.connect() as conn:
        df = pd.read_sql(text("SELECT * FROM staging.deduped"), conn)
    log.info(f"  Loaded {len(df):,} rows")

    # Fill NaN with empty string for validation functions
    str_cols = ['email','phone_e164','address_1',
                'city','state_code','zip5']
    for col in str_cols:
        if col in df.columns:
            df[col] = df[col].fillna('')

    # ── Run all validations ───────────────────────────────────
    df = run_validation(df)

    # ── Split into ready vs exceptions ───────────────────────
    # notification_ready: records with at least one valid
    #                     contact channel
    # exceptions:         NO_CONTACT records only
    #                     (everything else goes to ready,
    #                      even if some fields are invalid —
    #                      we flag them but still notify)
    df_ready      = df[df['channel'] != 'NO_CONTACT'].copy()
    df_exceptions = df[df['channel'] == 'NO_CONTACT'].copy()

    log.info(f"  Notification ready: {len(df_ready):,}")
    log.info(f"  Exceptions (NO_CONTACT): {len(df_exceptions):,}")

    # ── Channel breakdown ─────────────────────────────────────
    channel_counts = df_ready['channel'].value_counts()

    # ── Write processed.notification_ready ───────────────────
    log.info("Writing processed.notification_ready...")
    df_ready.to_sql(
        name='notification_ready',
        con=engine,
        schema='processed',
        if_exists='replace',
        index=False,
        method='multi',
        chunksize=2000
    )

    # ── Write processed.exceptions ────────────────────────────
    if not df_exceptions.empty:
        log.info("Writing processed.exceptions...")
        df_exceptions.to_sql(
            name='exceptions',
            con=engine,
            schema='processed',
            if_exists='replace',
            index=False,
            method='multi',
            chunksize=2000
        )
    else:
        log.info("  No NO_CONTACT exceptions found.")

    # ── Build exception report ────────────────────────────────
    exception_report = build_exception_report(df)

    # ── Verify DB counts ──────────────────────────────────────
    with engine.connect() as conn:
        ready_count = conn.execute(
            text("SELECT COUNT(*) FROM processed.notification_ready")
        ).fetchone()[0]

    # ── Print summary ─────────────────────────────────────────
    log.info("")
    log.info("=" * 60)
    log.info("PHASE 5 VALIDATION SUMMARY")
    log.info("=" * 60)
    log.info(f"  Total deduped records:     {len(df):,}")
    log.info(f"  Notification ready:        {len(df_ready):,}")
    log.info(f"  NO_CONTACT exceptions:     {len(df_exceptions):,}")
    log.info(f"  DB count verified:         {ready_count:,}")
    log.info(f"  Count match:               "
             f"{'✓ YES' if ready_count == len(df_ready) else '✗ NO'}")
    log.info("")
    log.info("CHANNEL BREAKDOWN:")
    log.info(f"  {'Channel':<15} {'Count':>8}  {'Pct':>6}")
    log.info(f"  {'-'*32}")
    total = len(df_ready)
    for channel, count in channel_counts.items():
        pct = round(count / total * 100, 1)
        log.info(f"  {channel:<15} {count:>8,}  {pct:>5.1f}%")
    log.info("")
    log.info("EMAIL VALIDATION BREAKDOWN:")
    email_flags = df['email_flag'].value_counts()
    for flag, count in email_flags.items():
        log.info(f"  {flag:<25} {count:>6,}")
    log.info("")
    log.info("ADDRESS VALIDATION BREAKDOWN:")
    addr_flags = df['address_flag'].value_counts()
    for flag, count in addr_flags.items():
        log.info(f"  {flag:<25} {count:>6,}")
    log.info("")
    if not exception_report.empty:
        log.info("EXCEPTION BREAKDOWN:")
        log.info(exception_report.to_string(index=False))
    log.info("=" * 60)
    log.info("processed.notification_ready is ready. Proceed to Phase 6.")