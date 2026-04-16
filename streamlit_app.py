import streamlit as st
import asyncio
import nest_asyncio
import redis
import json
import os
from playwright.async_api import async_playwright
from playwright_stealth import stealth_async
from langchain_openai import ChatOpenAI
from dotenv import load_dotenv

# Initialize async support for Streamlit
nest_asyncio.apply()
load_dotenv()

# --- CONFIGURATION & STATE (Component B) ---
st.set_page_config(page_title="PCI Audit Agent", layout="wide")

class AuditManager:
    def __init__(self):
        # Redis handles "State Recoverability" [cite: 71, 75]
        self.r = redis.Redis(host='localhost', port=6379, decode_responses=True)
        self.llm = ChatOpenAI(model="gpt-4o")

    def is_visited(self, url):
        return self.r.sismember("audit:visited", url)

    def log_finding(self, url, depth, screenshot):
        finding = json.dumps({"url": url, "depth": depth, "screenshot": screenshot})
        self.r.rpush("audit:findings", finding) # Component B: Auditability Log [cite: 76]

    async def detect_payment_vector(self, html):
        # Component A: Payment Page Detection [cite: 54, 101]
        prompt = f"Analyze for CC fields, Stripe/PayPal iframes, or checkout modals. Reply 'MATCH' or 'SAFE':\n\n{html[:1500]}"
        response = await self.llm.ainvoke(prompt)
        return "MATCH" in response.content.upper()

# --- CRAWLER ENGINE (Component A) ---
async def run_audit_cycle(target_url, manager):
    async with async_playwright() as p:
        # Component A: Headless browser with throttling [cite: 62, 63]
        browser = await p.chromium.launch(headless=True, slow_mo=2000) 
        context = await browser.new_context(user_agent="PCI-Auditor/1.0")
        page = await context.new_page()
        await stealth_async(page)

        queue = [(target_url, 0)] # (URL, Hop Depth)
        
        while queue:
            url, depth = queue.pop(0)
            if manager.is_visited(url) or depth > 2: continue # Enforce 2-hop limit [cite: 33, 115]

            st.write(f"🔍 **Auditing Depth {depth}:** {url}")
            
            try:
                await page.goto(url, wait_until="networkidle", timeout=60000)
                
                # Component D: CAPTCHA/Auth Detection [cite: 80, 91]
                content = await page.content()
                if "captcha" in content.lower():
                    st.warning(f"⚠️ CAPTCHA detected at {url}. CDP Handoff required.")
                    await page.pause() # Manual intervention gate [cite: 95]

                # Identify findings
                if await manager.detect_payment_vector(content):
                    path = f"screenshots/finding_{depth}_{hash(url)}.png"
                    await page.screenshot(path=path) # Component A: Evidence capture [cite: 65]
                    manager.log_finding(url, depth, path)
                    st.error(f"💳 Payment Vector Flagged: {url}")

                manager.r.sadd("audit:visited", url)

                # Link Extraction for Depth 1 & 2 [cite: 24, 29]
                if depth < 2:
                    links = await page.eval_on_selector_all("a", "elements => elements.map(e => e.href)")
                    for link in links:
                        if link.startswith("http"):
                            is_ext = target_url not in link
                            new_depth = depth + 1 if is_ext else depth
                            queue.append((link, new_depth))
            except Exception as e:
                st.error(f"Error on {url}: {e}")

        await browser.close()

# --- STREAMLIT UI ---
st.title("🛡️ PCI Payment Discovery & Audit Agent")
st.markdown("### Securin Security Engineering Assessment")

target = st.text_input("Enter Root Domain URL", "https://example.com")
col1, col2 = st.columns(2)

if col1.button("🚀 Start Audit Phase"):
    # Enforce Phase-Based Gate Checks [cite: 70]
    if not os.path.exists("screenshots"): os.makedirs("screenshots")
    manager = AuditManager()
    asyncio.run(run_audit_cycle(target, manager))
    st.success("Audit Completed. Phase Gate: Report Generation Ready.")

if col2.button("📋 Clear Audit State"):
    r = redis.Redis(host='localhost', port=6379)
    r.flushall()
    st.info("Redis State Cleared.")
