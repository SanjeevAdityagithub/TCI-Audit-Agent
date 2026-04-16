import streamlit as st
import asyncio
import nest_asyncio
from engine.state import AuditState
from engine.detector import PCIDetector
from engine.crawler import audit_url

# 1. Allow nested event loops for Playwright
nest_asyncio.apply()

st.set_page_config(page_title="PCI Audit Agent", layout="wide")

# 2. Cache heavy resources
@st.cache_resource
def init_resources():
    return AuditState(), PCIDetector()

state, detector = init_resources()

# 3. Sidebar - Audit Controls
st.sidebar.title("Audit Controls")
target_url = st.sidebar.text_input("Target URL", "https://example.com")
start_audit = st.sidebar.button("Launch Phase 1: Discovery")

# 4. Main UI - Progress Tracking (Component B)
st.title("🛡️ PCI Discovery & Audit Dashboard")
col1, col2, col3 = st.columns(3)
col1.metric("Depth 0 (Root)", "In Progress" if start_audit else "Idle")
col2.metric("Depth 1 (1st Hop)", "Pending")
col3.metric("Depth 2 (2nd Hop)", "Pending")

if start_audit:
    async def run_streamlit_audit():
        queue = [(target_url, 0)]
        progress_bar = st.progress(0)
        status_text = st.empty()
        
        while queue:
            current_url, depth = queue.pop(0)
            status_text.text(f"Auditing: {current_url} (Depth {depth})")
            
            # Run the crawler engine
            new_links = await audit_url(current_url, depth, state, detector)
            state.mark_visited(current_url, depth)
            
            # Log findings to Streamlit UI
            if state.r.lrange("audit:findings", -1, -1):
                st.warning(f"Payment Vector Detected: {current_url}")
            
            # Handle Depth logic
            if depth < 2:
                for link in new_links:
                    queue.append((link, depth + 1))
        
        st.success("Audit Phase 1 Complete! Enforcing Gate Check...")

    asyncio.run(run_streamlit_audit())
