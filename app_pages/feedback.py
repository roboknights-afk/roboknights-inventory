# The other half of the "Report an issue" button that floats in the
# bottom-left corner of every page (see app.py) — this is where those
# reports actually land. Visible to everyone, not private like Queries, so
# nobody re-reports something already in the list, and so people can see
# what's already been fixed. Only a host can change status or leave a note
# back for the reporter.

from datetime import datetime, timezone

import streamlit as st

from shared import cached_table, format_ist, format_relative, get_client, invalidate_cache, safe_write, send_email

client = get_client()
is_host = st.session_state.is_host
user_name_by_id = st.session_state.user_name_by_id
user_email_by_id = st.session_state.user_email_by_id

STATUS_LABELS = {"open": "Open", "in_progress": "In progress", "fixed": "Fixed", "wont_fix": "Won't fix"}
STATUS_COLORS = {"open": "orange", "in_progress": "blue", "fixed": "green", "wont_fix": "grey"}

st.title(":material/rate_review: Feedback")
st.caption(
    "Everything reported through the \"Report an issue\" button in the "
    "corner of any page shows up here. Every one that gets fixed makes "
    "the app a little more reliable for everyone else too."
)

all_feedback = cached_table("feedback")
open_count = sum(1 for f in all_feedback if f["status"] == "open")
if open_count:
    st.caption(f":material/priority_high: {open_count} open report(s) waiting on a fix.")

status_filter = st.segmented_control(
    "Filter",
    ["Open", "In progress", "Fixed", "Won't fix", "All"],
    default="Open",
    key="feedback_status_filter",
    label_visibility="collapsed",
) or "Open"  # deselecting the pill returns None — treat that as "Open"

label_to_status = {v: k for k, v in STATUS_LABELS.items()}
if status_filter == "All":
    visible_feedback = all_feedback
else:
    visible_feedback = [f for f in all_feedback if f["status"] == label_to_status[status_filter]]
visible_feedback = sorted(visible_feedback, key=lambda f: f["created_at"], reverse=True)

if not visible_feedback:
    st.caption("Nothing here yet.")
else:
    for f in visible_feedback:
        reporter_name = user_name_by_id.get(f["user_id"], "Unknown")
        with st.container(border=True, key=f"rkcard_fb_{f['feedback_id']}"):
            badge_col, date_col = st.columns([1, 3], vertical_alignment="center")
            badge_col.badge(STATUS_LABELS[f["status"]], color=STATUS_COLORS[f["status"]])
            date_col.caption(
                f":material/schedule: {reporter_name} • {format_relative(f['created_at'])}",
                help=format_ist(f["created_at"]),
            )

            st.write(f["body"])

            if f.get("host_notes"):
                st.info(f":material/shield_person: **Host note:** {f['host_notes']}")

            if is_host:
                with st.expander("Manage"):
                    new_status = st.selectbox(
                        "Status", list(STATUS_LABELS.keys()), format_func=lambda s: STATUS_LABELS[s],
                        index=list(STATUS_LABELS.keys()).index(f["status"]), key=f"fb_status_{f['feedback_id']}",
                    )
                    new_notes = st.text_area(
                        "Note for the reporter (optional)", value=f.get("host_notes") or "",
                        key=f"fb_notes_{f['feedback_id']}",
                    )
                    save_col, delete_col = st.columns([1, 1])
                    if save_col.button(
                        "Save", key=f"fb_save_{f['feedback_id']}", icon=":material/check:", type="primary",
                    ):
                        with safe_write("update this report"):
                            was_fixed = f["status"] == "fixed"
                            update = {"status": new_status, "host_notes": new_notes.strip() or None}
                            if new_status == "fixed" and not was_fixed:
                                update["resolved_at"] = datetime.now(timezone.utc).isoformat()
                            client.table("feedback").update(update).eq("feedback_id", f["feedback_id"]).execute()
                            invalidate_cache()

                            # Closes the loop for the reporter, so people
                            # keep reporting instead of assuming nothing
                            # ever happens to what they send in. Only fires
                            # on the actual open->fixed transition, not on
                            # every save, so re-editing an already-fixed
                            # note doesn't re-email them.
                            if new_status == "fixed" and not was_fixed:
                                reporter_email = user_email_by_id.get(f["user_id"])
                                if reporter_email:
                                    send_email(
                                        reporter_email,
                                        "Your report was fixed",
                                        f"Good news — the issue you reported has been fixed"
                                        f"{': ' + new_notes.strip() if new_notes.strip() else '.'}\n\n"
                                        f"Thanks for reporting it — every report like this makes the "
                                        f"app more reliable.",
                                    )
                        st.toast("Updated.", icon=":material/check_circle:")
                        st.rerun()
                    if delete_col.button(
                        "Delete", key=f"fb_delete_{f['feedback_id']}", icon=":material/delete:",
                    ):
                        with safe_write("delete this report"):
                            client.table("feedback").delete().eq("feedback_id", f["feedback_id"]).execute()
                            invalidate_cache()
                        st.toast("Deleted.", icon=":material/delete:")
                        st.rerun()
