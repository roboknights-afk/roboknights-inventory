# Competition rulebooks (2026-11-04): a host uploads an event's rules PDF on
# the Competitions page, and the Discord bot answers rules questions ("max
# bot weight for Robo War?") from it, quoting the page. Only the TEXT is
# kept, one string per page, in competition_rulebooks.pages - the PDF
# itself isn't stored, so there's no Storage bucket to set up.
#
# Host-only to upload or delete. Needs the competition_rulebooks migration
# at the end of supabase_schema.sql; until then the section says so.

import io

import streamlit as st
from pypdf import PdfReader

from shared import cached_table, get_client, invalidate_cache, safe_write

MAX_PDF_BYTES = 15 * 1024 * 1024
MAX_PAGES = 200


def extract_pages(pdf_bytes):
    # -> [text of page 1, text of page 2, ...]. A scanned PDF (pictures of
    # pages, no text layer) comes back as empty strings.
    reader = PdfReader(io.BytesIO(pdf_bytes))
    return [(page.extract_text() or "").strip() for page in reader.pages[:MAX_PAGES]]


def _table_ready():
    try:
        get_client().table("competition_rulebooks").select("rulebook_id").limit(1).execute()
        return True
    except Exception:
        return False


def render_rulebooks(comp, events):
    cid = comp["competition_id"]
    try:
        books = [b for b in cached_table("competition_rulebooks") if b["competition_id"] == cid]
    except Exception:
        books = None
    label = f"Rulebooks ({len(books)})" if books else "Rulebooks"
    with st.expander(label, icon=":material/menu_book:"):
        if books is None or not _table_ready():
            st.caption(
                "Needs a one-time database change: run the competition_rulebooks "
                "statement at the end of supabase_schema.sql in Supabase's SQL editor."
            )
            return
        st.caption(
            "Upload an event's rules PDF and the Discord bot will answer rules questions "
            "from it, quoting the page. Only the text is saved, not the file."
        )
        event_name = {e["event_id"]: e["name"] for e in events}
        for b in books:
            row = st.columns([5, 1], vertical_alignment="center")
            pages = b.get("pages") or []
            readable = sum(1 for p in pages if p.strip())
            row[0].markdown(
                f":material/description: **{b['file_name']}**"
                f" · {event_name.get(b.get('event_id'), 'whole competition')}"
                f" · {readable} of {len(pages)} page{'s' if len(pages) != 1 else ''} readable"
            )
            if row[1].button("Delete", key=f"rb_del_{b['rulebook_id']}", icon=":material/delete:"):
                with safe_write("delete this rulebook"):
                    get_client().table("competition_rulebooks").delete().eq("rulebook_id", b["rulebook_id"]).execute()
                    invalidate_cache()
                    st.rerun()

        with st.form(f"rb_add_{cid}", clear_on_submit=True, border=False):
            which = st.selectbox(
                "For", [None] + [e["event_id"] for e in events],
                format_func=lambda eid: "The whole competition" if eid is None else event_name[eid],
                key=f"rb_event_{cid}",
            )
            pdf = st.file_uploader("Rules PDF", type=["pdf"], key=f"rb_file_{cid}")
            if st.form_submit_button("Add rulebook", icon=":material/upload_file:"):
                if pdf is None:
                    st.error("Choose a PDF first.")
                elif pdf.size > MAX_PDF_BYTES:
                    st.error("That PDF is over 15 MB.")
                else:
                    try:
                        pages = extract_pages(pdf.getvalue())
                    except Exception:
                        pages = None
                    if not pages:
                        st.error("Couldn't read that PDF.")
                    elif not any(p.strip() for p in pages):
                        st.error(
                            "That PDF has no text in it (it's probably scanned pictures of pages), "
                            "so the bot couldn't read it. Try a text PDF of the rules instead."
                        )
                    else:
                        with safe_write("add this rulebook"):
                            get_client().table("competition_rulebooks").insert({
                                "competition_id": cid, "event_id": which,
                                "file_name": pdf.name, "pages": pages,
                            }).execute()
                            invalidate_cache()
                            st.rerun()
