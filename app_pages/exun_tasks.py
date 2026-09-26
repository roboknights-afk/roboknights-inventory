# Exun task delegation: a host hands out work items to volunteers signed
# up for the Exun event ("Exun volunteers" — see is_exun_volunteer below),
# each with a host-set due date. A volunteer sees what they've been given,
# submits their work (a file or a link) or just marks it done, and can
# flag an issue or ask for help on any task assigned to them. Reachable by
# hosts (full management) and by anyone flagged as an Exun volunteer (their
# own tasks only) — gated in app.py's nav the same two-layer way every
# other restricted page here is (nav list + a re-check on this page).

from datetime import date, datetime, timezone

import streamlit as st

from shared import (
    cached_table, get_client, get_storage_client, invalidate_cache, notify_exun_task_assigned,
    notify_exun_task_update, render_file_open_and_download, render_rich_html_editor, safe_write,
    sanitize_rich_html, today_ist, wrap_rich_html_for_storage,
)

client = get_client()
storage = get_storage_client()
is_host = st.session_state.is_host
is_exun_volunteer = st.session_state.get("is_exun_volunteer", False)
is_read_only = st.session_state.is_read_only
current_user_id = st.session_state.current_user_id
current_user_name = st.session_state.current_user_name
user_name_by_id = st.session_state.user_name_by_id

if not is_host and not is_exun_volunteer:
    st.error("This page is for hosts and Exun volunteers only.")
    st.stop()

BUCKET = "exun-task-submissions"
ALLOWED_EXTENSIONS = {
    "pdf", "doc", "docx", "ppt", "pptx", "xls", "xlsx", "zip",
    "png", "jpg", "jpeg", "txt", "csv", "mp4", "mov",
}
MAX_MB = 25

STATUS_LABELS = {"assigned": "Assigned", "submitted": "Submitted", "done": "Done"}
STATUS_BADGE_COLOR = {"assigned": "blue", "submitted": "orange", "done": "green"}

st.title(":material/checklist: Exun Tasks")
st.caption(
    "Work delegated to Exun volunteers — what's assigned, what's due, and "
    "what's been submitted."
)

if "task_message" not in st.session_state:
    st.session_state.task_message = None
if "editing_task_id" not in st.session_state:
    st.session_state.editing_task_id = None
if "flagging_task_id" not in st.session_state:
    st.session_state.flagging_task_id = None
if "flagging_kind" not in st.session_state:
    st.session_state.flagging_kind = None
if "show_assign_task" not in st.session_state:
    st.session_state.show_assign_task = False

all_users = sorted(cached_table("users"), key=lambda u: u["name"])
volunteers = [u for u in all_users if u.get("is_exun_volunteer")]
tasks = sorted(cached_table("exun_tasks"), key=lambda t: (t.get("due_date") or "9999-99-99", t["task_id"]))
all_assignees = cached_table("exun_task_assignees")
all_flags = cached_table("exun_task_flags")
assignees_by_task = {}
for row in all_assignees:
    assignees_by_task.setdefault(row["task_id"], []).append(row)
flags_by_task = {}
for f in all_flags:
    flags_by_task.setdefault(f["task_id"], []).append(f)

today_iso = today_ist().isoformat()


# --- Host: manage the Exun volunteer list -----------------------------------
# This IS the record of who's volunteering for Exun this year — the host
# supplies the list, kept here rather than anywhere else. Same
# multiselect-diff pattern meetings.py already uses for invitees: only
# what actually changed gets written.
def render_volunteer_manager():
    with st.expander(":material/groups: Manage Exun volunteers", expanded=not volunteers):
        st.caption(
            "Who's actually volunteering for Exun — only people on this list can "
            "be assigned a task below, and only they (plus hosts) see this page."
        )
        selected = st.multiselect(
            "Exun volunteers",
            options=[u["user_id"] for u in all_users],
            default=[u["user_id"] for u in volunteers],
            format_func=lambda uid: user_name_by_id.get(uid, "Unknown"),
            key="exun_volunteer_picker",
        )
        if st.button("Save volunteer list", icon=":material/save:", key="save_exun_volunteers"):
            was_volunteer = {u["user_id"] for u in volunteers}
            now_volunteer = set(selected)
            with safe_write("update the Exun volunteer list"):
                for uid in now_volunteer - was_volunteer:
                    client.table("users").update({"is_exun_volunteer": True}).eq(
                        "user_id", uid
                    ).execute()
                for uid in was_volunteer - now_volunteer:
                    client.table("users").update({"is_exun_volunteer": False}).eq(
                        "user_id", uid
                    ).execute()
                invalidate_cache()
            st.session_state.task_message = ("success", "Volunteer list saved.")
            st.rerun()


# --- Host: assign a task -----------------------------------------------------
@st.dialog("Assign a task", on_dismiss=lambda: st.session_state.update(show_assign_task=False))
def render_assign_task():
    if not volunteers:
        st.warning("Add at least one Exun volunteer above before assigning a task.")
        return
    title = st.text_input("Title", key="new_task_title")
    st.caption("Description")
    render_rich_html_editor("new_task_description_rich", height=120, placeholder="Description...")
    due_date = st.date_input("Due date", key="new_task_due_date", value=None)
    assignees = st.multiselect(
        "Assign to",
        options=[u["user_id"] for u in volunteers],
        format_func=lambda uid: user_name_by_id.get(uid, "Unknown"),
        key="new_task_assignees",
    )
    if st.button("Assign", icon=":material/check:", type="primary", key="confirm_assign_task"):
        if not title.strip():
            st.error("Title is required.")
        elif not assignees:
            st.error("Pick at least one volunteer.")
        else:
            with safe_write("assign this task"):
                created = client.table("exun_tasks").insert({
                    "title": title.strip(),
                    "description": wrap_rich_html_for_storage("new_task_description_rich") or None,
                    "due_date": due_date.isoformat() if due_date else None,
                    "created_by": current_user_id,
                }).execute()
                new_task = created.data[0]
                client.table("exun_task_assignees").upsert(
                    [{"task_id": new_task["task_id"], "user_id": uid} for uid in assignees],
                    on_conflict="task_id,user_id", ignore_duplicates=True,
                ).execute()
                invalidate_cache()
            notify_exun_task_assigned(new_task, assignees)
            st.session_state.task_message = (
                "success", f"Assigned \"{title.strip()}\" to {len(assignees)} volunteer(s)."
            )
            for k in (
                "new_task_title", "new_task_description_rich", "new_task_description_rich_size",
                "new_task_assignees",
            ):
                st.session_state.pop(k, None)
            st.session_state.show_assign_task = False
            st.rerun()


# --- Report an issue / ask for help (either role, per task) -----------------
@st.dialog("Report something", on_dismiss=lambda: st.session_state.update(
    flagging_task_id=None, flagging_kind=None,
))
def render_flag_dialog():
    task = next((t for t in tasks if t["task_id"] == st.session_state.flagging_task_id), None)
    kind = st.session_state.flagging_kind
    if task is None or kind is None:
        st.session_state.flagging_task_id = None
        st.session_state.flagging_kind = None
        st.rerun()
        return
    st.caption(f"For: **{task['title']}**")
    label = "What's the issue?" if kind == "issue" else "What do you need help with?"
    body = st.text_area(label, key="flag_body")
    if st.button("Send", icon=":material/send:", type="primary", key="confirm_flag"):
        if not body.strip():
            st.error("Description can't be empty.")
        else:
            with safe_write("send this"):
                client.table("exun_task_flags").insert({
                    "task_id": task["task_id"], "user_id": current_user_id,
                    "kind": kind, "body": body.strip(),
                }).execute()
                invalidate_cache()
            verb = "reported an issue on" if kind == "issue" else "asked for help on"
            notify_exun_task_update(
                f":triangular_flag_on_post: **{current_user_name} {verb} \"{task['title']}\"**\n"
                f"{body.strip()}",
                f"Exun task {'issue' if kind == 'issue' else 'help request'}: {task['title']}",
            )
            st.session_state.task_message = ("success", "Sent — a host will follow up.")
            st.session_state.pop("flag_body", None)
            st.session_state.flagging_task_id = None
            st.session_state.flagging_kind = None
            st.rerun()


if is_host:
    render_volunteer_manager()
    if st.button("Assign a task", icon=":material/add_box:", type="primary", key="open_assign_task"):
        st.session_state.show_assign_task = True
    if st.session_state.show_assign_task:
        render_assign_task()

if st.session_state.flagging_task_id:
    render_flag_dialog()

if st.session_state.task_message:
    kind, text = st.session_state.task_message
    if kind == "success":
        st.toast(text, icon=":material/check_circle:")
    else:
        st.error(text)
    st.session_state.task_message = None


def _open_and_download(assignee_row, key_suffix):
    render_file_open_and_download(
        storage, BUCKET, assignee_row.get("submission_file_path"),
        assignee_row.get("submission_file_name"), key_suffix,
    )


def render_flags(task_id, allow_resolve):
    open_flags = [f for f in flags_by_task.get(task_id, []) if f["status"] == "open"]
    if not open_flags:
        return
    for f in open_flags:
        kind_label = "Issue" if f["kind"] == "issue" else "Help needed"
        icon = ":material/report:" if f["kind"] == "issue" else ":material/support_agent:"
        st.warning(f"{icon} **{kind_label}** from {user_name_by_id.get(f['user_id'], 'Unknown')}: {f['body']}")
        if allow_resolve:
            if st.button(
                "Mark resolved", key=f"resolve_flag_{f['flag_id']}", icon=":material/check_circle:"
            ):
                with safe_write("resolve this"):
                    client.table("exun_task_flags").update({
                        "status": "resolved",
                        "resolved_at": datetime.now(timezone.utc).isoformat(),
                    }).eq("flag_id", f["flag_id"]).execute()
                    invalidate_cache()
                st.session_state.task_message = ("success", "Marked resolved.")
                st.rerun()


# --- Host: every task, with every assignee's progress -----------------------
def render_host_task_card(t):
    editing_this = st.session_state.editing_task_id == t["task_id"]
    assignees = assignees_by_task.get(t["task_id"], [])
    overdue = bool(t.get("due_date")) and t["due_date"] < today_iso and any(
        a["status"] != "done" for a in assignees
    )

    with st.container(border=True, key=f"rkcard_exuntask_{t['task_id']}"):
        if editing_this:
            edit_title = st.text_input("Title", value=t["title"], key=f"edit_task_title_{t['task_id']}")
            st.caption("Description")
            render_rich_html_editor(
                f"edit_task_desc_rich_{t['task_id']}", height=120, initial_html=t.get("description"),
            )
            edit_due = st.date_input(
                "Due date",
                value=date.fromisoformat(t["due_date"]) if t.get("due_date") else None,
                key=f"edit_task_due_{t['task_id']}",
            )
            edit_assignees = st.multiselect(
                "Assigned to",
                options=[u["user_id"] for u in volunteers],
                default=[a["user_id"] for a in assignees],
                format_func=lambda uid: user_name_by_id.get(uid, "Unknown"),
                key=f"edit_task_assignees_{t['task_id']}",
            )
            save_col, cancel_col = st.columns(2)
            if save_col.button(
                "Save changes", key=f"save_task_{t['task_id']}", icon=":material/check:", type="primary"
            ):
                edit_description_html = wrap_rich_html_for_storage(f"edit_task_desc_rich_{t['task_id']}")
                if not edit_title.strip():
                    st.session_state.task_message = ("error", "Title is required.")
                else:
                    with safe_write(f"update {edit_title.strip()}"):
                        client.table("exun_tasks").update({
                            "title": edit_title.strip(),
                            "description": edit_description_html or None,
                            "due_date": edit_due.isoformat() if edit_due else None,
                        }).eq("task_id", t["task_id"]).execute()
                        was_assigned = {a["user_id"] for a in assignees}
                        now_assigned = set(edit_assignees)
                        newly_added = now_assigned - was_assigned
                        if newly_added:
                            client.table("exun_task_assignees").upsert(
                                [{"task_id": t["task_id"], "user_id": uid} for uid in newly_added],
                                on_conflict="task_id,user_id", ignore_duplicates=True,
                            ).execute()
                        for uid in was_assigned - now_assigned:
                            client.table("exun_task_assignees").delete().eq(
                                "task_id", t["task_id"]
                            ).eq("user_id", uid).execute()
                        invalidate_cache()
                    if newly_added:
                        notify_exun_task_assigned(
                            {**t, "title": edit_title.strip(), "description": edit_description_html,
                             "due_date": edit_due.isoformat() if edit_due else None},
                            newly_added,
                        )
                    st.session_state.task_message = ("success", f"Updated {edit_title.strip()}.")
                    st.session_state.editing_task_id = None
                st.rerun()
            if cancel_col.button("Cancel", key=f"cancel_task_{t['task_id']}", icon=":material/close:"):
                st.session_state.editing_task_id = None
                st.rerun()
            return

        title_col, badge_col, edit_col, delete_col, notify_col = st.columns(
            [3, 1, 1, 1, 1], vertical_alignment="center"
        )
        title_col.markdown(f"### {t['title']}")
        if overdue:
            badge_col.badge("Overdue", color="red", icon=":material/warning:")
        elif all(a["status"] == "done" for a in assignees) and assignees:
            badge_col.badge("All done", color="green", icon=":material/check_circle:")
        if edit_col.button("Edit", key=f"edit_btn_task_{t['task_id']}", icon=":material/edit:"):
            st.session_state.editing_task_id = t["task_id"]
            st.rerun()
        if delete_col.button("Delete", key=f"delete_task_{t['task_id']}", icon=":material/delete:"):
            with safe_write(f"delete {t['title']}"):
                client.table("exun_tasks").delete().eq("task_id", t["task_id"]).execute()
                invalidate_cache()
            st.session_state.task_message = ("success", f"Deleted {t['title']}.")
            st.rerun()
        if notify_col.button(
            "Notify again", key=f"renotify_task_{t['task_id']}", icon=":material/notifications_active:",
            help="Re-send the assignment email + Discord DM to everyone assigned to this task.",
            disabled=not assignees,
        ):
            notify_exun_task_assigned(t, [a["user_id"] for a in assignees])
            st.session_state.task_message = (
                "success", f"Re-sent the notification to {len(assignees)} volunteer(s)."
            )
            st.rerun()

        if t.get("due_date"):
            st.caption(f":material/event: Due {date.fromisoformat(t['due_date']).strftime('%d %b %Y')}")
        if t.get("description"):
            st.markdown(sanitize_rich_html(t["description"]), unsafe_allow_html=True)

        if not assignees:
            st.caption("Nobody assigned.")
        for a in assignees:
            name_col, done_col = st.columns([4, 1], vertical_alignment="center")
            name_col.markdown(
                f"**{user_name_by_id.get(a['user_id'], 'Unknown')}** — "
                f":{STATUS_BADGE_COLOR[a['status']]}[{STATUS_LABELS[a['status']]}]"
            )
            # Host override, same "self-report then host can correct it"
            # shape the Meetings attendance list already uses — a host
            # shouldn't have to wait on someone else to click their own
            # "Mark as done" for a task that's genuinely finished.
            if a["status"] != "done":
                if done_col.button(
                    "Mark done", key=f"host_mark_done_{t['task_id']}_{a['user_id']}",
                    icon=":material/check_circle:",
                ):
                    with safe_write("mark this as done"):
                        client.table("exun_task_assignees").update({
                            "status": "done",
                            "done_at": datetime.now(timezone.utc).isoformat(),
                        }).eq("task_id", t["task_id"]).eq("user_id", a["user_id"]).execute()
                        invalidate_cache()
                    st.session_state.task_message = ("success", "Marked done.")
                    st.rerun()
            if a.get("submission_link"):
                st.caption(f":material/link: {a['submission_link']}")
            _open_and_download(a, key_suffix=f"{t['task_id']}_{a['user_id']}")

        render_flags(t["task_id"], allow_resolve=True)


# --- Volunteer: my own tasks --------------------------------------------------
def render_my_task_card(t, my_row):
    with st.container(border=True, key=f"rkcard_mytask_{t['task_id']}"):
        header_col, badge_col = st.columns([3, 1], vertical_alignment="center")
        header_col.markdown(f"### {t['title']}")
        badge_col.badge(
            STATUS_LABELS[my_row["status"]], color=STATUS_BADGE_COLOR[my_row["status"]],
            icon=":material/task_alt:",
        )
        if t.get("due_date"):
            overdue = t["due_date"] < today_iso and my_row["status"] != "done"
            due_text = f":material/event: Due {date.fromisoformat(t['due_date']).strftime('%d %b %Y')}"
            st.caption(f":red[{due_text} — overdue]" if overdue else due_text)
        if t.get("description"):
            st.markdown(sanitize_rich_html(t["description"]), unsafe_allow_html=True)

        if is_read_only:
            return

        if my_row["status"] != "done":
            link = st.text_input(
                "Link to your work (optional)", value=my_row.get("submission_link") or "",
                key=f"submit_link_{t['task_id']}", placeholder="https://...",
            )
            upload = st.file_uploader(
                "Or upload a file (optional)", type=list(ALLOWED_EXTENSIONS),
                key=f"submit_file_{t['task_id']}", disabled=storage is None,
                help=f"Up to {MAX_MB} MB.",
            )
            action_col1, action_col2 = st.columns(2)
            if action_col1.button(
                "Submit", key=f"submit_btn_{t['task_id']}", icon=":material/upload:", type="primary"
            ):
                file_path = my_row.get("submission_file_path")
                file_name = my_row.get("submission_file_name")
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
                        st.error("File uploads aren't set up on this server yet — use the link field instead.")
                        st.stop()
                    file_path = f"{t['task_id']}_{current_user_id}.{ext}"
                    file_name = upload.name
                    with safe_write("upload this file"):
                        storage.storage.from_(BUCKET).upload(
                            file_path, raw, {"content-type": upload.type or "application/octet-stream", "upsert": "true"},
                        )
                with safe_write("submit this work"):
                    client.table("exun_task_assignees").update({
                        "submission_link": link.strip() or None,
                        "submission_file_path": file_path,
                        "submission_file_name": file_name,
                        "submitted_at": datetime.now(timezone.utc).isoformat(),
                        "status": "submitted",
                    }).eq("task_id", t["task_id"]).eq("user_id", current_user_id).execute()
                    invalidate_cache()
                notify_exun_task_update(
                    f":inbox_tray: **{current_user_name} submitted work for \"{t['title']}\"**",
                    f"Exun task submitted: {t['title']}",
                )
                st.session_state.task_message = ("success", "Submitted.")
                st.rerun()
            if action_col2.button(
                "Mark as done", key=f"done_btn_{t['task_id']}", icon=":material/check_circle:"
            ):
                with safe_write("mark this as done"):
                    client.table("exun_task_assignees").update({
                        "status": "done",
                        "done_at": datetime.now(timezone.utc).isoformat(),
                    }).eq("task_id", t["task_id"]).eq("user_id", current_user_id).execute()
                    invalidate_cache()
                notify_exun_task_update(
                    f":white_check_mark: **{current_user_name} marked \"{t['title']}\" done**",
                    f"Exun task done: {t['title']}",
                )
                st.session_state.task_message = ("celebrate", "Marked done!")
                st.rerun()
        else:
            if my_row.get("submission_link"):
                st.caption(f":material/link: {my_row['submission_link']}")
            _open_and_download(my_row, key_suffix=f"mine_{t['task_id']}")
            if st.button("Reopen", key=f"reopen_{t['task_id']}", icon=":material/undo:"):
                with safe_write("reopen this task"):
                    client.table("exun_task_assignees").update({
                        "status": "submitted" if my_row.get("submission_link") or my_row.get("submission_file_path") else "assigned",
                        "done_at": None,
                    }).eq("task_id", t["task_id"]).eq("user_id", current_user_id).execute()
                    invalidate_cache()
                st.session_state.task_message = ("success", "Reopened.")
                st.rerun()

        flag_col1, flag_col2 = st.columns(2)
        if flag_col1.button(
            "Report an issue", key=f"flag_issue_{t['task_id']}", icon=":material/report:"
        ):
            st.session_state.flagging_task_id = t["task_id"]
            st.session_state.flagging_kind = "issue"
            st.rerun()
        if flag_col2.button(
            "Need help", key=f"flag_help_{t['task_id']}", icon=":material/support_agent:"
        ):
            st.session_state.flagging_task_id = t["task_id"]
            st.session_state.flagging_kind = "help"
            st.rerun()

        render_flags(t["task_id"], allow_resolve=False)


# --- Layout -------------------------------------------------------------------

if is_host:
    st.divider()
    st.subheader("All tasks")
    if not tasks:
        st.caption("No tasks assigned yet.")
    for t in tasks:
        render_host_task_card(t)

my_rows_by_task = {
    a["task_id"]: a for a in all_assignees if a["user_id"] == current_user_id
}
if is_exun_volunteer:
    st.divider()
    st.subheader("My tasks")
    my_tasks = [t for t in tasks if t["task_id"] in my_rows_by_task]
    if not my_tasks:
        st.caption("No tasks assigned to you yet.")
    for t in my_tasks:
        render_my_task_card(t, my_rows_by_task[t["task_id"]])
