# Exun 2026 materials hub — a structured, two-way place for RoboKnights
# and Exun to hand each other what they actually need for the Exun event:
# a write-up (a real rich-text box — Bold/Italic/Underline, genuinely
# formatted, not raw markdown asterisks), a link, or a file (including an
# Excel sheet or a slide deck). A SEPARATE page from the existing private
# RoboKnights<>Exun channel (exun_channel.py) — same audience
# (EXUN_CHANNEL_MEMBERS), different shape of content: a feed of shared
# materials, not a running chat.

import time

import streamlit as st

from shared import (
    EXUN_CHANNEL_MEMBERS, EXUN_EMAILS, cached_table, format_relative, get_client, get_storage_client,
    invalidate_cache, notify_exun_hub_post, plain_text_from_rich_html, render_file_open_and_download,
    render_rich_html_editor, safe_write, sanitize_rich_html, wrap_rich_html_for_storage,
)

# Belt-and-braces alongside the nav gating in app.py, same two-layer
# pattern every other restricted page here uses — st.navigation's page
# list alone doesn't stop a direct URL hit.
if st.session_state.auth_user["email"] not in EXUN_CHANNEL_MEMBERS:
    st.error("This page is only for RoboKnights and Exun leadership.")
    st.stop()

client = get_client()
storage = get_storage_client()
current_user_id = st.session_state.current_user_id
current_user_name = st.session_state.current_user_name
user_name_by_id = st.session_state.user_name_by_id
user_email_by_id = st.session_state.user_email_by_id

# This hub is deliberately two-way (host's explicit request, 2026-09-26) —
# unlike every other page Exun can see, where they're genuinely view-only.
# is_read_only (is_exun or is_viewer) would hide the composer for Exun
# too, so this page asks a narrower question instead: only VIEWER (a
# look-around/demo account with no business writing anywhere) is excluded.
# The actual enforcement backstop is safe_write(..., allow_exun=True)
# below, not this — this only decides whether to show the form at all.
can_post_here = not st.session_state.is_viewer

BUCKET = "exun-event-materials"
ALLOWED_EXTENSIONS = {
    "pdf", "doc", "docx", "ppt", "pptx", "xls", "xlsx", "zip",
    "png", "jpg", "jpeg", "txt", "csv",
}
MAX_MB = 25

st.title(":material/folder_shared: Exun 2026 Materials")
st.caption(
    "Whatever Exun needs for the event, and whatever they send back — write-ups, "
    "links, files. Visible to RoboKnights and Exun leadership only."
)

if "hub_message" not in st.session_state:
    st.session_state.hub_message = None
if st.session_state.hub_message:
    st.toast(st.session_state.hub_message, icon=":material/check_circle:")
    st.session_state.hub_message = None

if can_post_here:
    with st.container(border=True):
        st.subheader("Share something")
        render_rich_html_editor("hub_new_body_rich", placeholder="Write-up (optional)...")
        link = st.text_input("Link (optional)", key="hub_new_link", placeholder="https://...")
        upload = st.file_uploader(
            "Attach a file (optional)", type=list(ALLOWED_EXTENSIONS),
            key="hub_new_file", disabled=storage is None,
            help=f"Any of: {', '.join(sorted(ALLOWED_EXTENSIONS))} — up to {MAX_MB} MB.",
        )
        if storage is None:
            st.caption("File attachments are off until a host adds SUPABASE_SERVICE_KEY.")
        if st.button("Post", icon=":material/send:", type="primary", key="hub_post_btn"):
            body_html = wrap_rich_html_for_storage("hub_new_body_rich")
            if not body_html and not link.strip() and upload is None:
                st.error("Add a write-up, a link, or a file before posting.")
            else:
                file_path, file_name = None, None
                if upload is not None:
                    raw = upload.getvalue()
                    ext = upload.name.rsplit(".", 1)[-1].lower()
                    if ext not in ALLOWED_EXTENSIONS:
                        st.error("That file type isn't allowed.")
                        st.stop()
                    if len(raw) > MAX_MB * 1024 * 1024:
                        st.error(f"That file is {len(raw) / 1024 / 1024:.1f} MB — the limit is {MAX_MB} MB.")
                        st.stop()
                    if storage is None:
                        st.error("File uploads aren't set up on this server yet.")
                        st.stop()
                    # Named by post_id would need the row to exist first —
                    # simpler to key by author + a timestamp instead, same
                    # "never trust the original filename as a storage key"
                    # reasoning profile.py's photo path already follows.
                    file_path = f"{current_user_id}_{int(time.time())}.{ext}"
                    file_name = upload.name
                    with safe_write("upload this file", allow_exun=True):
                        storage.storage.from_(BUCKET).upload(
                            file_path, raw,
                            {"content-type": upload.type or "application/octet-stream", "upsert": "true"},
                        )
                link_clean = link.strip()
                if link_clean and not link_clean.startswith(("http://", "https://")):
                    link_clean = "https://" + link_clean
                with safe_write("post this", allow_exun=True):
                    client.table("exun_hub_posts").insert({
                        "author_id": current_user_id,
                        "body": body_html or None,
                        "link": link_clean or None,
                        "file_path": file_path,
                        "file_name": file_name,
                    }).execute()
                    invalidate_cache()
                summary_bits = [f":inbox_tray: **{current_user_name} shared something in the Exun 2026 hub**"]
                if body_html:
                    # Discord/email get plain text — the rich formatting
                    # only means something rendered on the dashboard itself.
                    plain_body = plain_text_from_rich_html(body_html)
                    if plain_body:
                        summary_bits.append(plain_body[:200])
                if link_clean:
                    summary_bits.append(link_clean)
                if file_name:
                    summary_bits.append(f"Attached: {file_name}")
                summary_bits.append("Full details are on the dashboard.")
                notify_exun_hub_post(
                    st.session_state.auth_user["email"], "\n".join(summary_bits)
                )
                st.session_state.hub_message = "Posted."
                for k in ("hub_new_body_rich", "hub_new_body_rich_size", "hub_new_link"):
                    st.session_state.pop(k, None)
                st.rerun()

st.divider()

posts = sorted(cached_table("exun_hub_posts"), key=lambda p: p["created_at"], reverse=True)
if not posts:
    st.caption("Nothing shared yet.")
for p in posts:
    author_email = user_email_by_id.get(p["author_id"], "")
    with st.container(border=True, key=f"rkcard_hubpost_{p['post_id']}"):
        header_col, badge_col = st.columns([3, 1], vertical_alignment="center")
        header_col.markdown(f"**{user_name_by_id.get(p['author_id'], 'Unknown')}**")
        header_col.caption(format_relative(p["created_at"]))
        if author_email in EXUN_EMAILS:
            badge_col.badge("Exun", color="blue", icon=":material/handshake:")
        if p.get("body"):
            st.markdown(sanitize_rich_html(p["body"]), unsafe_allow_html=True)
        if p.get("link"):
            st.link_button("Open link", p["link"], icon=":material/link:")
        render_file_open_and_download(
            storage, BUCKET, p.get("file_path"), p.get("file_name"), f"hubpost_{p['post_id']}",
        )
