import streamlit as st
import asyncio
import nest_asyncio
import redis
import json
import os
import subprocess
import sys
from playwright.async_api import async_playwright
from playwright_stealth import Stealth 
from langchain_openai import ChatOpenAI
from dotenv import load_dotenv

# --- 1. ENVIRONMENT SYNC (Cloud Friendly) ---
def ensure_playwright_binaries():
    """Installs only the browser binaries. System deps are handled by packages.txt"""
    # Standard location for playwright browsers on Streamlit Cloud
    playwright_path = os.path.expanduser("~/.cache/ms-playwright")
    if not os.path.exists(playwright_path):
        try:
            with st.spinner("🚀 Initializing Browser Engine..."):
                # Install only the chromium binary, no sudo-level deps
                subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], check=True)
        except Exception as e:
            st.error(f"Browser Binary Sync Failed: {e}")

# Trigger once per session
if 'browser_init' not in st.session_state:
    ensure_playwright_binaries()
    st.session_state.browser_init = True

nest_asyncio.apply()
load_dotenv()

# --- 2. AUDIT LOGIC (Component B) ---
class AuditManager:
    def __init__(self):
        try:
            # Connect using Secrets (Upstash/Cloud Redis recommended)
            self.r = redis.Redis(
                host=st.secrets.get("REDIS_HOST", "localhost"),
                port=int(st.secrets.get("REDIS_PORT", 6379)),
                password=st.secrets.get("REDIS_PASSWORD", None),
                decode_responses=True
            )
            self.llm = ChatOpenAI(model="gpt-4o", api_key=st.secrets.get("OPENAI_API_KEY"))
        except Exception as e:
            st.error("Infrastructure Error: Check Redis/OpenAI Secrets.")

    def is_visited(self, url):
        return self.r.sismember("audit:visited", url)

    async def analyze_content(self, html):
        prompt = "Identify if this HTML contains CC forms or payment iframes. Reply 'MATCH' or 'SAFE'.\n\n" + html[:2000]
        res = await self.llm.ainvoke(prompt)
        return "MATCH" in res.content.upper()

# --- 3. CRAWLER ENGINE (Component A) ---
async def run_pci_audit(target_url, manager):
    async with async_playwright() as p:
        try:
            # Launch arguments optimized for limited-resource containers
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                    "--single-process"
                ]
            )
            context = await browser.new_context(user_agent="PCI-Auditor/1.0")
            page = await context.new_page()
            await Stealth().apply_stealth_async(page)

            queue = [(target_url, 0)]
            visited_count = 0

            while queue:
                url, depth = queue.pop(0)
                if manager.is_visited(url) or depth > 2:
                    continue

                st.write(f"🔎 Auditing: {url} (Depth {depth})")
                
                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=45000)
                    content = await page.content()
                    
                    if await manager.analyze_content(content):
                        st.error(f"🚨 PCI FINDING: {url}")
                        manager.r.rpush("audit:findings", json.dumps({"url": url, "depth": depth}))

                    manager.r.sadd("audit:visited", url)
                    visited_count += 1

                    if depth < 2:
                        # Extract links for next hop
                        hrefs = await page.eval_on_selector_all("a", "elements => elements.map(e => e.href)")
                        for h in hrefs:
                            if h.startswith("http") and target_url in h:
                                queue.append((h, depth + 1))
                except Exception as e:
                    st.warning(f"Skipping {url}: {e}")

            await browser.close()
            st.success(f"Audit Complete! Processed {visited_count} endpoints.")
        except Exception as e:
            st.error(f"Browser Crash: {e}")

# --- 4. DASHBOARD ---
st.title("🛡️ PCI Audit Agent")
url_input = st.text_input("Root Domain", "https://example.com")

if st.button("Launch Audit Phase"):
    if not st.secrets.get("OPENAI_API_KEY"):
        st.error("API Key missing in Secrets!")
    else:
        asyncio.run(run_pci_audit(url_input, AuditManager()))
