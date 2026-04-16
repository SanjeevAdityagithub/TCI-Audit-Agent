import streamlit as st
import asyncio
import nest_asyncio
import redis
import json
import os
import subprocess
from playwright.async_api import async_playwright
from playwright_stealth import Stealth 
from langchain_openai import ChatOpenAI
from dotenv import load_dotenv

# --- 1. CLOUD ENVIRONMENT SELF-HEALING ---
# This block ensures Chromium and its Linux dependencies are ready on the Streamlit server
def ensure_playwright_installed():
    # Force check for the playwright folder
    if not os.path.exists("/home/appuser/.cache/ms-playwright"):
        try:
            st.info("Initializing browser environment. This may take a minute...")
            # We add 'install-deps' to handle system-level requirements automatically
            subprocess.run(["python", "-m", "playwright", "install", "chromium"], check=True)
            subprocess.run(["python", "-m", "playwright", "install-deps"], check=True)
        except Exception as e:
            st.error(f"Playwright installation failed: {e}")

ensure_playwright_installed()
nest_asyncio.apply()
load_dotenv()

# --- 2. RESOURCE INITIALIZATION (Component B) ---
st.set_page_config(page_title="PCI Audit Agent", layout="wide", page_icon="🛡️")

class AuditManager:
    def __init__(self):
        # Configuration pulled from Streamlit Cloud Secrets (TOML)
        try:
            self.r = redis.Redis(
                host=st.secrets.get("REDIS_HOST", "localhost"),
                port=int(st.secrets.get("REDIS_PORT", 6379)),
                password=st.secrets.get("REDIS_PASSWORD", None),
                decode_responses=True
            )
            self.llm = ChatOpenAI(
                model="gpt-4o", 
                api_key=st.secrets.get("OPENAI_API_KEY")
            )
        except Exception as e:
            st.error(f"Initialization Error: {e}. Check your Streamlit Secrets.")

    def is_visited(self, url):
        return self.r.sismember("audit:visited", url)

    async def detect_payment_vector(self, html):
        # Component A: Intelligent Reasoning for Payment Entry Points
        prompt = (
            "Analyze the following HTML. Identify if it contains credit card fields, "
            "payment iframes (Stripe/PayPal), or checkout forms. "
            "Reply 'MATCH' or 'SAFE' only.\n\n" + html[:2000]
        )
        response = await self.llm.ainvoke(prompt)
        return "MATCH" in response.content.upper()

    def log_finding(self, url, depth, vector_type):
        finding = json.dumps({"url": url, "depth": depth, "vector": vector_type})
        self.r.rpush("audit:findings", finding)

# --- 3. CRAWLER ENGINE (Component A & C) ---
async def run_pci_audit(target_url, manager):
    async with async_playwright() as p:
        # CRITICAL FIX: Added args to resolve TargetClosedError and Sandbox restrictions
        browser = await p.chromium.launch(
            headless=True, 
            slow_mo=1000, # 1s delay for better connection stability on cloud
            args=[
                "--no-sandbox", 
                "--disable-setuid-sandbox", 
                "--disable-dev-shm-usage", # Prevents memory-related browser crashes
                "--disable-gpu",
                "--single-process" # Minimizes resource overhead
            ]
        )
        context = await browser.new_context(user_agent="PCI-Auditor-Agent/1.0")
        page = await context.new_page()
        
        # Apply updated Stealth API
        stealth = Stealth()
        await stealth.apply_stealth_async(page)

        queue = [(target_url, 0)] # (URL, Depth)
        visited_count = 0
        
        status_box = st.empty()
        findings_area = st.container()

        while queue:
            url, depth = queue.pop(0)
            if manager.is_visited(url) or depth > 2: # Domain Hop Hard Stop
                continue

            status_box.info(f"🔎 **Auditing Depth {depth}:** {url}")
            
            try:
                # 60s timeout to allow for slow dynamic rendering
                await page.goto(url, wait_until="networkidle", timeout=60000)
                
                # Component C: Human-in-the-Loop Detection (CAPTCHA)
                content = await page.content()
                if "captcha" in content.lower() or "verify you are human" in content.lower():
                    st.warning(f"⚠️ CAPTCHA detected at {url}. Intervention needed.")
                
                # Discovery logic
                if await manager.detect_payment_vector(content):
                    manager.log_finding(url, depth, "PCI Vector Flagged")
                    findings_area.error(f"💳 **Payment Entry Point Found:** {url} (Depth {depth})")

                manager.r.sadd("audit:visited", url)
                visited_count += 1

                # Extract links for hops (Component A)
                if depth < 2:
                    hrefs = await page.eval_on_selector_all("a", "elements => elements.map(e => e.href)")
                    for href in hrefs:
                        if href.startswith("http"):
                            is_external = target_url not in href
                            new_depth = depth + 1 if is_external else depth
                            queue.append((href, new_depth))

            except Exception as e:
                st.error(f"Could not audit {url}: {e}")

        await browser.close()
        st.success(f"Audit Cycle Complete. Processed {visited_count} endpoints.")

# --- 4. STREAMLIT UI ---
st.title("🛡️ PCI Payment Page Discovery Agent")
st.caption("Automated Security Engineering Audit Tool - CSE III REC")

with st.sidebar:
    st.header("Audit Configuration")
    root_url = st.text_input("Root Domain URL", "https://example.com")
    run_btn = st.button("🚀 Start Audit")
    
    st.divider()
    if st.button("🗑️ Clear Audit State"):
        m = AuditManager()
        m.r.flushall()
        st.sidebar.success("Audit state cleared.")

if run_btn:
    if not st.secrets.get("OPENAI_API_KEY"):
        st.error("Missing OpenAI API Key! Add it to Streamlit Secrets.")
    else:
        manager = AuditManager()
        asyncio.run(run_pci_audit(root_url, manager))
