import streamlit as st
import json
import pandas as pd
import streamlit.components.v1 as components
from logic import process_analysis, generate_html_report

st.set_page_config(page_title="Bank Statement Analyzer", layout="wide")

st.title("🏦 Universal Bank Statement Analyzer")
st.markdown("Upload standard JSON bank statement files to analyze turnover, detect related party transactions, and check integrity.")

# --- SIDEBAR CONFIGURATION ---
with st.sidebar:
    st.header("1. Company Details")
    company_name = st.text_input("Company Name", value="MY COMPANY SDN BHD")
    company_aliases = st.text_area("Company Aliases (comma separated)", value="MY COMPANY, MY CO").split(',')
    
    st.header("2. Related Parties")
    st.info("Add directors, sister companies, etc. to exclude their transactions.")
    
    if 'related_parties' not in st.session_state:
        st.session_state.related_parties = []

    with st.form("add_rp"):
        rp_name = st.text_input("Party Name")
        rp_rel = st.selectbox("Relationship", ["Director", "Sister Company", "Shareholder", "Subsidiary"])
        submitted = st.form_submit_button("Add Party")
        if submitted and rp_name:
            st.session_state.related_parties.append({'name': rp_name, 'relationship': rp_rel})
            st.success(f"Added {rp_name}")

    if st.session_state.related_parties:
        st.write("### Current List:")
        rp_df = pd.DataFrame(st.session_state.related_parties)
        st.dataframe(rp_df, hide_index=True)
        if st.button("Clear List"):
            st.session_state.related_parties = []
            st.rerun()

# --- MAIN AREA ---

st.header("3. Upload Files")
uploaded_files = st.file_uploader("Upload JSON Files", type=['json'], accept_multiple_files=True)

if uploaded_files:
    st.success(f"{len(uploaded_files)} files uploaded.")
    
    # Dynamic Account Mapping
    st.subheader("4. Account Mapping")
    account_info = {}
    uploaded_data_content = {}
    
    cols = st.columns(2)
    
    for i, file in enumerate(uploaded_files):
        # Read file content once
        content = json.load(file)
        file_key = f"ACC_{i+1}"
        uploaded_data_content[file_key] = content
        
        with cols[i % 2]:
            st.markdown(f"**File:** `{file.name}`")
            bank_name = st.text_input(f"Bank Name for {file.name}", value="CIMB", key=f"bank_{i}")
            acc_num = st.text_input(f"Account No for {file.name}", value="1234567890", key=f"num_{i}")
            
            account_info[file_key] = {
                'bank_name': bank_name,
                'account_number': acc_num,
                'classification': 'PRIMARY'
            }
            st.divider()

    # --- ANALYZE BUTTON ---
    if st.button("🚀 Run Analysis", type="primary"):
        with st.spinner("Crunching numbers..."):
            try:
                # 1. Run Logic
                results = process_analysis(
                    company_name=company_name,
                    company_keywords=[k.strip() for k in company_aliases],
                    related_parties=st.session_state.related_parties,
                    account_info=account_info,
                    uploaded_data=uploaded_data_content
                )
                
                # 2. Generate HTML Report
                html_report = generate_html_report(results, template_path="template.html")
                
                # 3. Display Results
                st.divider()
                st.subheader("📊 Analysis Report")
                
                # Metrics
                m1, m2, m3 = st.columns(3)
                m1.metric("Total Credits", f"RM {results['report_info']['total_credits']:,.2f}")
                m2.metric("Accounts Analyzed", results['report_info']['total_accounts'])
                m3.metric("Integrity Score", f"{results['integrity_score']['score']}%")
                
                # Download Button
                st.download_button(
                    label="📄 Download Interactive HTML Dashboard",
                    data=html_report,
                    file_name="bank_analysis_dashboard.html",
                    mime="text/html"
                )
                
                # Preview
                st.write("### Report Preview")
                components.html(html_report, height=800, scrolling=True)
                
            except Exception as e:
                st.error(f"Analysis failed: {str(e)}")
                st.exception(e)

else:
    st.info("Please upload files to begin.")
