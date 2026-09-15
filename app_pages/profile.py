# Your profile — the one page where a member changes things about
# themselves rather than about the club: their photo, and their social
# handles.
#
# Why this is in Streamlit at all, given Phase 2 is replacing it: the new
# dashboard is not deployed yet, so nobody can reach it. Streamlit is where
# members actually are today, and photos are needed for the website now.
# The student chose this knowing it gets rebuilt at cutover (2026-08-31).
#
# Photos live in a PRIVATE Supabase Storage bucket, not in this table and
# not on the public web. See supabase_schema.sql for why that bucket must
# stay private. Getting a photo onto roboknights.in is a second, separate
# tick — uploading alone does not publish anything.

import io
import re

import streamlit as st

from shared import cached_table, get_client, get_storage_client, invalidate_cache, safe_write

client = get_client()
storage = get_storage_client()
user_id = st.session_state.current_user_id

BUCKET = "member-photos"
ALLOWED = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png"}
MAX_MB = 5
MAX_EDGE = 800  # px; a directory photo never needs to be bigger

st.title(":material/account_circle: Your profile")
st.caption(
    "Your photo and links for the club directory and the club website. "
    "Nobody else can change these."
)

me = next((u for u in cached_table("users") if u["user_id"] == user_id), None)
if me is None:
    st.error("No member record for this account — tell a host.")
    st.stop()

# Belt and braces alongside the nav gating in app.py, which is what
# actually keeps adhocs/hosts/Exun/viewers from seeing this page in the
# first place — this just refuses to render anything if it's somehow
# reached anyway (a stale link, a bookmark from before a role changed).
if me.get("role") not in ("member", "core_member"):
    st.error("This page is for members only.")
    st.stop()


def _clean_handle(value, kind):
    # People paste whole profile URLs into boxes labelled "handle". Rather
    # than reject that, pull the handle out of it — the URL is rebuilt from
    # the handle at render time, which is what stops a bare domain ever
    # being stored as if it were a person's link.
    text = (value or "").strip()
    if not text:
        return ""
    text = re.sub(r"^https?://", "", text, flags=re.I)
    text = re.sub(r"^(www\.|in\.)", "", text, flags=re.I)
    for prefix in (f"{kind}.com/", "linkedin.com/in/", "linkedin.com/"):
        if text.lower().startswith(prefix):
            text = text[len(prefix):]
    text = text.strip("/@ ").split("?")[0].split("/")[0]
    return text


def _shrink(raw, ext):
    # Pillow ships with Streamlit, so this needs no new dependency. If it
    # is somehow missing, store the original rather than lose the upload.
    try:
        from PIL import Image
    except ImportError:
        return raw
    try:
        image = Image.open(io.BytesIO(raw))
        image.thumbnail((MAX_EDGE, MAX_EDGE))
        buffer = io.BytesIO()
        if ext == "png":
            image.save(buffer, format="PNG", optimize=True)
        else:
            image.convert("RGB").save(buffer, format="JPEG", quality=85, optimize=True)
        return buffer.getvalue()
    except Exception:
        return raw


# --- Photo -----------------------------------------------------------------

st.subheader("Photo")

if me.get("photo_path"):
    if storage is None:
        st.warning(
            "A photo is on file, but this server isn't set up to show or "
            "replace it yet — SUPABASE_SERVICE_KEY is missing. Tell a host."
        )
    else:
        try:
            st.image(
                storage.storage.from_(BUCKET).download(me["photo_path"]),
                width=180,
                caption="On file now",
            )
        except Exception:
            st.warning("Your photo is on file but could not be loaded just now.")
else:
    st.info("No photo yet. Until you add one the website shows your initials.")

upload = st.file_uploader(
    "Upload a photo",
    type=list(ALLOWED),
    help=f"JPG, JPEG or PNG, up to {MAX_MB} MB. Shrunk to {MAX_EDGE}px automatically.",
    key="profile_photo",
    disabled=storage is None,
)
if storage is None:
    st.caption("Uploads are off until a host adds SUPABASE_SERVICE_KEY.")

if upload is not None and storage is not None:
    raw = upload.getvalue()
    ext = upload.name.rsplit(".", 1)[-1].lower()
    if ext not in ALLOWED:
        st.error("That is not a .jpg, .jpeg or .png file.")
    elif len(raw) > MAX_MB * 1024 * 1024:
        st.error(f"That file is {len(raw) / 1024 / 1024:.1f} MB — the limit is {MAX_MB} MB.")
    else:
        st.image(raw, width=180, caption="About to be saved")
        if st.button("Save this photo", icon=":material/upload:", type="primary"):
            with safe_write("save your photo"):
                data = _shrink(raw, ext)
                # Named by user_id, not by name: a photo should not be
                # findable by guessing whose it is. One file per member, so
                # a new upload replaces the old rather than piling up.
                path = f"{user_id}.{'png' if ext == 'png' else 'jpg'}"
                storage.storage.from_(BUCKET).upload(
                    path,
                    data,
                    {"content-type": ALLOWED[ext], "upsert": "true"},
                )
                client.table("users").update({"photo_path": path}).eq(
                    "user_id", user_id
                ).execute()
                invalidate_cache()
                st.success("Photo saved.")
                st.rerun()

# The publish decision, deliberately its own control rather than something
# that happens because a file was uploaded. Ticking this is a REQUEST, not
# an immediate publish — a host reviews it on their own "Website" page
# before anything actually appears on roboknights.in. See
# supabase_schema.sql's comment on why that review step exists at all.
st.divider()
publish = st.checkbox(
    "Show my photo on roboknights.in",
    value=bool(me.get("photo_public")),
    disabled=not me.get("photo_path"),
    help="A host reviews this before it actually appears on the site.",
)
if publish != bool(me.get("photo_public")):
    if st.button("Save that choice", icon=":material/save:"):
        with safe_write("change where your photo is shown"):
            client.table("users").update({
                "photo_public": publish,
                # A fresh decision - checking or unchecking it - goes back
                # to the front of the review queue, even if a host already
                # decided once before.
                "website_status": "pending",
            }).eq("user_id", user_id).execute()
            invalidate_cache()
            st.success(
                "Sent for a host to review."
                if publish
                else "Your photo is dashboard-only now."
            )
            st.rerun()

status = me.get("website_status") or "pending"
if me.get("photo_path") and not me.get("photo_public"):
    st.caption("Your photo is in the club directory. It is not on the public website.")
elif me.get("photo_public") and status == "pending":
    st.caption(":material/hourglass_empty: Waiting for a host to review this.")
elif me.get("photo_public") and status == "approved":
    st.caption(":material/public: On the website.")
elif me.get("photo_public") and status == "declined":
    note = me.get("website_note")
    st.warning(
        "A host didn't approve this for the website"
        + (f": {note}" if note else ".")
        + " Fix it and tick the box again to ask for another review."
    )


# --- Links -----------------------------------------------------------------

st.divider()
st.subheader("Links")
st.caption(
    "Just the handle, not the whole address — @ and the full URL both work, "
    "we take the handle out. Leave a box empty to show nothing."
)

with st.form("profile_links"):
    instagram = st.text_input("Instagram", value=me.get("instagram") or "", placeholder="yourhandle")
    linkedin = st.text_input("LinkedIn", value=me.get("linkedin") or "", placeholder="your-name-1234")
    github = st.text_input("GitHub", value=me.get("github") or "", placeholder="yourusername")
    if st.form_submit_button("Save links", icon=":material/link:", type="primary"):
        with safe_write("save your links"):
            values = {
                "instagram": _clean_handle(instagram, "instagram"),
                "linkedin": _clean_handle(linkedin, "linkedin"),
                "github": _clean_handle(github, "github"),
            }
            # Handles are part of the same reviewed bundle as the photo —
            # changing them after a host already approved shouldn't let
            # new, unreviewed text reach the site silently, so an edit
            # made while already public sends it back for another look.
            if me.get("photo_public"):
                values["website_status"] = "pending"
            client.table("users").update(values).eq("user_id", user_id).execute()
            invalidate_cache()
            shown = [k for k, v in values.items() if v]
            st.success(
                "Links saved — " + ", ".join(shown) + "."
                if shown
                else "Links cleared."
            )
            st.rerun()

for kind, handle, url in (
    ("Instagram", me.get("instagram"), "https://instagram.com/{}"),
    ("LinkedIn", me.get("linkedin"), "https://www.linkedin.com/in/{}"),
    ("GitHub", me.get("github"), "https://github.com/{}"),
):
    if handle:
        st.caption(f"{kind}: {url.format(handle)}")
