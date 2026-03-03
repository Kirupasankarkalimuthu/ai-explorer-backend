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
HEADLESS = True  # Change to False for local debugging

@app.post("/run")
async def run_exploration(request: Request):
   payload = await request.json()
   url = payload.get("url")
   test_data = payload.get("test_data", {})
# =============================
# PROMPT (STABILIZED)
# =============================
   prompt = f"""
You are analyzing a webpage DOM for automated exploratory testing.
DETERMINISTIC POLICY:
If test_data is NOT empty:
- Generate exactly one positive scenario using ONLY the provided values.
- Also generate negative scenarios.
If test_data IS empty:
- Generate ONLY negative scenarios.
- Do NOT attempt successful login.
- Do NOT use known credentials.
- Do NOT assume valid authentication.
Before generating automation_steps:
- Inspect the DOM and list actual button elements.
- Inspect the DOM and list actual elements that display validation or error messages.
- Use ONLY selectors that exist in the DOM.
- Do NOT assume common class names like .error-message.
- Do NOT assume button[type="submit"] unless it exists exactly in DOM.
- If a selector does not appear exactly in the DOM text,do NOT use it.
AUTOMATION RULES:
Return structured automation_steps as flat JSON objects.
DO NOT return Playwright code strings.
DO NOT nest steps.
Each automation step MUST follow this structure:
{{
 "action": "type" | "click" | "assert_url_contains" | "assert_text" | "assert_visible",
 "selector": "CSS selector",
 "value": "text (only for type or assert_text)"
}}
Selector Rules:
1. If id exists, use "#id".
2. Otherwise use input[name="..."].
3. Prefer specific selectors.
4. Never hallucinate selectors.
Return strictly valid JSON:
{{
 "test_cases": [],
 "automation_steps": []
}}
Test Data:
{json.dumps(test_data)}
"""
# =============================
# CALL OPENAI
# =============================
   response = client.chat.completions.create(
       model="gpt-4.1-mini",
       messages=[{"role": "user", "content": prompt}],
       temperature=0.2,
   )
   result = json.loads(response.choices[0].message.content)
   test_cases = result.get("test_cases", [])
   automation_steps = result.get("automation_steps", [])
   execution_log = []
# =============================
# PLAYWRIGHT EXECUTION
# =============================
   async with async_playwright() as p:
       browser = await p.chromium.launch(
           headless=HEADLESS,
           args=["--no-sandbox", "--disable-dev-shm-usage"],
       )
       context = await browser.new_context()
       page = await context.new_page()
       for test_case in test_cases:
           execution_log.append(
               f"▶ Running: {test_case.get('description', 'Unnamed Test')}"
           )
# Reset page before each test
           await page.goto(url)
           await page.wait_for_load_state("networkidle")
           for step in automation_steps:
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
                   elif action == "assert_url_contains":
                       if value in page.url:
                           execution_log.append(
                               f"Executed: assert_url_contains {value}"
                           )
                       else:
                           execution_log.append(
                               f"❌ URL assertion failed for {value}"
                           )
                   elif action == "assert_text":
                       text = await page.locator(selector).text_content()
                       if value and value in (text or ""):
                           execution_log.append(
                               f"Executed: assert_text on {selector}"
                           )
                       else:
                           execution_log.append(
                               f"❌ Text assertion failed on {selector}"
                           )
                   elif action == "assert_visible":
                       await page.wait_for_selector(selector, timeout=3000)
                       execution_log.append(
                           f"Executed: assert_visible on {selector}"
                       )
                   else:
                       execution_log.append(f"⚠ Unknown action: {action}")
               except Exception as e:
                   execution_log.append(
                       f"❌ Failed: {action} on {selector} - {str(e)}"
                   )
# Take screenshot after last test
       screenshot_bytes = await page.screenshot(full_page=True)
       screenshot_base64 = base64.b64encode(screenshot_bytes).decode("utf-8")
       await browser.close()
   return {
       "test_cases": test_cases,
       "execution_log": execution_log,
       "screenshot": screenshot_base64,
   }