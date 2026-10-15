# Regenerates slip_template.json: a snapshot of how the NEWEST permission-slip
# block in the Google Doc is formatted, with every student's text stripped (the
# repo is public). Run it after restyling a block in the doc, then commit the
# result. See perm_slip_writer.py for how the snapshot is used.
#
#   python make_slip_template.py [doc-url-or-id]    (default: PERM_SLIP_DOC_URL)
#
# Reads the doc only; it never writes to it.

import json
import os
import sys

from dotenv import load_dotenv

import perm_slip_check
import perm_slip_writer

load_dotenv(".env")

source = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("PERM_SLIP_DOC_URL", "")
doc_id = perm_slip_check.doc_id_from_url(source)
if not doc_id:
    sys.exit("Give a doc url/id, or set PERM_SLIP_DOC_URL.")

headers, email = perm_slip_writer._headers(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON_B64"])
template = perm_slip_writer.extract_template(perm_slip_writer.get_doc(doc_id, headers, email))
with open(perm_slip_writer.TEMPLATE_PATH, "w", encoding="utf8") as fh:
    json.dump(template, fh, indent=1, ensure_ascii=False)

tables = [i for i in template["items"] if i["kind"] == "table"]
print(f"Wrote {perm_slip_writer.TEMPLATE_PATH}: {len(template['items'])} items, {len(tables)} tables "
      f"({', '.join(t['role'] for t in tables)}).")
