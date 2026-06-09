"""
Operation DataShield — Phase 2: Data Profiling & Quality Assessment
Goal: Understand every quality issue in the raw data BEFORE touching it.
Output: data_quality_report.xlsx with one sheet per vendor + a summary sheet.
"""

import pandas as pd
from sqlalchemy import create_engine, text
import logging
from datetime import datetime
from openpyxl import load_workbook
from openpyxl.styles import PatternFill, Font, Alignment, Border, Side
from openpyxl.utils import get_column_letter
import re

# ─────────────────────────────────────────────────────────────
# SETUP — same as Phase 1
# ─────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    handlers=[
        logging.FileHandler('phase2_profiling.log'),
        logging.StreamHandler()
    ]
)
log = logging.getLogger('phase2')

# !! Replace with your actual password
DB_URL = "postgresql+psycopg2://postgres:Vivek%40827009@localhost:5432/Datasheild"
engine = create_engine(DB_URL)

# ─────────────────────────────────────────────────────────────
# STEP 1: Load raw tables from PostgreSQL into DataFrames
# We load all 3 vendors into memory for profiling.
# These are small enough (21,500 rows total) to load fully.
# ─────────────────────────────────────────────────────────────
def load_raw_tables() -> dict:
    """Load all 3 vendor tables from raw schema into DataFrames."""
    tables = {
        'vendor_a': 'raw.vendor_a',
        'vendor_b': 'raw.vendor_b',
        'vendor_c': 'raw.vendor_c'
    }
    dataframes = {}
    for name, table in tables.items():
        log.info(f"Loading {table} from database...")
        with engine.connect() as conn:
            df = pd.read_sql(text(f"SELECT * FROM {table}"), conn)
        # Drop the metadata columns we added during ingestion
        meta_cols = ['_source_vendor', '_load_timestamp', '_chunk_number']
        df = df.drop(columns=[c for c in meta_cols if c in df.columns])
        dataframes[name] = df
        log.info(f"  Loaded {len(df):,} rows, {len(df.columns)} columns")
    return dataframes

# ─────────────────────────────────────────────────────────────
# STEP 2: Basic profile — nulls, distinct counts, lengths
# For every column in a DataFrame, we calculate:
# - Total rows
# - Null count and null percentage
# - Distinct value count
# - Min and max character length
# - Sample values (first 3 unique non-null values)
# ─────────────────────────────────────────────────────────────
def profile_dataframe(df: pd.DataFrame, vendor_name: str) -> pd.DataFrame:
    """Generate a column-level quality profile for one vendor DataFrame."""
    log.info(f"Profiling {vendor_name}...")
    rows = []
    total_rows = len(df)

    for col in df.columns:
        series = df[col]
        null_count = series.isna().sum() + (series == '').sum()
        null_pct = round((null_count / total_rows) * 100, 1)
        non_null = series.dropna()
        non_null = non_null[non_null != '']
        distinct_count = non_null.nunique()

        # Character length stats
        lengths = non_null.astype(str).str.len()
        min_len = int(lengths.min()) if len(lengths) > 0 else 0
        max_len = int(lengths.max()) if len(lengths) > 0 else 0

        # Sample values — first 3 unique non-null values
        samples = non_null.unique()[:3].tolist()
        sample_str = ' | '.join(str(s) for s in samples)

        rows.append({
            'Vendor':         vendor_name,
            'Column':         col,
            'Total Rows':     total_rows,
            'Null/Blank Count': null_count,
            'Null %':         null_pct,
            'Distinct Values': distinct_count,
            'Min Length':     min_len,
            'Max Length':     max_len,
            'Sample Values':  sample_str,
            'Flag':           '⚠ HIGH NULLS' if null_pct > 30 else (
                              '⚡ CHECK' if null_pct > 10 else '')
        })

    profile_df = pd.DataFrame(rows)
    log.info(f"  {vendor_name}: {len(rows)} columns profiled")
    return profile_df

# ─────────────────────────────────────────────────────────────
# STEP 3: Date format detection
# The DOB column has 4 different formats across vendors.
# This function detects which formats appear and how many
# records use each format.
# ─────────────────────────────────────────────────────────────
DATE_PATTERNS = {
    'MM/DD/YYYY':   r'^\d{2}/\d{2}/\d{4}$',
    'YYYY-MM-DD':   r'^\d{4}-\d{2}-\d{2}$',
    'DD-Mon-YYYY':  r'^\d{2}-[A-Za-z]{3}-\d{4}$',
    'MM-DD-YYYY':   r'^\d{2}-\d{2}-\d{4}$',
    'Future date':  None,   # handled separately
    'Other/Unknown': None   # catch-all
}

def detect_date_formats(df: pd.DataFrame, col: str, vendor_name: str) -> pd.DataFrame:
    """Detect and count different date formats in a column."""
    if col not in df.columns:
        return pd.DataFrame()

    log.info(f"  Detecting date formats in {vendor_name}.{col}...")
    series = df[col].dropna()
    series = series[series != '']

    results = []
    unmatched = []

    for val in series:
        val_str = str(val).strip()
        matched = False

        for fmt_name, pattern in DATE_PATTERNS.items():
            if pattern and re.match(pattern, val_str):
                results.append(fmt_name)
                matched = True
                break

        if not matched:
            results.append('Other/Unknown')
            unmatched.append(val_str)

    # Count future dates among matched
    future_count = 0
    for val in series:
        try:
            parsed = pd.to_datetime(str(val), infer_datetime_format=True, errors='coerce')
            if parsed and parsed > pd.Timestamp.now():
                future_count += 1
        except:
            pass

    from collections import Counter
    counts = Counter(results)
    rows = []
    for fmt, count in sorted(counts.items(), key=lambda x: -x[1]):
        rows.append({
            'Vendor':       vendor_name,
            'Column':       col,
            'Format':       fmt,
            'Count':        count,
            'Percentage %': round((count / len(series)) * 100, 1),
            'Sample':       ''
        })

    if future_count > 0:
        rows.append({
            'Vendor':       vendor_name,
            'Column':       col,
            'Format':       'Future date (invalid)',
            'Count':        future_count,
            'Percentage %': round((future_count / len(series)) * 100, 1),
            'Sample':       'e.g. 2030-01-01, 2035-01-01'
        })

    if unmatched:
        rows.append({
            'Vendor':       vendor_name,
            'Column':       col,
            'Format':       'Other/Unknown',
            'Count':        counts.get('Other/Unknown', 0),
            'Percentage %': round((counts.get('Other/Unknown', 0) / len(series)) * 100, 1),
            'Sample':       ' | '.join(unmatched[:3])
        })

    return pd.DataFrame(rows)

# ─────────────────────────────────────────────────────────────
# STEP 4: Phone format detection
# Phone numbers appear in at least 5 different formats.
# We detect each and count occurrences.
# ─────────────────────────────────────────────────────────────
PHONE_PATTERNS = {
    '(XXX) XXX-XXXX':  r'^\(\d{3}\) \d{3}-\d{4}$',
    'XXXXXXXXXX':       r'^\d{10}$',
    'XXX-XXX-XXXX':    r'^\d{3}-\d{3}-\d{4}$',
    '+1XXXXXXXXXX':    r'^\+1\d{10}$',
    'XXX.XXX.XXXX':    r'^\d{3}\.\d{3}\.\d{4}$',
}

def detect_phone_formats(df: pd.DataFrame, col: str, vendor_name: str) -> pd.DataFrame:
    """Detect and count different phone number formats in a column."""
    if col not in df.columns:
        return pd.DataFrame()

    log.info(f"  Detecting phone formats in {vendor_name}.{col}...")
    series = df[col].dropna()
    series = series[series != '']

    results = []
    for val in series:
        val_str = str(val).strip()
        matched = False
        for fmt_name, pattern in PHONE_PATTERNS.items():
            if re.match(pattern, val_str):
                results.append(fmt_name)
                matched = True
                break
        if not matched:
            results.append('Other/Unknown')

    from collections import Counter
    counts = Counter(results)

    rows = []
    for fmt, count in sorted(counts.items(), key=lambda x: -x[1]):
        rows.append({
            'Vendor':       vendor_name,
            'Column':       col,
            'Format':       fmt,
            'Count':        count,
            'Percentage %': round((count / len(series)) * 100, 1)
        })
    return pd.DataFrame(rows)

# ─────────────────────────────────────────────────────────────
# STEP 5: Duplicate estimate
# Count obvious exact duplicates within each vendor file.
# We use email as the primary dedup key where available.
# ─────────────────────────────────────────────────────────────
def estimate_duplicates(dataframes: dict) -> pd.DataFrame:
    """Estimate exact duplicates within each vendor file."""
    log.info("Estimating duplicates per vendor...")

    # Map each vendor to its email and phone column names
    email_cols  = {'vendor_a': 'EMAIL_ADDRESS',
                   'vendor_b': 'contact_email',
                   'vendor_c': 'EmailAddr'}
    phone_cols  = {'vendor_a': 'PHONE_NUMBER',
                   'vendor_b': 'contact_phone',
                   'vendor_c': 'PrimaryPhone'}

    rows = []
    for vendor, df in dataframes.items():
        total = len(df)
        email_col = email_cols.get(vendor)
        phone_col = phone_cols.get(vendor)

        # Exact duplicates on all columns
        full_dupes = total - df.drop_duplicates().shape[0]

        # Email duplicates (non-null emails only)
        email_dupes = 0
        if email_col and email_col in df.columns:
            non_null_email = df[df[email_col].notna() & (df[email_col] != '')]
            email_dupes = len(non_null_email) - non_null_email.drop_duplicates(
                subset=[email_col]).shape[0]

        # Phone duplicates (non-null phones only)
        phone_dupes = 0
        if phone_col and phone_col in df.columns:
            non_null_phone = df[df[phone_col].notna() & (df[phone_col] != '')]
            phone_dupes = len(non_null_phone) - non_null_phone.drop_duplicates(
                subset=[phone_col]).shape[0]

        rows.append({
            'Vendor':                vendor,
            'Total Rows':            total,
            'Full Row Duplicates':   full_dupes,
            'Email Duplicates':      email_dupes,
            'Phone Duplicates':      phone_dupes,
            'Est. Duplicate %':      round((email_dupes / total) * 100, 1)
        })

        log.info(f"  {vendor}: {full_dupes} full dupes, "
                 f"{email_dupes} email dupes, {phone_dupes} phone dupes")

    return pd.DataFrame(rows)

# ─────────────────────────────────────────────────────────────
# STEP 6: Channel classification preview
# How many records can we reach by email vs mail vs both?
# This gives EPIQ and the client an early estimate.
# ─────────────────────────────────────────────────────────────
def channel_preview(dataframes: dict) -> pd.DataFrame:
    """Estimate notification channel breakdown per vendor."""
    email_cols = {'vendor_a': 'EMAIL_ADDRESS',
                  'vendor_b': 'contact_email',
                  'vendor_c': 'EmailAddr'}
    phone_cols = {'vendor_a': 'PHONE_NUMBER',
                  'vendor_b': 'contact_phone',
                  'vendor_c': 'PrimaryPhone'}
    addr_cols  = {'vendor_a': 'STREET_ADDRESS',
                  'vendor_b': 'address',
                  'vendor_c': 'AddressLine1'}

    rows = []
    for vendor, df in dataframes.items():
        total = len(df)
        ec = email_cols.get(vendor)
        pc = phone_cols.get(vendor)
        ac = addr_cols.get(vendor)

        has_email = df[ec].notna() & (df[ec] != '') if ec in df.columns else pd.Series([False]*total)
        has_phone = df[pc].notna() & (df[pc] != '') if pc in df.columns else pd.Series([False]*total)
        has_addr  = df[ac].notna() & (df[ac] != '') if ac in df.columns else pd.Series([False]*total)

        both        = (has_email & has_addr).sum()
        email_only  = (has_email & ~has_addr).sum()
        mail_only   = (~has_email & has_addr).sum()
        no_contact  = (~has_email & ~has_addr).sum()

        rows.append({
            'Vendor':          vendor,
            'Total':           total,
            'Email + Mail':    both,
            'Email Only':      email_only,
            'Mail Only':       mail_only,
            'No Contact Info': no_contact,
            'No Contact %':    round((no_contact / total) * 100, 1)
        })
    return pd.DataFrame(rows)

# ─────────────────────────────────────────────────────────────
# STEP 7: Write the Excel report with formatting
# One sheet per vendor profile + format analysis sheets
# + a summary sheet at the front.
# ─────────────────────────────────────────────────────────────
def write_excel_report(
    profiles: dict,
    date_formats: pd.DataFrame,
    phone_formats: pd.DataFrame,
    duplicates: pd.DataFrame,
    channels: pd.DataFrame,
    output_path: str
):
    log.info(f"Writing quality report to {output_path}...")

    with pd.ExcelWriter(output_path, engine='openpyxl') as writer:

        # Sheet 1: Summary
        summary_data = {
            'Metric': [
                'Total raw records (all vendors)',
                'Vendor A rows',
                'Vendor B rows',
                'Vendor C rows',
                'Report generated at',
                'Prepared by'
            ],
            'Value': [
                21500,
                profiles['vendor_a']['Total Rows'].iloc[0],
                profiles['vendor_b']['Total Rows'].iloc[0],
                profiles['vendor_c']['Total Rows'].iloc[0],
                datetime.now().strftime('%Y-%m-%d %H:%M'),
                'DataShield Pipeline — Phase 2'
            ]
        }
        pd.DataFrame(summary_data).to_excel(
            writer, sheet_name='Summary', index=False)

        # Sheets 2-4: Column profiles per vendor
        for vendor, profile_df in profiles.items():
            sheet_name = f'Profile_{vendor[-1].upper()}'  # Profile_A, Profile_B, Profile_C
            profile_df.to_excel(writer, sheet_name=sheet_name, index=False)

        # Sheet 5: Date format analysis
        date_formats.to_excel(writer, sheet_name='Date_Formats', index=False)

        # Sheet 6: Phone format analysis
        phone_formats.to_excel(writer, sheet_name='Phone_Formats', index=False)

        # Sheet 7: Duplicate estimates
        duplicates.to_excel(writer, sheet_name='Duplicates', index=False)

        # Sheet 8: Channel preview
        channels.to_excel(writer, sheet_name='Channel_Preview', index=False)

    # ── Apply formatting with openpyxl ───────────────────────
    wb = load_workbook(output_path)

    # Color scheme
    HEADER_FILL   = PatternFill('solid', fgColor='1F3864')  # dark blue
    HEADER_FONT   = Font(color='FFFFFF', bold=True, size=11)
    WARN_FILL     = PatternFill('solid', fgColor='FFE699')   # amber
    HIGH_FILL     = PatternFill('solid', fgColor='FF7B7B')   # red
    ALT_FILL      = PatternFill('solid', fgColor='EEF2FF')   # light blue
    BORDER        = Border(
        bottom=Side(style='thin', color='CCCCCC')
    )

    for sheet_name in wb.sheetnames:
        ws = wb[sheet_name]

        # Format header row
        for cell in ws[1]:
            cell.fill = HEADER_FILL
            cell.font = HEADER_FONT
            cell.alignment = Alignment(horizontal='center', vertical='center')

        # Auto-width columns
        for col_idx, col in enumerate(ws.columns, 1):
            max_len = 0
            for cell in col:
                try:
                    if cell.value:
                        max_len = max(max_len, len(str(cell.value)))
                except:
                    pass
            ws.column_dimensions[get_column_letter(col_idx)].width = min(max_len + 4, 50)

        # Highlight high null rows in profile sheets
        if sheet_name.startswith('Profile_'):
            for row in ws.iter_rows(min_row=2):
                for cell in row:
                    if cell.column == 1:  # alternating row color
                        if cell.row % 2 == 0:
                            for c in row:
                                c.fill = ALT_FILL
                # Check Flag column for warnings
                flag_cell = row[-1]  # last column is Flag
                if flag_cell.value == '⚠ HIGH NULLS':
                    for c in row:
                        c.fill = HIGH_FILL
                elif flag_cell.value == '⚡ CHECK':
                    for c in row:
                        c.fill = WARN_FILL

        # Freeze top row on all sheets
        ws.freeze_panes = 'A2'

    wb.save(output_path)
    log.info(f"Report saved: {output_path}")

# ─────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────
if __name__ == '__main__':
    log.info("Operation DataShield — Phase 2: Profiling starting")
    log.info(f"Run timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    # Load raw tables
    dataframes = load_raw_tables()

    # Profile each vendor
    profiles = {}
    for vendor, df in dataframes.items():
        profiles[vendor] = profile_dataframe(df, vendor)

    # Date format analysis
    log.info("Running date format analysis...")
    dob_cols = {
        'vendor_a': 'DATE_OF_BIRTH',
        'vendor_b': 'birthdate',
        'vendor_c': 'DOB'
    }
    all_date_formats = []
    for vendor, df in dataframes.items():
        col = dob_cols.get(vendor)
        result = detect_date_formats(df, col, vendor)
        if not result.empty:
            all_date_formats.append(result)
    date_formats_df = pd.concat(all_date_formats, ignore_index=True)

    # Phone format analysis
    log.info("Running phone format analysis...")
    phone_cols_map = {
        'vendor_a': 'PHONE_NUMBER',
        'vendor_b': 'contact_phone',
        'vendor_c': 'PrimaryPhone'
    }
    all_phone_formats = []
    for vendor, df in dataframes.items():
        col = phone_cols_map.get(vendor)
        result = detect_phone_formats(df, col, vendor)
        if not result.empty:
            all_phone_formats.append(result)
    phone_formats_df = pd.concat(all_phone_formats, ignore_index=True)

    # Duplicate estimates
    duplicates_df = estimate_duplicates(dataframes)

    # Channel preview
    channels_df = channel_preview(dataframes)

    # Write report
    output_file = 'data_quality_report.xlsx'
    write_excel_report(
        profiles,
        date_formats_df,
        phone_formats_df,
        duplicates_df,
        channels_df,
        output_file
    )

    log.info("")
    log.info("=" * 60)
    log.info("PHASE 2 COMPLETE")
    log.info("=" * 60)
    log.info(f"Output: {output_file}")
    log.info("Open the Excel file to review all quality findings.")
    log.info("This report should be reviewed before Phase 3 begins.")
    log.info("=" * 60)