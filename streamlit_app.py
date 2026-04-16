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

# --- 1. CLOUD ENVIRONMENT SETUP ---
# Required to install Chromium binaries on Streamlit Cloud servers
def ensure_playwright():
    if not os.path.exists("/home/appuser/.cache/ms-playwright"):
        try:
            subprocess.run(["playwright", "install", "chromium"], check=True)
            subprocess.run(["playwright", "install-deps"], check=True)
        except Exception as e:
            st.error(f"Failed to install browser dependencies: {e}")

ensure_playwright()
nest_asyncio.apply()
load_dotenv()

# --- 2. RESOURCE INITIALIZATION ---
st.set_page_config(page_title="PCI Audit Agent", layout="wide", page_icon="🛡️")

class AuditManager:
    def __init__(self):
        # Use st.secrets for Cloud or local environment variables
        self.r = redis.Redis(
            host=st.secrets.get("REDIS_HOST", "localhost"),
            port=int(st.secrets.get("REDIS_PORT", 6379)),
            password=st.secrets.get("REDIS_PASSWORD", None),
            decode_responses=True
        )
        self.llm = ChatOpenAI(model="gpt-4o", api_key=st.secrets.get("OPENAI_API_KEY"))

    def is_visited(self, url):
        return self.r.sismember("audit:visited", url)

    async def detect_payment_vector(self, html):
        # Intelligent Reasoning for Payment Entry Points
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

# --- 3. CRAWLER ENGINE ---
async def run_pci_audit(target_url, manager):
    async with async_playwright() as p:
        # slow_mo=2000 enforces the 2-5s safety throttling requirement
        browser = await p.chromium.launch(headless=True, slow_mo=2000)
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
            if manager.is_visited(url) or depth > 2:
                continue

            status_box.info(f"🔎 Auditing Depth {depth}: {url}")
            
            try:
                await page.goto(url, wait_until="networkidle", timeout=60000)
                
                # Check for Human-in-the-Loop Gates (CAPTCHA)
                content = await page.content()
                if "captcha" in content.lower():
                    st.warning(f"⚠️ CAPTCHA detected at {url}. Manual intervention required.")
                    # In a local environment, page.pause() would trigger here
                
                # Payment Discovery
                if await manager.detect_payment_vector(content):
                    manager.log_finding(url, depth, "Form/Iframe Detected")
                    findings_area.error(f"💳 **Payment Vector Found:** {url} (Depth {depth})")

                manager.r.sadd("audit:visited", url)
                visited_count += 1

                # Extract links for hops (Component A)
                if depth < 2:
                    hrefs = await page.eval_on_selector_all("a", "elements => elements.map(e => e.href)")
                    for href in hrefs:
                        if href.startswith("http"):
                            # Determine if this is a new domain hop
                            is_external = target_url not in href
                            new_depth = depth + 1 if is_external else depth
                            queue.append((href, new_depth))

            except Exception as e:
                st.error(f"Could not audit {url}: {e}")

        await browser.close()
        st.success(f"Audit Complete. Processed {visited_count} URLs.")

# --- 4. STREAMLIT UI ---
st.title("🛡️ PCI Payment Discovery Agent")
st.caption("Automated Security Engineering Audit Tool")

with st.sidebar:
    st.header("Settings")
    root_url = st.text_input("Root Domain", "https://example.com")
    run_btn = st.button("🚀 Start Audit")
    
    if st.button("🗑️ Clear Redis State"):
        m = AuditManager()
        m.r.flushall()
        st.sidebar.success("State Cleared.")

if run_btn:
    manager = AuditManager()
    asyncio.run(run_pci_audit(root_url, manager))
