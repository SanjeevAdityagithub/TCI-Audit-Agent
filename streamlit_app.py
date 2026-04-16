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

# --- 1. ROBUST ENVIRONMENT SYNC ---
def ensure_playwright_installed():
    """Forces installation of binaries if not present or mismatched."""
    try:
        # Check if chromium is already available in the expected path
        import playwright
        # Attempt to get the version to ensure sync
        subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], check=True)
        subprocess.run([sys.executable, "-m", "playwright", "install-deps", "chromium"], check=True)
    except Exception as e:
        st.error(f"Critical: Browser environment setup failed: {e}")

# Run this at the very start of the app
if 'playwright_ready' not in st.session_state:
    with st.spinner("Preparing secure audit environment..."):
        ensure_playwright_installed()
        st.session_state.playwright_ready = True

nest_asyncio.apply()
load_dotenv()

# --- 2. AUDIT MANAGER (Component B) ---
class AuditManager:
    def __init__(self):
        try:
            self.r = redis.Redis(
                host=st.secrets.get("REDIS_HOST", "localhost"),
                port=int(st.secrets.get("REDIS_PORT", 6379)),
                password=st.secrets.get("REDIS_PASSWORD", None),
                decode_responses=True
            )
            self.llm = ChatOpenAI(model="gpt-4o", api_key=st.secrets.get("OPENAI_API_KEY"))
        except Exception as e:
            st.error("Redis/OpenAI Init Error. Check Secrets.")

    def is_visited(self, url):
        return self.r.sismember("audit:visited", url)

    async def analyze_page(self, html):
        prompt = "Does this HTML contain payment fields/iframes? Reply 'MATCH' or 'SAFE'.\n\n" + html[:2000]
        res = await self.llm.ainvoke(prompt)
        return "MATCH" in res.content.upper()

# --- 3. AUDIT ENGINE (Component A) ---
async def run_pci_audit(target_url, manager):
    async with async_playwright() as p:
        # ULTIMATE CLOUD LAUNCH FLAGS
        try:
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                    "--no-zygote", # Reduces process count
                    "--single-process", # Crucial for memory-limited containers
                ]
            )
            context = await browser.new_context(user_agent="PCI-Auditor/1.0")
            page = await context.new_page()
            await Stealth().apply_stealth_async(page)

            queue = [(target_url, 0)]
            while queue:
                url, depth = queue.pop(0)
                if manager.is_visited(url) or depth > 2: continue

                st.info(f"Auditing: {url} (Depth {depth})")
                await page.goto(url, wait_until="domcontentloaded", timeout=45000)
                
                content = await page.content()
                if await manager.analyze_page(content):
                    st.error(f"💳 PCI Finding: {url}")
                    manager.r.rpush("audit:findings", json.dumps({"url": url, "depth": depth}))

                manager.r.sadd("audit:visited", url)
                
                if depth < 2:
                    hrefs = await page.eval_on_selector_all("a", "elements => elements.map(e => e.href)")
                    for href in hrefs:
                        if href.startswith("http") and target_url in href:
                            queue.append((href, depth + 1))
            
            await browser.close()
            st.success("Audit Cycle Completed.")
        except Exception as e:
            st.error(f"Browser Execution Error: {e}")

# --- 4. UI ---
st.title("🛡️ PCI Audit Agent")
root_url = st.text_input("Target URL", "https://example.com")
if st.button("Start"):
    manager = AuditManager()
    asyncio.run(run_pci_audit(root_url, manager))
