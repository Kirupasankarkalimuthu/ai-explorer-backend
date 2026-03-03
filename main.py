import json
import base64
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from openai import OpenAI
from playwright.async_api import async_playwright # type: ignore
app = FastAPI()
client = OpenAI()
app.add_middleware(
   CORSMiddleware,
   allow_origins=["*"],
   allow_credentials=True,
   allow_methods=["*"],
   allow_headers=["*"],
)
HEADLESS = True

@app.post("/run")
async def run_exploration(request: Request):
   payload = await request.json()
   url = payload.get("url")
   test_data = payload.get("test_data", {})
   if not url:
       return {"error": "URL is required"}
   # =============================
   # STEP 1 — CAPTURE UI SNAPSHOT
   # =============================
   async with async_playwright() as p:
       browser = await p.chromium.launch(
           headless=HEADLESS,
           args=["--no-sandbox", "--disable-dev-shm-usage"],
       )
       context = await browser.new_context()
       page = await context.new_page()
       await page.goto(url)
       await page.wait_for_load_state("networkidle")
       inputs = await page.evaluate("""
           () => Array.from(document.querySelectorAll("input"))
               .filter(i => i.type !== "hidden")
               .map(i => ({
                   id: i.id || null,
                   name: i.name || null,
                   type: i.type,
                   placeholder: i.placeholder || null
               }))
       """)
       buttons = await page.evaluate("""
           () => Array.from(document.querySelectorAll("button"))
               .map(b => ({
                   id: b.id || null,
                   text: b.innerText.trim(),
                   type: b.type || null
               }))
       """)
       await browser.close()
   ui_snapshot = {
       "inputs": inputs,
       "buttons": buttons
   }
   # =============================
   # STEP 2 — AI TEST GENERATION
   # =============================
   prompt = f"""
    Generate exploratory login test cases.
    If test_data exists:
    - Include one positive scenario using provided values.
    - Include negative scenarios.
    If test_data empty:
    - Generate only negative scenarios.
    Return JSON only.
    Structure:
    {{
    "test_cases": [
    {{
        "description": "...",
        "steps": [
        {{"action": "type", "selector": "...", "value": "..."}},
        {{"action": "click", "selector": "..."}}
        ]
    }}
    ]
    }}
    Use ONLY selectors from this UI snapshot.
    UI Snapshot:
    {json.dumps(ui_snapshot)}
    Test Data:
    {json.dumps(test_data)}
    """
   response = client.chat.completions.create(
       model="gpt-4.1-mini",
       messages=[{"role": "user", "content": prompt}],
       temperature=0.2,
       response_format={"type": "json_object"},
   )
   result = json.loads(response.choices[0].message.content) # type: ignore
   test_cases = result.get("test_cases", [])
   execution_log = []
   # =============================
   # STEP 3 — EXECUTION ENGINE
   # =============================
   async with async_playwright() as p:
       browser = await p.chromium.launch(
           headless=HEADLESS,
           args=["--no-sandbox", "--disable-dev-shm-usage"],
       )
       context = await browser.new_context()
       page = await context.new_page()
       console_errors = []
       network_errors = []
       page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
       page.on("response", lambda response: network_errors.append(
           f"{response.status} - {response.url}"
       ) if response.status >= 400 else None)
       for test_case in test_cases:
           description = test_case.get("description", "Unnamed Test")
           steps = test_case.get("steps", [])
           execution_log.append(f"▶ Running: {description}")
           await page.goto(url)
           await page.wait_for_load_state("networkidle")
           initial_url = page.url
           for step in steps:
               action = step.get("action")
               selector = step.get("selector")
               value = step.get("value", "")
               try:
                   if action == "type":
                       await page.fill(selector, value)
                       execution_log.append(f"DEBUG: Typing '{value}' into {selector}")
                       execution_log.append(f"Executed: type on {selector}")
                   elif action == "click":
                       await page.click(selector)
                       execution_log.append(f"DEBUG: Clicking on {selector}")
                       execution_log.append(f"Executed: click on {selector}")
                       await page.wait_for_timeout(800)
               except Exception as e:
                   execution_log.append(f"❌ Failed: {action} on {selector} - {str(e)}")
           # =============================
           # URL CHANGE CHECK
           # =============================
           if page.url != initial_url:
               execution_log.append("✔ URL changed after action")
           else:
               execution_log.append("⚠ URL did not change after action")
           # =============================
           # FORM-SCOPED VALIDATION CHECK
           # =============================
           error_messages = []
           selectors_to_check = [
               "#error",
               ".error",
               ".error-message",
               ".validation-message",
               "[class*='error']",
           ]
           for sel in selectors_to_check:
               try:
                   locator = page.locator(sel)
                   count = await locator.count()
                   if count > 0:
                       text = await locator.first.inner_text()
                       if text and len(text.strip()) > 0:
                           error_messages.append(text.strip())
               except:
                   pass
           if error_messages:
               execution_log.append(f"⚠ Validation Messages Detected: {error_messages}")
       if console_errors:
           execution_log.append(f"⚠ Console Errors: {console_errors}")
       if network_errors:
           execution_log.append(f"⚠ Network Errors: {network_errors}")
       screenshot_bytes = await page.screenshot(full_page=True)
       screenshot_base64 = base64.b64encode(screenshot_bytes).decode("utf-8")
       await browser.close()
   return {
       "test_cases": test_cases,
       "execution_log": execution_log,
       "screenshot": screenshot_base64,
   }