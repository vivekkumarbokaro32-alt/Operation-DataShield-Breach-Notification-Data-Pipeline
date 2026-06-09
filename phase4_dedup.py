"""
Operation DataShield — Phase 4: Deduplication
Goal: Remove duplicate records across all 3 vendor files.
      3 passes: exact email → exact phone → fuzzy name+address
Output: staging.deduped — one row per unique real person
"""

import pandas as pd
from sqlalchemy import create_engine, text
import logging
from datetime import datetime
import re

# ─────────────────────────────────────────────────────────────
# SETUP
# ─────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    handlers=[
        logging.FileHandler('phase4_dedup.log'),
        logging.StreamHandler()
    ]
)
log = logging.getLogger('phase4')

DB_URL = "postgresql+psycopg2://postgres:Vivek%40@localhost:5432/Datasheild"
engine = create_engine(DB_URL)

# Source preference order — which vendor's record wins when
# we find a duplicate. vendor_a is the oldest/most authoritative.
# Change this order based on client instructions.
SOURCE_PRIORITY = {'vendor_a': 1, 'vendor_b': 2, 'vendor_c': 3}

# ─────────────────────────────────────────────────────────────
# PASS 1: Exact deduplication on email
# Logic: if two records share the same email address,
# they are the same person. Keep one — prefer vendor_a.
# Records with no email (empty string) are NOT deduplicated
# here — each blank email is treated as unique.
# ─────────────────────────────────────────────────────────────
def dedup_pass1_email(df: pd.DataFrame) -> tuple:
    """
    Exact dedup on email address.
    - Empty emails are excluded from dedup (each is unique)
    - Among duplicates, keep record from highest priority vendor
    Returns: (survivors_df, removed_count, removed_df)
    """
    log.info("Pass 1: Exact dedup on email...")
    before = len(df)

    # Split into two groups:
    # has_email  — records with a valid email (dedup these)
    # no_email   — records with blank email (all pass through)
    has_email = df[df['email'] != ''].copy()
    no_email  = df[df['email'] == ''].copy()

    log.info(f"  Records with email:    {len(has_email):,}")
    log.info(f"  Records without email: {len(no_email):,}")

    # Add priority column so we can rank within each email group
    has_email['_priority'] = has_email['record_source'].map(SOURCE_PRIORITY)

    # Sort by email then priority — best record first in each group
    has_email = has_email.sort_values(['email', '_priority'])

    # Keep first occurrence per email = lowest priority number = best vendor
    survivors_email = has_email.drop_duplicates(subset=['email'], keep='first')
    removed_email   = has_email[has_email.duplicated(subset=['email'], keep='first')]

    # Drop the helper column
    survivors_email = survivors_email.drop(columns=['_priority'])

    # Combine survivors with no-email records
    survivors = pd.concat([survivors_email, no_email], ignore_index=True)

    removed_count = before - len(survivors)
    log.info(f"  Before: {before:,} | After: {len(survivors):,} | Removed: {removed_count:,}")

    return survivors, removed_count, removed_email


# ─────────────────────────────────────────────────────────────
# PASS 2: Exact deduplication on phone
# Logic: same phone number = same person.
# Only applied to records that survived Pass 1.
# Records with no phone are excluded (same as email logic).
# ─────────────────────────────────────────────────────────────
def dedup_pass2_phone(df: pd.DataFrame) -> tuple:
    """
    Exact dedup on phone_e164.
    Only runs on records that survived Pass 1.
    Returns: (survivors_df, removed_count, removed_df)
    """
    log.info("Pass 2: Exact dedup on phone...")
    before = len(df)

    has_phone = df[df['phone_e164'] != ''].copy()
    no_phone  = df[df['phone_e164'] == ''].copy()

    log.info(f"  Records with phone:    {len(has_phone):,}")
    log.info(f"  Records without phone: {len(no_phone):,}")

    has_phone['_priority'] = has_phone['record_source'].map(SOURCE_PRIORITY)
    has_phone = has_phone.sort_values(['phone_e164', '_priority'])

    survivors_phone = has_phone.drop_duplicates(subset=['phone_e164'], keep='first')
    removed_phone   = has_phone[has_phone.duplicated(subset=['phone_e164'], keep='first')]

    survivors_phone = survivors_phone.drop(columns=['_priority'])

    survivors = pd.concat([survivors_phone, no_phone], ignore_index=True)

    removed_count = before - len(survivors)
    log.info(f"  Before: {before:,} | After: {len(survivors):,} | Removed: {removed_count:,}")

    return survivors, removed_count, removed_phone


# ─────────────────────────────────────────────────────────────
# PASS 3: Fuzzy deduplication on name + address
#
# This is the expensive pass. We can't compare every record
# against every other record — that's 21,500 x 21,500 = 462M
# comparisons. Way too slow.
#
# Solution: BLOCKING
# We only compare records within the same Soundex group.
# Soundex("Smith") = Soundex("Smyth") = S530
# So we only compare Smiths against Smiths, not against Garcias.
# This reduces comparisons from 462M to a manageable number.
#
# Within each Soundex block:
# We compute token_sort_ratio on (full_name + zip5)
# Score >= 85 = candidate duplicate pair
# ─────────────────────────────────────────────────────────────
def soundex(name: str) -> str:
    """
    Compute Soundex code for a name.
    Returns a 4-character code like S530.
    Same-sounding names get the same code.
    """
    if not name:
        return '0000'

    name = name.upper()
    # Keep only letters
    name = re.sub(r'[^A-Z]', '', name)
    if not name:
        return '0000'

    # Soundex coding map
    coding = {
        'B':1,'F':1,'P':1,'V':1,
        'C':2,'G':2,'J':2,'K':2,'Q':2,'S':2,'X':2,'Z':2,
        'D':3,'T':3,
        'L':4,
        'M':5,'N':5,
        'R':6
    }

    first_letter = name[0]
    code = first_letter
    prev = coding.get(first_letter, 0)

    for char in name[1:]:
        curr = coding.get(char, 0)
        if curr != 0 and curr != prev:
            code += str(curr)
        prev = curr if curr != 0 else prev
        if len(code) == 4:
            break

    return code.ljust(4, '0')


def token_sort_ratio(s1: str, s2: str) -> int:
    """
    Compare two strings by sorting their words alphabetically
    before comparing. This handles word-order differences.
    'John Smith 60601' vs 'Smith John 60601' → 100
    Returns a score 0-100.
    """
    if not s1 or not s2:
        return 0

    # Sort words alphabetically and rejoin
    t1 = ' '.join(sorted(s1.lower().split()))
    t2 = ' '.join(sorted(s2.lower().split()))

    # Levenshtein ratio
    if t1 == t2:
        return 100

    max_len = max(len(t1), len(t2))
    if max_len == 0:
        return 100

    # Build distance matrix
    m, n = len(t1), len(t2)
    dp = list(range(n + 1))

    for i in range(1, m + 1):
        prev = dp[0]
        dp[0] = i
        for j in range(1, n + 1):
            temp = dp[j]
            if t1[i-1] == t2[j-1]:
                dp[j] = prev
            else:
                dp[j] = 1 + min(prev, dp[j], dp[j-1])
            prev = temp

    dist = dp[n]
    return int((1 - dist / max_len) * 100)


def dedup_pass3_fuzzy(df: pd.DataFrame,
                      threshold: int = 85) -> tuple:
    """
    Fuzzy dedup using Soundex blocking + token_sort_ratio.
    Only compares records within the same Soundex(last_name) block.
    Pairs scoring >= threshold are considered duplicates.
    The record with higher source priority survives.
    Returns: (survivors_df, removed_count, pairs_df)
    """
    log.info(f"Pass 3: Fuzzy dedup (threshold={threshold})...")
    log.info("  Building Soundex blocks...")
    before = len(df)

    # Build the comparison key: full_name + zip5
    df = df.copy()
    df['_fullname'] = (df['first_name'] + ' ' + df['last_name']).str.strip()
    df['_compare_key'] = df['_fullname'] + ' ' + df['zip5'].fillna('')
    df['_soundex'] = df['last_name'].apply(soundex)
    df['_priority'] = df['record_source'].map(SOURCE_PRIORITY)
    df['_idx'] = range(len(df))

    # Group by Soundex block
    blocks = df.groupby('_soundex')
    log.info(f"  Total Soundex blocks: {blocks.ngroups:,}")

    # Track which indices to remove
    to_remove = set()
    candidate_pairs = []

    block_count = 0
    for soundex_code, block in blocks:
        block_count += 1
        if len(block) < 2:
            continue  # Only 1 record in this block — skip

        # Compare all pairs within this block
        block_list = block.to_dict('records')
        for i in range(len(block_list)):
            for j in range(i + 1, len(block_list)):
                r1 = block_list[i]
                r2 = block_list[j]

                # Skip if either is already marked for removal
                if r1['_idx'] in to_remove or r2['_idx'] in to_remove:
                    continue

                # Compute similarity
                score = token_sort_ratio(r1['_compare_key'], r2['_compare_key'])

                if score >= threshold:
                    candidate_pairs.append({
                        'record_1_id':     r1['record_id'],
                        'record_2_id':     r2['record_id'],
                        'name_1':          r1['_fullname'],
                        'name_2':          r2['_fullname'],
                        'zip_1':           r1['zip5'],
                        'zip_2':           r2['zip5'],
                        'source_1':        r1['record_source'],
                        'source_2':        r2['record_source'],
                        'similarity_score': score
                    })

                    # Remove the lower priority record
                    if r1['_priority'] <= r2['_priority']:
                        to_remove.add(r2['_idx'])
                    else:
                        to_remove.add(r1['_idx'])

    log.info(f"  Candidate duplicate pairs found: {len(candidate_pairs):,}")
    log.info(f"  Records to remove: {len(to_remove):,}")

    # Build survivors and removed sets
    survivors = df[~df['_idx'].isin(to_remove)].copy()
    removed   = df[df['_idx'].isin(to_remove)].copy()

    # Drop helper columns
    helper_cols = ['_fullname','_compare_key','_soundex','_priority','_idx']
    survivors = survivors.drop(columns=helper_cols)
    removed   = removed.drop(columns=helper_cols)

    removed_count = before - len(survivors)
    log.info(f"  Before: {before:,} | After: {len(survivors):,} | Removed: {removed_count:,}")

    pairs_df = pd.DataFrame(candidate_pairs) if candidate_pairs else pd.DataFrame()
    return survivors, removed_count, pairs_df


# ─────────────────────────────────────────────────────────────
# DEDUP SUMMARY REPORT
# After all 3 passes, log a clear summary.
# This goes to the client for sign-off before notification.
# ─────────────────────────────────────────────────────────────
def print_dedup_summary(original: int,
                         after_p1: int, removed_p1: int,
                         after_p2: int, removed_p2: int,
                         after_p3: int, removed_p3: int):
    total_removed = original - after_p3
    pct_removed   = round(total_removed / original * 100, 1)

    log.info("")
    log.info("=" * 60)
    log.info("PHASE 4 DEDUPLICATION SUMMARY")
    log.info("=" * 60)
    log.info(f"  Original records:          {original:,}")
    log.info(f"  After Pass 1 (email):      {after_p1:,}  (-{removed_p1:,})")
    log.info(f"  After Pass 2 (phone):      {after_p2:,}  (-{removed_p2:,})")
    log.info(f"  After Pass 3 (fuzzy):      {after_p3:,}  (-{removed_p3:,})")
    log.info(f"  ─────────────────────────────────────")
    log.info(f"  Total removed:             {total_removed:,}")
    log.info(f"  Total reduction:           {pct_removed}%")
    log.info(f"  Unique individuals:        {after_p3:,}")
    log.info("=" * 60)


# ─────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────
if __name__ == '__main__':
    log.info("Operation DataShield — Phase 4: Deduplication starting")
    log.info(f"Run timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    # ── Load staging.normalized ───────────────────────────────
    log.info("Loading staging.normalized...")
    with engine.connect() as conn:
        df = pd.read_sql(text("SELECT * FROM staging.normalized"), conn)
    log.info(f"  Loaded {len(df):,} rows")
    original_count = len(df)

    # ── Pass 1: Exact email dedup ─────────────────────────────
    df_p1, removed_p1, _ = dedup_pass1_email(df)

    # ── Pass 2: Exact phone dedup ─────────────────────────────
    df_p2, removed_p2, _ = dedup_pass2_phone(df_p1)

    # ── Pass 3: Fuzzy name+address dedup ─────────────────────
    df_p3, removed_p3, pairs_df = dedup_pass3_fuzzy(df_p2, threshold=85)

    # ── Write staging.deduped ─────────────────────────────────
    log.info("Writing staging.deduped...")
    df_p3.to_sql(
        name='deduped',
        con=engine,
        schema='staging',
        if_exists='replace',
        index=False,
        method='multi',
        chunksize=2000
    )

    # ── Write fuzzy candidate pairs for review ────────────────
    if not pairs_df.empty:
        pairs_df.to_sql(
            name='fuzzy_pairs_review',
            con=engine,
            schema='staging',
            if_exists='replace',
            index=False
        )
        log.info(f"  Fuzzy pairs saved to staging.fuzzy_pairs_review")

    # ── Verify DB count ───────────────────────────────────────
    with engine.connect() as conn:
        db_count = conn.execute(
            text("SELECT COUNT(*) FROM staging.deduped")
        ).fetchone()[0]

    # ── Print summary ─────────────────────────────────────────
    print_dedup_summary(
        original_count,
        len(df_p1), removed_p1,
        len(df_p2), removed_p2,
        len(df_p3), removed_p3
    )

    log.info(f"  DB count verified: {db_count:,}")
    log.info(f"  Count match: {'✓ YES' if db_count == len(df_p3) else '✗ NO'}")
    log.info("staging.deduped is ready. Proceed to Phase 5.")