# The Discord bot's club FAQ (2026-11-04): hosts write the club's own
# answers to common questions ("how do I borrow a part?", "how do I log a
# result?", club rules) on the Discord Messages page, and the bot answers
# from them instead of guessing. One row per question in club_faq.
#
# Host-only (that page already is). Needs the club_faq migration at the end
# of supabase_schema.sql; until then the section says so.

import streamlit as st

from shared import cached_table, get_client, invalidate_cache, safe_write


def _rows():
    try:
        return sorted(cached_table("club_faq"), key=lambda r: r["faq_id"])
    except Exception:
        return None


def render_bot_faq():
    st.subheader(":material/live_help: Bot FAQ")
    rows = _rows()
    if rows is None:
        st.caption(
            "Needs a one-time database change: run the club_faq statement at the end of "
            "supabase_schema.sql in Supabase's SQL editor."
        )
        return
    st.caption(
        "The bot answers questions about how the club and the dashboard work from these, "
        "instead of guessing. Write them the way you'd answer a member."
    )
    for r in rows:
        with st.expander(r["question"]):
            with st.form(f"faq_edit_{r['faq_id']}", border=False):
                question = st.text_input("Question", value=r["question"])
                answer = st.text_area("Answer", value=r["answer"], height=120)
                save_col, delete_col = st.columns(2)
                save = save_col.form_submit_button("Save", icon=":material/save:", type="primary")
                delete = delete_col.form_submit_button("Delete", icon=":material/delete:")
            if save:
                if not question.strip() or not answer.strip():
                    st.error("A question and an answer are both needed.")
                else:
                    with safe_write("save this FAQ"):
                        get_client().table("club_faq").update({
                            "question": question.strip(), "answer": answer.strip(),
                        }).eq("faq_id", r["faq_id"]).execute()
                        invalidate_cache()
                        st.rerun()
            if delete:
                with safe_write("delete this FAQ"):
                    get_client().table("club_faq").delete().eq("faq_id", r["faq_id"]).execute()
                    invalidate_cache()
                    st.rerun()

    with st.form("faq_add", clear_on_submit=True):
        st.markdown("**Add a question**")
        question = st.text_input("Question", placeholder="How do I borrow a part?")
        answer = st.text_area("Answer", height=120)
        if st.form_submit_button("Add", icon=":material/add:"):
            if not question.strip() or not answer.strip():
                st.error("A question and an answer are both needed.")
            else:
                with safe_write("add this FAQ"):
                    get_client().table("club_faq").insert({
                        "question": question.strip(), "answer": answer.strip(),
                    }).execute()
                    invalidate_cache()
                    st.rerun()
