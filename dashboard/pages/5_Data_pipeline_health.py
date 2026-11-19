"""Is the automatic data collection working? Runs, data checks and corrections (RQ1, RQ2)."""

from __future__ import annotations

import plotly.express as px
import streamlit as st

import data
import ui

ui.page("Data pipeline health", "🩺", "Whether the automatic weekly collection is working, and what the "
        "data checks found.")
runs = data.runs(100)
if runs.empty:
    st.info("No pipeline runs recorded yet.")
    st.stop()

scheduled = runs[runs["run_type"] != "backfill"]
finished = scheduled[scheduled["status"] != "running"]
ok = (finished["status"] == "success").mean() * 100 if len(finished) else float("nan")
c1, c2, c3, c4 = st.columns(4)
c1.metric("Runs that succeeded", f"{ok:.0f}%" if ok == ok else "–", help="Weekly and manual runs (not the one-off history load).")
c2.metric("Last run", {"success": "✅ Success", "partial": "🟠 Partly", "failed": "❌ Failed", "running": "⏳ Running"}
          .get(runs.iloc[0]["status"], runs.iloc[0]["status"]), help=str(runs.iloc[0]["started_at"]))
c3.metric("Rows held back for review", f"{int(runs['rows_quarantined'].sum()):,}",
          help="Figures that broke a data rule (for example more deaths than cases) and were not loaded.")
c4.metric("Figures NCDC later corrected", f"{int(runs['revisions_detected'].sum()):,}",
          help="Times a later report changed a number that had already been published.")

st.subheader("Recent runs")
r = runs.copy()
r["started_at"] = r["started_at"].astype(str).str[:16]
r = r.rename(columns={"started_at": "Started", "run_type": "Type", "trigger_source": "Started by", "status": "Result",
                      "duration_s": "Seconds", "files_new": "New reports", "rows_passed": "Rows loaded",
                      "rows_warned": "Rows with warnings", "rows_quarantined": "Rows held back",
                      "revisions_detected": "Corrections found", "error_message": "Notes"})
st.dataframe(r[["Started", "Type", "Started by", "Result", "Seconds", "New reports", "Rows loaded",
                "Rows with warnings", "Rows held back", "Corrections found", "Notes"]],
             use_container_width=True, hide_index=True)

left, right = st.columns(2)
with left:
    st.subheader("Reports by status")
    docs = data.documents_by_status()
    docs["processing_status"] = docs["processing_status"].map({
        "parsed": "Read successfully", "manual_queue": "Needs a person to check", "ignored": "Outside 2021–2026",
        "parse_failed": "Failed to read", "new": "Waiting to be read"}).fillna(docs["processing_status"])
    st.dataframe(docs.rename(columns={"processing_status": "Status", "n": "Reports"}),
                 use_container_width=True, hide_index=True)
with right:
    st.subheader("Data warnings")
    flags = data.flag_counts()
    if not flags.empty:
        flags["flag_type"] = flags["flag_type"].map(lambda f: ui.FLAG_LABELS.get(f, f))
        st.plotly_chart(px.bar(flags, x="n", y="flag_type", orientation="h", height=300,
                               labels={"n": "Rows", "flag_type": ""}), use_container_width=True)
    else:
        st.caption("No warnings.")

st.subheader("Rows held back for review")
q = data.quarantine()
if q.empty:
    st.success("Nothing is waiting for review.")
else:
    q["rule_code"] = q["rule_code"].map(lambda c: ui.RULE_LABELS.get(c, c) if c else "Report could not be read")
    q = q.rename(columns={"quarantined_at": "When", "review_status": "Review", "scope": "What", "rule_code": "Problem",
                          "failure_reason": "Details", "file_name": "Report file", "source_page": "Page",
                          "payload": "Row as read"})
    st.dataframe(q, use_container_width=True, hide_index=True)

st.subheader("Figures NCDC corrected later")
rev = data.revisions()
if rev.empty:
    st.info("No corrections detected yet.")
else:
    rev = rev.rename(columns={"detected_at": "Detected", "disease_name": "Disease", "location_name": "Place",
                              "epi_year": "Year", "epi_week": "Week", "field_name": "What changed",
                              "old_value": "Old figure", "new_value": "New figure", "abs_change": "Change",
                              "rel_change": "Change (fraction)", "revision_lag_weeks": "Weeks later",
                              "old_report": "First report", "new_report": "Correcting report"})
    rev["What changed"] = rev["What changed"].map(ui.label)
    st.dataframe(ui.rounded(rev, 2), use_container_width=True, hide_index=True)
