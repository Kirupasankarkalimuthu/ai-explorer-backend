import json
import base64
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from openai import OpenAI
from playwright.async_api import async_playwright
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
   # UI SNAPSHOT
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
                   type: i.type
               }))
       """)
       buttons = await page.evaluate("""
           () => Array.from(document.querySelectorAll("button"))
               .map(b => ({
                   id: b.id || null,
                   text: b.innerText.trim()
               }))
       """)
       await browser.close()
   ui_snapshot = {
       "inputs": inputs,
       "buttons": buttons
   }
   # =============================
   # AI TEST GENERATION
   # =============================
   prompt = f"""
Generate login exploratory test cases.
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
   result = json.loads(response.choices[0].message.content)
   test_cases = result.get("test_cases", [])
   execution_log = []
   structured_results = []
   # =============================
   # EXECUTION ENGINE
   # =============================
   async with async_playwright() as p:
       browser = await p.chromium.launch(
           headless=HEADLESS,
           args=["--no-sandbox", "--disable-dev-shm-usage"],
       )
       context = await browser.new_context()
       page = await context.new_page()
       for test_case in test_cases:
           description = test_case.get("description", "Unnamed Test")
           steps = test_case.get("steps", [])
           execution_log.append(f"▶ Running: {description}")
           await page.goto(url)
           await page.wait_for_load_state("networkidle")
           initial_url = page.url
           validation_detected = False
           for step in steps:
               action = step.get("action")
               selector = step.get("selector")
               value = step.get("value", "")
               try:
                   if action == "type":
                       await page.fill(selector, "")
                       await page.fill(selector, value)
                       execution_log.append(f"Executed: type '{value}' on {selector}")
                   elif action == "click":
                       await page.click(selector)
                       execution_log.append(f"Executed: click on {selector}")
                       await page.wait_for_timeout(1000)
               except Exception as e:
                   execution_log.append(f"❌ Failed: {action} on {selector} - {str(e)}")
           # URL detection
           url_changed = page.url != initial_url
           if url_changed:
               execution_log.append("✔ URL changed after action")
           else:
               execution_log.append("⚠ URL did not change after action")
           # Validation detection
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
                   if await locator.count() > 0:
                       text = await locator.first.inner_text()
                       if text and len(text.strip()) > 0:
                           error_messages.append(text.strip())
                           validation_detected = True
               except:
                   pass
           if error_messages:
               execution_log.append(f"⚠ Validation Messages Detected: {error_messages}")
           # =============================
           # PASS / FAIL CLASSIFICATION
           # =============================
           is_positive = "positive" in description.lower()
           if is_positive:
               if url_changed:
                   status = "PASS"
                   reason = "Login successful and URL changed."
               else:
                   status = "FAIL"
                   reason = "Expected successful login but URL did not change."
           else:
               if validation_detected:
                   status = "PASS"
                   reason = "Validation message detected as expected."
               else:
                   status = "FAIL"
                   reason = "Expected validation message but none detected."
           structured_results.append({
               "test_case": description,
               "status": status,
               "reason": reason
           })
       screenshot_bytes = await page.screenshot(full_page=True)
       screenshot_base64 = base64.b64encode(screenshot_bytes).decode("utf-8")
       await browser.close()
   return {
       "test_cases": test_cases,
       "execution_log": execution_log,
       "results": structured_results,
       "screenshot": screenshot_base64,
   }