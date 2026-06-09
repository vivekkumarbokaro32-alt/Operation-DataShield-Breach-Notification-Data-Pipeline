"""
Operation DataShield — Phase 3: Normalization & Standardization
Goal: Transform all 3 raw vendor tables into one clean unified table
      called staging.normalized with a consistent schema.
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
        logging.FileHandler('phase3_normalize.log'),
        logging.StreamHandler()
    ]
)
log = logging.getLogger('phase3')

DB_URL = "postgresql+psycopg2://postgres:Vivek%40@localhost:5432/Datasheild"
engine = create_engine(DB_URL)

# ─────────────────────────────────────────────────────────────
# TRANSFORMATION FUNCTIONS
# Each function does ONE job. Each can be tested independently.
# This is the professional way to build ETL logic.
# ─────────────────────────────────────────────────────────────

# ── 1. Name normalization ─────────────────────────────────────
HONORIFICS = ['mr.', 'mrs.', 'ms.', 'dr.', 'prof.',
              'mr', 'mrs', 'ms', 'dr', 'prof']
SUFFIXES   = ['jr.', 'sr.', 'jr', 'sr', 'ii', 'iii', 'iv']

def normalize_name(raw: str) -> str:
    """
    Clean a name field:
    - Strip leading/trailing whitespace
    - Remove honorifics (Mr., Dr., etc.)
    - Remove suffixes (Jr., Sr., etc.)
    - Convert to Title Case
    Returns empty string if input is null/empty.
    """
    if not raw or pd.isna(raw):
        return ''

    # Strip and lowercase for processing
    cleaned = str(raw).strip().lower()

    # Remove honorifics from the start
    for hon in HONORIFICS:
        if cleaned.startswith(hon + ' '):
            cleaned = cleaned[len(hon):].strip()
            break

    # Remove suffixes from the end
    for suf in SUFFIXES:
        if cleaned.endswith(' ' + suf):
            cleaned = cleaned[:-len(suf)].strip()
            break

    # Title case the result
    return cleaned.title().strip()


# ── 2. State normalization ────────────────────────────────────
# Complete map: every variant we might see → 2-letter USPS code
STATE_MAP = {
    # Full names
    'alabama':'AL','alaska':'AK','arizona':'AZ','arkansas':'AR',
    'california':'CA','colorado':'CO','connecticut':'CT','delaware':'DE',
    'florida':'FL','georgia':'GA','hawaii':'HI','idaho':'ID',
    'illinois':'IL','indiana':'IN','iowa':'IA','kansas':'KS',
    'kentucky':'KY','louisiana':'LA','maine':'ME','maryland':'MD',
    'massachusetts':'MA','michigan':'MI','minnesota':'MN','mississippi':'MS',
    'missouri':'MO','montana':'MT','nebraska':'NE','nevada':'NV',
    'new hampshire':'NH','new jersey':'NJ','new mexico':'NM','new york':'NY',
    'north carolina':'NC','north dakota':'ND','ohio':'OH','oklahoma':'OK',
    'oregon':'OR','pennsylvania':'PA','rhode island':'RI','south carolina':'SC',
    'south dakota':'SD','tennessee':'TN','texas':'TX','utah':'UT',
    'vermont':'VT','virginia':'VA','washington':'WA','west virginia':'WV',
    'wisconsin':'WI','wyoming':'WY',
    # Abbreviations with periods
    'ala.':'AL','ariz.':'AZ','ark.':'AR','cal.':'CA','calif.':'CA',
    'colo.':'CO','conn.':'CT','del.':'DE','fla.':'FL','geo.':'GA',
    'ill.':'IL','ind.':'IN','kan.':'KS','ky.':'KY','la.':'LA',
    'mich.':'MI','minn.':'MN','miss.':'MS','mo.':'MO','mont.':'MT',
    'neb.':'NE','nev.':'NV','nor.':'NC','nort.':'ND','okl.':'OK',
    'ore.':'OR','penn.':'PA','tex.':'TX','tenn.':'TN','vir.':'VA',
    'wash.':'WA','wis.':'WI','wyo.':'WY','mas.':'MA','mar.':'MD',
}

def normalize_state(raw: str) -> str:
    """
    Convert any state representation to 2-letter USPS code.
    Handles: full names, lowercase, abbreviations with periods.
    Returns original value in uppercase if no match found
    (so we can flag it later rather than silently lose it).
    """
    if not raw or pd.isna(raw):
        return ''

    cleaned = str(raw).strip().lower()

    # Already a 2-letter code (uppercase or lowercase)
    if len(cleaned) == 2 and cleaned.isalpha():
        return cleaned.upper()

    # Look up in map
    if cleaned in STATE_MAP:
        return STATE_MAP[cleaned]

    # Try stripping trailing period
    if cleaned.rstrip('.') in STATE_MAP:
        return STATE_MAP[cleaned.rstrip('.')]

    # No match — return uppercased original so it's visible
    log.warning(f"  Unrecognized state value: '{raw}'")
    return str(raw).strip().upper()


# ── 3. ZIP code normalization ─────────────────────────────────
def normalize_zip(raw: str) -> str:
    """
    Standardize ZIP code to 5-digit zero-padded string.
    - Strips ZIP+4 extension (e.g. 60601-1234 → 60601)
    - Left-pads short ZIPs with zeros (e.g. 6060 → 06060)
    - Returns empty string if not a valid 5-digit ZIP.
    """
    if not raw or pd.isna(raw):
        return ''

    cleaned = str(raw).strip()

    # Strip ZIP+4 if present
    if '-' in cleaned:
        cleaned = cleaned.split('-')[0]

    # Remove any non-digit characters
    digits_only = re.sub(r'\D', '', cleaned)

    # Left-pad to 5 digits
    if len(digits_only) <= 5:
        return digits_only.zfill(5)

    # If more than 5 digits and no hyphen was present, take first 5
    return digits_only[:5]


# ── 4. Phone normalization ────────────────────────────────────
def normalize_phone(raw: str) -> str:
    """
    Convert any phone format to E.164: +1XXXXXXXXXX
    Accepts: (555) 123-4567 / 5551234567 / 555-123-4567
             +15551234567 / 555.123.4567
    Returns empty string if phone is invalid or fake.
    """
    FAKE_PHONES = {'5555555555', '0000000000', '1234567890'}

    if not raw or pd.isna(raw):
        return ''

    # Strip everything except digits
    digits = re.sub(r'\D', '', str(raw))

    # Handle +1 country code already present
    if len(digits) == 11 and digits.startswith('1'):
        digits = digits[1:]

    # Must be exactly 10 digits for a valid US number
    if len(digits) != 10:
        return ''

    # Check for fake/test numbers
    if digits in FAKE_PHONES:
        return ''

    # Check for obviously fake patterns (555-555-XXXX)
    if digits.startswith('555') and digits[3:6] == '555':
        return ''

    return f'+1{digits}'


# ── 5. DOB normalization ──────────────────────────────────────
# All 4 formats we found in Phase 2
DOB_FORMATS = [
    '%m/%d/%Y',    # MM/DD/YYYY  e.g. 04/22/1975
    '%Y-%m-%d',    # YYYY-MM-DD  e.g. 1975-04-22
    '%d-%b-%Y',    # DD-Mon-YYYY e.g. 22-Apr-1975
    '%m-%d-%Y',    # MM-DD-YYYY  e.g. 04-22-1975
]

def normalize_dob(raw: str) -> tuple:
    """
    Parse DOB from any of 4 formats into YYYY-MM-DD string.
    Returns a tuple: (clean_date_string, flag)
    flag = 'OK'          — valid date
    flag = 'INVALID_FUTURE' — date is in the future
    flag = 'INVALID_OLD'    — date before 1900 (impossible)
    flag = 'UNPARSEABLE'    — couldn't parse at all
    """
    if not raw or pd.isna(raw):
        return ('', 'MISSING')

    raw_str = str(raw).strip()

    for fmt in DOB_FORMATS:
        try:
            parsed = datetime.strptime(raw_str, fmt)

            # Validate range
            if parsed.year < 1900:
                return (parsed.strftime('%Y-%m-%d'), 'INVALID_OLD')
            if parsed > datetime.now():
                return (parsed.strftime('%Y-%m-%d'), 'INVALID_FUTURE')

            return (parsed.strftime('%Y-%m-%d'), 'OK')

        except ValueError:
            continue

    # None of the formats worked
    return (raw_str, 'UNPARSEABLE')


# ── 6. Email normalization ────────────────────────────────────
def normalize_email(raw: str) -> str:
    """
    Lowercase and strip an email address.
    Returns empty string if null/blank.
    Note: Full validation happens in Phase 5.
    """
    if not raw or pd.isna(raw):
        return ''
    return str(raw).strip().lower()


# ─────────────────────────────────────────────────────────────
# VENDOR-SPECIFIC MAPPERS
# Each vendor has different column names and structure.
# These functions map each vendor's raw row → canonical schema.
# ─────────────────────────────────────────────────────────────

def map_vendor_a(df: pd.DataFrame) -> pd.DataFrame:
    """Map Vendor A columns to canonical schema."""
    log.info("  Mapping Vendor A schema...")
    out = pd.DataFrame()

    out['record_id']     = df['RECORD_ID']
    out['first_name']    = df['FIRST_NAME'].apply(normalize_name)
    out['last_name']     = df['LAST_NAME'].apply(normalize_name)
    out['address_1']     = df['STREET_ADDRESS'].str.strip().str.title()
    out['address_2']     = ''  # Vendor A has no address line 2
    out['city']          = df['CITY'].str.strip().str.title()
    out['state_code']    = df['STATE'].apply(normalize_state)
    out['zip5']          = df['ZIP_CODE'].apply(normalize_zip)
    out['email']         = df['EMAIL_ADDRESS'].apply(normalize_email)
    out['phone_e164']    = df['PHONE_NUMBER'].apply(normalize_phone)
    out['ssn_last4']     = df['SSN_LAST4'].str.strip()
    out['record_source'] = 'vendor_a'

    # DOB — returns tuple, need to unpack
    dob_results          = df['DATE_OF_BIRTH'].apply(normalize_dob)
    out['dob_clean']     = dob_results.apply(lambda x: x[0])
    out['dob_flag']      = dob_results.apply(lambda x: x[1])

    return out


def map_vendor_b(df: pd.DataFrame) -> pd.DataFrame:
    """Map Vendor B columns to canonical schema."""
    log.info("  Mapping Vendor B schema...")
    out = pd.DataFrame()

    out['record_id']     = df['src_id']
    out['first_name']    = df['fname'].apply(normalize_name)
    out['last_name']     = df['lname'].apply(normalize_name)
    out['address_1']     = df['address'].str.strip().str.title()
    out['address_2']     = ''  # Vendor B has no address line 2
    out['city']          = df['city_name'].str.strip().str.title()
    out['state_code']    = df['state_name'].apply(normalize_state)
    out['zip5']          = df['postal_code'].apply(normalize_zip)
    out['email']         = df['contact_email'].apply(normalize_email)
    out['phone_e164']    = df['contact_phone'].apply(normalize_phone)
    out['ssn_last4']     = df['last4_ssn'].str.strip()
    out['record_source'] = 'vendor_b'

    dob_results          = df['birthdate'].apply(normalize_dob)
    out['dob_clean']     = dob_results.apply(lambda x: x[0])
    out['dob_flag']      = dob_results.apply(lambda x: x[1])

    return out


def map_vendor_c(df: pd.DataFrame) -> pd.DataFrame:
    """Map Vendor C columns to canonical schema."""
    log.info("  Mapping Vendor C schema...")
    out = pd.DataFrame()

    out['record_id']     = df['RecordID']
    # Vendor C has LastName first, then FirstName — note the swap
    out['first_name']    = df['FirstName'].apply(normalize_name)
    out['last_name']     = df['LastName'].apply(normalize_name)
    out['address_1']     = df['AddressLine1'].str.strip().str.title()
    out['address_2']     = df['AddressLine2'].fillna('').str.strip().str.title()
    out['city']          = df['City'].str.strip().str.title()
    out['state_code']    = df['St'].apply(normalize_state)
    out['zip5']          = df['Zipcode'].apply(normalize_zip)
    out['email']         = df['EmailAddr'].apply(normalize_email)
    # Vendor C has PrimaryPhone — use that
    out['phone_e164']    = df['PrimaryPhone'].apply(normalize_phone)
    out['ssn_last4']     = df['SSN4'].str.strip()
    out['record_source'] = 'vendor_c'

    dob_results          = df['DOB'].apply(normalize_dob)
    out['dob_clean']     = dob_results.apply(lambda x: x[0])
    out['dob_flag']      = dob_results.apply(lambda x: x[1])

    return out


# ─────────────────────────────────────────────────────────────
# TRANSFORMATION AUDIT LOG
# After transforming, we log how many records each rule
# affected. This is your evidence trail for the client.
# ─────────────────────────────────────────────────────────────
def generate_audit_log(raw_df: pd.DataFrame,
                        clean_df: pd.DataFrame,
                        vendor: str) -> list:
    """Compare raw vs clean to count how many records each rule touched."""
    audit = []
    total = len(clean_df)

    audit.append({
        'Vendor':    vendor,
        'Rule':      'Total records processed',
        'Affected':  total,
        'Pct %':     100.0
    })
    audit.append({
        'Vendor':    vendor,
        'Rule':      'Records with invalid/fake phone removed',
        'Affected':  (clean_df['phone_e164'] == '').sum(),
        'Pct %':     round((clean_df['phone_e164'] == '').sum() / total * 100, 1)
    })
    audit.append({
        'Vendor':    vendor,
        'Rule':      'DOB flagged as INVALID_OLD (before 1900)',
        'Affected':  (clean_df['dob_flag'] == 'INVALID_OLD').sum(),
        'Pct %':     round((clean_df['dob_flag'] == 'INVALID_OLD').sum() / total * 100, 1)
    })
    audit.append({
        'Vendor':    vendor,
        'Rule':      'DOB flagged as INVALID_FUTURE',
        'Affected':  (clean_df['dob_flag'] == 'INVALID_FUTURE').sum(),
        'Pct %':     round((clean_df['dob_flag'] == 'INVALID_FUTURE').sum() / total * 100, 1)
    })
    audit.append({
        'Vendor':    vendor,
        'Rule':      'DOB unparseable',
        'Affected':  (clean_df['dob_flag'] == 'UNPARSEABLE').sum(),
        'Pct %':     round((clean_df['dob_flag'] == 'UNPARSEABLE').sum() / total * 100, 1)
    })
    audit.append({
        'Vendor':    vendor,
        'Rule':      'Records missing email',
        'Affected':  (clean_df['email'] == '').sum(),
        'Pct %':     round((clean_df['email'] == '').sum() / total * 100, 1)
    })

    return audit


# ─────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────
if __name__ == '__main__':
    log.info("Operation DataShield — Phase 3: Normalization starting")
    log.info(f"Run timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    # ── Load raw tables ───────────────────────────────────────
    log.info("Loading raw tables from database...")
    with engine.connect() as conn:
        df_a = pd.read_sql(text("SELECT * FROM raw.vendor_a"), conn)
        df_b = pd.read_sql(text("SELECT * FROM raw.vendor_b"), conn)
        df_c = pd.read_sql(text("SELECT * FROM raw.vendor_c"), conn)

    # Drop metadata columns added during ingestion
    meta_cols = ['_source_vendor', '_load_timestamp', '_chunk_number']
    df_a = df_a.drop(columns=[c for c in meta_cols if c in df_a.columns])
    df_b = df_b.drop(columns=[c for c in meta_cols if c in df_b.columns])
    df_c = df_c.drop(columns=[c for c in meta_cols if c in df_c.columns])

    log.info(f"  Vendor A: {len(df_a):,} rows")
    log.info(f"  Vendor B: {len(df_b):,} rows")
    log.info(f"  Vendor C: {len(df_c):,} rows")

    # ── Apply transformations ─────────────────────────────────
    log.info("Applying normalization transforms...")
    clean_a = map_vendor_a(df_a)
    clean_b = map_vendor_b(df_b)
    clean_c = map_vendor_c(df_c)

    # ── Generate audit log ────────────────────────────────────
    audit_rows = []
    audit_rows.extend(generate_audit_log(df_a, clean_a, 'vendor_a'))
    audit_rows.extend(generate_audit_log(df_b, clean_b, 'vendor_b'))
    audit_rows.extend(generate_audit_log(df_c, clean_c, 'vendor_c'))
    audit_df = pd.DataFrame(audit_rows)

    # ── Combine all 3 vendors into one DataFrame ──────────────
    log.info("Combining all vendors into unified dataset...")
    df_normalized = pd.concat([clean_a, clean_b, clean_c], ignore_index=True)
    df_normalized['load_ts'] = datetime.now()

    log.info(f"  Combined total: {len(df_normalized):,} rows")
    log.info(f"  Columns: {list(df_normalized.columns)}")

    # ── Write to staging.normalized ───────────────────────────
    log.info("Writing to staging.normalized...")
    df_normalized.to_sql(
        name='normalized',
        con=engine,
        schema='staging',
        if_exists='replace',
        index=False,
        method='multi',
        chunksize=2000
    )
    log.info("  Done.")

    # ── Verify row count in DB ────────────────────────────────
    with engine.connect() as conn:
        db_count = conn.execute(
            text("SELECT COUNT(*) FROM staging.normalized")
        ).fetchone()[0]

    # ── Print summary ─────────────────────────────────────────
    log.info("")
    log.info("=" * 60)
    log.info("PHASE 3 NORMALIZATION SUMMARY")
    log.info("=" * 60)
    log.info(f"  Vendor A records:     {len(clean_a):,}")
    log.info(f"  Vendor B records:     {len(clean_b):,}")
    log.info(f"  Vendor C records:     {len(clean_c):,}")
    log.info(f"  Total normalized:     {len(df_normalized):,}")
    log.info(f"  DB count verified:    {db_count:,}")
    log.info(f"  Count match:          {'✓ YES' if db_count == len(df_normalized) else '✗ NO'}")
    log.info("")
    log.info("TRANSFORMATION AUDIT:")
    log.info(audit_df.to_string(index=False))
    log.info("=" * 60)
    log.info("staging.normalized is ready. Proceed to Phase 4.")