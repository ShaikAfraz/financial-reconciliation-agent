"""
Autonomous Financial Reconciliation Agent - Streamlit Application
A GenAI-powered financial reconciliation engine with deterministic rules,
Gemini discrepancy adjudication, Ask-Your-Data assistant, and verified audit reports.
"""

import os
import sys
import json
import time
from pathlib import Path
from typing import Dict, Any, Optional

import streamlit as st

# Read secrets from Streamlit Cloud if present
try:
    if "GEMINI_API_KEY" in st.secrets and not os.environ.get("GEMINI_API_KEY"):
        os.environ["GEMINI_API_KEY"] = st.secrets["GEMINI_API_KEY"]
    if "GEMINI_MODEL" in st.secrets and not os.environ.get("GEMINI_MODEL"):
        os.environ["GEMINI_MODEL"] = st.secrets["GEMINI_MODEL"]
except Exception:
    pass

# Ensure local imports work properly
ROOT_DIR = Path(__file__).resolve().parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import data.db as db
import auth.service as auth_service
import core.csv_workflow as csv_workflow
import ai.reconciliation_ai as reconciliation_ai
from core.csv_workflow import format_cents

# Streamlit Page Configuration
st.set_page_config(
    page_title="Financial Reconciliation Agent",
    page_icon="⚖️",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom Styling
st.markdown("""
<style>
    .main-header {
        font-size: 2rem;
        font-weight: 700;
        color: #107c41;
        margin-bottom: 0.2rem;
    }
    .sub-header {
        font-size: 1rem;
        color: #555;
        margin-bottom: 1.5rem;
    }
    .metric-card {
        background-color: #f8fafc;
        border: 1px solid #e2e8f0;
        border-radius: 8px;
        padding: 1rem;
        text-align: center;
    }
    .ai-badge {
        display: inline-block;
        padding: 0.25rem 0.6rem;
        border-radius: 12px;
        font-size: 0.8rem;
        font-weight: 600;
        background-color: #ecfdf5;
        color: #065f46;
        border: 1px solid #a7f3d0;
    }
    .fallback-badge {
        display: inline-block;
        padding: 0.25rem 0.6rem;
        border-radius: 12px;
        font-size: 0.8rem;
        font-weight: 600;
        background-color: #fffbeb;
        color: #92400e;
        border: 1px solid #fde68a;
    }
    .priority-high {
        color: #dc2626;
        font-weight: bold;
    }
    .priority-medium {
        color: #d97706;
        font-weight: bold;
    }
    .priority-low {
        color: #2563eb;
        font-weight: bold;
    }
</style>
""", unsafe_allow_html=True)

# Initialize Database and Session State
@st.cache_resource
def setup_backend():
    db.init_database()
    user = auth_service.get_user_by_username("streamlit_analyst")
    if not user:
        user = auth_service.create_user("streamlit_analyst", "StreamlitPassword12345!", role="analyst")
    return user

user = setup_backend()

if "current_run_id" not in st.session_state:
    st.session_state.current_run_id = None
if "run_results" not in st.session_state:
    st.session_state.run_results = None
if "ai_insights" not in st.session_state:
    st.session_state.ai_insights = None
if "chat_history" not in st.session_state:
    st.session_state.chat_history = []

# Sidebar: System Status & Settings
with st.sidebar:
    st.title("System Controls")
    
    # API Key Configuration
    api_key_env = os.environ.get("GEMINI_API_KEY", "")
    has_key = bool(api_key_env)
    
    st.subheader("Gemini GenAI Status")
    if has_key:
        masked_key = api_key_env[:5] + "..." + api_key_env[-4:] if len(api_key_env) >= 9 else "***"
        st.success(f"Key configured ({masked_key})")
    else:
        st.warning("GEMINI_API_KEY is not set.")
        user_key_input = st.text_input("Enter Gemini API Key (optional):", type="password")
        if user_key_input:
            os.environ["GEMINI_API_KEY"] = user_key_input.strip()
            st.rerun()

    model_name = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash")
    st.caption(f"Active Model: `{model_name}`")
    
    st.divider()
    
    st.subheader("Sample Datasets")
    st.markdown("Use bundled test statements for quick demonstration:")
    sample_bank = ROOT_DIR / "sample_data" / "bank_statement.csv"
    sample_ledger = ROOT_DIR / "sample_data" / "ledger_statement.csv"
    
    col_d1, col_d2 = st.columns(2)
    if sample_bank.exists():
        col_d1.download_button("Bank CSV", sample_bank.read_bytes(), "bank_statement.csv", "text/csv")
    if sample_ledger.exists():
        col_d2.download_button("Ledger CSV", sample_ledger.read_bytes(), "ledger_statement.csv", "text/csv")

    st.divider()
    st.caption("Veritas Treasury · Autonomous Financial Reconciliation Agent")

# Header
st.markdown('<div class="main-header">Autonomous Financial Reconciliation Agent</div>', unsafe_allow_html=True)
st.markdown('<div class="sub-header">Deterministic Rules Engine & Authoritative Integer Cents Calculations with Google Gemini GenAI Discrepancy Investigation</div>', unsafe_allow_html=True)

# Main Navigation Tabs
tab_reconcile, tab_results, tab_insights, tab_assistant, tab_report = st.tabs([
    "1. Ingestion & Reconcile",
    "2. Stored Results & Matches",
    "3. AI Reconciliation Insights",
    "4. Ask-Your-Data Assistant",
    "5. Verified Report"
])

# TAB 1: Ingestion & Reconciliation
with tab_reconcile:
    st.subheader("Create Reconciliation Run")
    
    col_r1, col_r2, col_r3 = st.columns([2, 1, 1])
    run_name = col_r1.text_input("Run Name", value="Monthly Treasury Close - October 2026")
    currency = col_r2.selectbox("Currency", ["USD", "EUR", "GBP", "INR"], index=0)
    period = col_r3.date_input("Period Date", value=None)
    
    st.divider()
    st.subheader("Upload Statements")
    
    col_u1, col_u2 = st.columns(2)
    with col_u1:
        st.markdown("**Bank Statement CSV**")
        use_sample_bank = st.checkbox("Use sample bank statement", value=True)
        uploaded_bank = st.file_uploader("Upload Bank Statement (CSV)", type=["csv"], key="bank_upload", disabled=use_sample_bank)
    
    with col_u2:
        st.markdown("**Ledger Statement CSV**")
        use_sample_ledger = st.checkbox("Use sample ledger statement", value=True)
        uploaded_ledger = st.file_uploader("Upload General Ledger (CSV)", type=["csv"], key="ledger_upload", disabled=use_sample_ledger)
        
    bank_bytes = sample_bank.read_bytes() if (use_sample_bank and sample_bank.exists()) else (uploaded_bank.getvalue() if uploaded_bank else None)
    ledger_bytes = sample_ledger.read_bytes() if (use_sample_ledger and sample_ledger.exists()) else (uploaded_ledger.getvalue() if uploaded_ledger else None)
    
    if st.button("🚀 Process Imports and Run Reconciliation", type="primary"):
        if not bank_bytes or not ledger_bytes:
            st.error("Please provide both bank and general ledger CSV statements.")
        else:
            with st.spinner("Processing CSV uploads and running deterministic reconciliation..."):
                try:
                    # 1. Create run
                    run = csv_workflow.create_workflow_run(user["id"], run_name, currency)
                    run_id = run["id"]
                    st.session_state.current_run_id = run_id
                    
                    # 2. Ingest Bank CSV
                    b_batch = csv_workflow.ingest_raw_csv_upload(run_id, "bank", "bank_statement.csv", bank_bytes, user["id"])
                    b_mapping = {
                        "date_column": "Date",
                        "amount_mode": "signed",
                        "amount_column": "Amount",
                        "description_column": "Description",
                        "reference_column": "Reference",
                        "sign_convention": "standard"
                    }
                    csv_workflow.map_and_process_import_batch(b_batch["batch_id"], b_mapping, user["id"])
                    csv_workflow.confirm_import_batch(b_batch["batch_id"], user["id"], "analyst")
                    
                    # 3. Ingest Ledger CSV
                    l_batch = csv_workflow.ingest_raw_csv_upload(run_id, "ledger", "ledger_statement.csv", ledger_bytes, user["id"])
                    l_mapping = dict(b_mapping, ledger_perspective="bank")
                    csv_workflow.map_and_process_import_batch(l_batch["batch_id"], l_mapping, user["id"])
                    csv_workflow.confirm_import_batch(l_batch["batch_id"], user["id"], "analyst")
                    
                    # 4. Run authoritative deterministic reconciliation
                    csv_workflow.run_reconciliation_for_run(run_id, user["id"])
                    
                    # 5. Fetch stored results
                    results = csv_workflow.get_run_results(run_id)
                    st.session_state.run_results = results
                    st.session_state.ai_insights = None
                    st.session_state.chat_history = []
                    
                    st.success(f"Reconciliation completed successfully for Run ID: `{run_id}`")
                    st.rerun()
                except Exception as e:
                    st.error(f"Reconciliation failed: {e}")

# Display Summary Metrics if Results are Available
results = st.session_state.run_results
if results:
    summary = results.get("summary", {})
    st.divider()
    m1, m2, m3, m4, m5, m6 = st.columns(6)
    m1.metric("Bank Records", summary.get("bank_records", 0))
    m2.metric("Ledger Records", summary.get("ledger_records", 0))
    m3.metric("Matched Pairs", summary.get("matched_items", 0))
    m4.metric("Unmatched Bank", summary.get("unmatched_bank_records", 0))
    m5.metric("Unmatched Ledger", summary.get("unmatched_ledger_records", 0))
    m6.metric("Review Required", summary.get("review_required_items", 0))
    
    diff_cents = summary.get("net_activity_difference_cents", 0)
    diff_str = format_cents(diff_cents, results.get("currency", "USD"))
    st.info(f"**Net Activity Delta:** {diff_str} · Authoritative integer calculations: Bank Net = {format_cents(summary.get('bank_net_activity_cents', 0))} | Ledger Net = {format_cents(summary.get('ledger_net_activity_cents', 0))}")

# TAB 2: Stored Results & Matches
with tab_results:
    if not results:
        st.info("No active reconciliation results. Upload statements and run reconciliation in Tab 1.")
    else:
        st.subheader("Deterministic Match Pairs & Exceptions")
        res_view = st.radio("View", ["Matches", "Exceptions"], horizontal=True)
        
        if res_view == "Matches":
            matches = results.get("matches", [])
            if not matches:
                st.write("No matches stored.")
            else:
                table_data = []
                for m in matches:
                    feats = m.get("features", {})
                    score = m.get("rule_score")
                    table_data.append({
                        "Match ID": m.get("id"),
                        "Method": m.get("match_type"),
                        "Rule Score": f"{float(score)*100:.1f}%" if score is not None else "—",
                        "Amount Delta": f"{feats.get('amount_delta_cents', 0)}¢",
                        "Date Delta": f"{feats.get('date_delta_days', 0)} days",
                        "Status": m.get("status")
                    })
                st.dataframe(table_data, use_container_width=True)
                
        else:
            exceptions = results.get("exceptions", [])
            if not exceptions:
                st.write("No exceptions stored.")
            else:
                table_data = []
                for exc in exceptions:
                    table_data.append({
                        "Record ID": exc.get("record_id"),
                        "Side": exc.get("side"),
                        "Date": exc.get("date"),
                        "Amount": format_cents(exc.get("amount_cents", 0), results.get("currency", "USD")),
                        "Description": exc.get("description"),
                        "Reason": exc.get("reason"),
                        "Status": exc.get("status")
                    })
                st.dataframe(table_data, use_container_width=True)

# TAB 3: AI Reconciliation Insights
with tab_insights:
    if not results:
        st.info("Run reconciliation first to generate AI insights.")
    else:
        st.subheader("AI Discrepancy Investigator & Candidate Proposals")
        st.caption("Bounded analysis of unmatched records and candidate pairs using Google Gemini. Explanations are hypotheses and require human review.")
        
        run_id = st.session_state.current_run_id
        insights = st.session_state.ai_insights
        
        if st.button("✨ Generate AI Insights with Gemini", type="primary") or insights is None:
            with st.spinner("Analyzing discrepancies and candidate matches with Gemini..."):
                try:
                    insights = reconciliation_ai.generate_insights(run_id, user["id"], results)
                    st.session_state.ai_insights = insights
                except Exception as e:
                    st.error(f"Failed to fetch insights: {e}")
        
        if insights:
            is_live_ai = insights.get("status") == "success" and insights.get("provider") == "google_genai"
            if is_live_ai:
                st.markdown(f'<span class="ai-badge">Gemini-Generated · {insights.get("model", "gemini-3.5-flash")} · Validated against Run</span>', unsafe_allow_html=True)
            else:
                st.markdown(f'<span class="fallback-badge">{insights.get("label", "Rule-based fallback (not AI)")}</span>', unsafe_allow_html=True)
            
            st.write(insights.get("summary", ""))
            
            issues = insights.get("issues", [])
            if not issues:
                st.info("No high-risk issues or candidate proposals requiring manual review were flagged.")
            else:
                st.write(f"**Identified Issues ({len(issues)}):**")
                for idx, issue in enumerate(issues, 1):
                    p_class = f"priority-{issue.get('priority', 'medium').lower()}"
                    with st.expander(f"{idx}. {issue.get('issue_type')} — Priority: {issue.get('priority', 'medium').upper()}", expanded=True):
                        st.markdown(f"**Transaction IDs:** `{', '.join(issue.get('transaction_ids', []))}`")
                        
                        st.markdown("**Verified Evidence (Database Grounded):**")
                        for ev in issue.get("observed_evidence", []):
                            st.markdown(f"- `{ev['record_id']}` · **{ev['field']}**: `{ev['value']}`")
                            
                        st.markdown(f"**Hypothesis (Root Cause):** {issue.get('possible_explanation')}")
                        st.markdown(f"**Recommended Action:** {issue.get('recommended_action')}")
                        
                        missing = issue.get("missing_information", [])
                        if missing:
                            st.markdown(f"**Missing Information:** {'; '.join(missing)}")
                            
                        st.caption("⚠️ Human review required. AI output does not authorize financial postings or automatic match confirmation.")

# TAB 4: Ask-Your-Data Assistant
with tab_assistant:
    if not results:
        st.info("Run reconciliation first to interact with the assistant.")
    else:
        st.subheader("Ask-Your-Data Assistant")
        st.caption("Ask natural-language questions about this reconciliation run. Answers are strictly grounded in retrieved database records.")
        
        # Display existing chat history
        for msg in st.session_state.chat_history:
            with st.chat_message(msg["role"]):
                st.markdown(msg["content"])
                if msg.get("records"):
                    st.caption(f"Cited records: {', '.join(msg['records'])}")
        
        prompt = st.chat_input("e.g., Which bank records remain unmatched?")
        if prompt:
            st.session_state.chat_history.append({"role": "user", "content": prompt})
            with st.chat_message("user"):
                st.markdown(prompt)
                
            with st.chat_message("assistant"):
                with st.spinner("Reviewing stored run records..."):
                    run_id = st.session_state.current_run_id
                    ans_data = reconciliation_ai.generate_assistant_answer(run_id, user["id"], prompt, results)
                    answer_text = ans_data.get("answer", "")
                    cited_ids = ans_data.get("transaction_ids", [])
                    
                    st.markdown(answer_text)
                    if cited_ids:
                        st.caption(f"Cited records: {', '.join(cited_ids)}")
                        
                    st.session_state.chat_history.append({
                        "role": "assistant",
                        "content": answer_text,
                        "records": cited_ids
                    })

# TAB 5: Verified Report
with tab_report:
    if not results:
        st.info("Run reconciliation first to generate the report.")
    else:
        st.subheader("Reconciliation Audit Report")
        st.caption("Authoritative summary of verified reconciliation results, arithmetic proofs, and validated AI investigation hypotheses.")
        
        run_id = st.session_state.current_run_id
        insights = st.session_state.ai_insights or reconciliation_ai.rule_based_insights(run_id, results)
        report_md = reconciliation_ai.render_report(run_id, results, insights)
        
        st.download_button(
            label="📥 Download Full Reconciliation Report (.md)",
            data=report_md.encode("utf-8"),
            file_name=f"reconciliation-report-{run_id}.md",
            mime="text/markdown",
            type="primary"
        )
        
        with st.expander("Preview Report", expanded=True):
            st.markdown(report_md)
