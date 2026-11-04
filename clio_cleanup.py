# Takes removed (kicked) members off the Clio roster (2026-11-04).
#
# Used two ways:
#   - by the Members page's "Remove kicked members", right after a removal;
#   - as a script (python clio_cleanup.py, via the "Clio cleanup" workflow)
#     to sweep out everyone already marked access_roles.is_removed.
#
# Only the current year's tab is touched (CLIO_CURRENT_TAB_ID in shared.py);
# older per-year tabs are the club's history and stay as they were.
# A row is matched by its admission number (column A) or institutional email
# (column D), both derived from the member's school email, never by name.
#
# Deliberately does NOT import shared.py (which pulls in Streamlit), so the
# workflow stays light. Prints counts only, never names or emails: the
# workflow logs of this public repo are public.

import base64
import json
import os
import re

CLIO_SHEET_ID = "18M5VLCmC0tczG61m9Zx2OWQXP9jOa_oAfpPBo_BhAi8"
CLIO_CURRENT_TAB_ID = 930634755  # keep in step with shared.py


def sheets_client_from_env():
    b64 = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON_B64")
    if not b64:
        return None
    import gspread
    from google.oauth2.service_account import Credentials

    creds = Credentials.from_service_account_info(
        json.loads(base64.b64decode(b64)), scopes=["https://www.googleapis.com/auth/spreadsheets"],
    )
    return gspread.authorize(creds)


def _admission_no(email):
    # School emails start with the admission number: r12345name@dpsrkp.net.
    m = re.match(r"([a-z]\d{5})", (email or "").strip().lower())
    return m.group(1).upper() if m else None


def remove_from_clio(gc, emails):
    # Deletes the current tab's rows for these school emails. Returns how
    # many rows were deleted. Raises on a Sheets error; callers decide.
    emails = {(e or "").strip().lower() for e in emails if e}
    admission_nos = {a for a in (_admission_no(e) for e in emails) if a}
    if not emails:
        return 0
    ws = gc.open_by_key(CLIO_SHEET_ID).get_worksheet_by_id(CLIO_CURRENT_TAB_ID)
    rows = ws.get_all_values()
    doomed = []
    for i, row in enumerate(rows[1:], start=2):  # row 1 is the header
        adm = (row[0] if len(row) > 0 else "").strip().upper()
        email = (row[3] if len(row) > 3 else "").strip().lower()
        if adm in admission_nos or email in emails:
            doomed.append(i)
    # Bottom-up, so deleting one row never shifts the next one's number.
    for r in sorted(doomed, reverse=True):
        ws.delete_rows(r)
    return len(doomed)


if __name__ == "__main__":
    from supabase import create_client

    supabase = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])
    removed = [r["email"] for r in supabase.table("access_roles").select("email").eq("is_removed", True).execute().data]
    gc = sheets_client_from_env()
    if gc is None:
        raise SystemExit("GOOGLE_SERVICE_ACCOUNT_JSON_B64 isn't set")
    deleted = remove_from_clio(gc, removed)
    print(f"{len(removed)} removed member(s) on record; deleted {deleted} Clio row(s).")
