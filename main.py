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
   # STEP 1 — EXTRACT STRUCTURED UI SNAPSHOT
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
   # STEP 2 — BUILD PROMPT
   # =============================
   prompt = f"""
You are generating automated exploratory test cases.
DETERMINISTIC POLICY:
If test_data is NOT empty:
- Generate exactly one positive scenario using ONLY provided values.
- Also generate negative scenarios.
If test_data IS empty:
- Generate ONLY negative scenarios.
- Do NOT assume valid credentials.
Use ONLY the UI snapshot below.
Do NOT hallucinate selectors.
Each test case MUST follow this structure:
{{
 "description": "short description",
 "steps": [
   {{
     "action": "type" | "click" | "assert_visible",
     "selector": "CSS selector",
     "value": "text (only for type)"
   }}
 ]
}}
Return strictly valid JSON:
{{
 "test_cases": []
}}
UI Snapshot:
{json.dumps(ui_snapshot)}
Test Data:
{json.dumps(test_data)}
"""
   # =============================
   # STEP 3 — CALL OPENAI
   # =============================
   response = client.chat.completions.create(
       model="gpt-4.1-mini",
       messages=[{"role": "user", "content": prompt}],
       temperature=0.2,
   )
   try:
       result = json.loads(response.choices[0].message.content)
   except Exception:
       return {"error": "Failed to parse AI response"}
   test_cases = result.get("test_cases", [])
   execution_log = []
   # =============================
   # STEP 4 — EXECUTION ENGINE
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
           # Reset page per test case
           await page.goto(url)
           await page.wait_for_load_state("networkidle")
           for step in steps:
               if not isinstance(step, dict):
                   execution_log.append("⚠ Invalid step format skipped")
                   continue
               action = step.get("action")
               selector = step.get("selector")
               value = step.get("value", "")
               if not action or not selector:
                   execution_log.append("⚠ Missing action/selector")
                   continue
               try:
                   locator = page.locator(selector)
                   if await locator.count() == 0:
                       execution_log.append(f"❌ Selector not found: {selector}")
                       continue
                   if action == "type":
                       await page.fill(selector, value)
                       execution_log.append(f"Executed: type on {selector}")
                   elif action == "click":
                       await page.click(selector)
                       execution_log.append(f"Executed: click on {selector}")
                   elif action == "assert_visible":
                       await page.wait_for_selector(selector, timeout=3000)
                       execution_log.append(f"Executed: assert_visible on {selector}")
                   else:
                       execution_log.append(f"⚠ Unknown action: {action}")
               except Exception as e:
                   execution_log.append(
                       f"❌ Failed: {action} on {selector} - {str(e)}"
                   )
       screenshot_bytes = await page.screenshot(full_page=True)
       screenshot_base64 = base64.b64encode(screenshot_bytes).decode("utf-8")
       await browser.close()
   return {
       "test_cases": test_cases,
       "execution_log": execution_log,
       "screenshot": screenshot_base64,
   }