"""Pipeline health: run history, success rate, quarantine, data-quality flags, revisions (RQ1, RQ2)."""

from __future__ import annotations

import plotly.express as px
import streamlit as st

import data
import ui

ui.page("Pipeline health", "🩺")
runs = data.runs(100)
if runs.empty:
    st.info("No pipeline runs recorded yet.")
    st.stop()

scheduled = runs[runs["run_type"] != "backfill"]
ok = (scheduled["status"] == "success").mean() * 100 if len(scheduled) else float("nan")
c1, c2, c3, c4 = st.columns(4)
c1.metric("Run success rate", f"{ok:.0f}%" if ok == ok else "–", help="Share of non-backfill runs that finished 'success'.")
c2.metric("Last run", runs.iloc[0]["status"], help=str(runs.iloc[0]["started_at"]))
c3.metric("Rows quarantined (all runs)", f"{int(runs['rows_quarantined'].sum()):,}")
c4.metric("Revisions detected (all runs)", f"{int(runs['revisions_detected'].sum()):,}")

st.subheader("Recent runs")
st.dataframe(runs.drop(columns=["run_id"]), use_container_width=True, hide_index=True)

left, right = st.columns(2)
with left:
    st.subheader("Documents by status")
    st.dataframe(data.documents_by_status(), use_container_width=True, hide_index=True)
with right:
    st.subheader("Data-quality warnings")
    flags = data.flag_counts()
    if not flags.empty:
        st.plotly_chart(px.bar(flags, x="n", y="flag_type", orientation="h", height=300), use_container_width=True)

st.subheader("Quarantine (rows held back for review)")
q = data.quarantine()
if q.empty:
    st.success("Nothing in quarantine.")
else:
    st.dataframe(q, use_container_width=True, hide_index=True)

st.subheader("Revision history (NCDC changing previously published figures)")
rev = data.revisions()
if rev.empty:
    st.info("No revisions detected yet.")
else:
    st.dataframe(ui.rounded(rev, 3), use_container_width=True, hide_index=True)
    st.plotly_chart(px.histogram(rev, x="revision_lag_weeks", nbins=20, title="Weeks between first publication and revision"),
                    use_container_width=True)
