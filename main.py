from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from playwright.async_api import async_playwright # type: ignore
from openai import OpenAI
import json
import base64
app = FastAPI()
client = OpenAI()
# Allow extension requests
app.add_middleware(
   CORSMiddleware,
   allow_origins=["*"],
   allow_credentials=True,
   allow_methods=["*"],
   allow_headers=["*"],
)

@app.post("/run")
async def run_exploration(payload: dict):
   test_data = payload.get("test_data", {})
   url = payload.get("url")
   if not url:
       return {"error": "URL missing"}
   execution_log = []
   test_cases = []
   screenshot_base64 = ""
   async with async_playwright() as p:
       browser = await p.chromium.launch(
           headless=True,
           args=["--no-sandbox", "--disable-dev-shm-usage"]
       )
       page = await browser.new_page()
       await page.goto(url)
       await page.wait_for_load_state("networkidle")
       # ✅ Single source of truth DOM
       dom = await page.content()
       prompt = f"""
You are analyzing a webpage DOM for automated exploratory testing.
You are given test_data as key-value pairs.
INSTRUCTIONS:
1. If test_data is NOT empty:
  - Match each test_data key with input field id, name, placeholder, or label text.
  - Use matching test_data values when generating automation steps.
  - Do NOT hallucinate fields that do not exist in DOM.
  - If a test_data key does not match any field, ignore it.
2. If test_data IS empty:
  - Generate negative test scenarios.
  - Generate realistic dummy data for positive scenarios.
  - Do NOT assume login success unless a success indicator exists in DOM.
  - If the page contains input fields and a submit button,generate at least one scenario interacting with them.
3. Generate automation_steps using STRICT selector rules:
  - If id exists, ALWAYS use "#id".
  - If no id, use input[name="..."].
  - Selector must match exactly ONE element in DOM.
  - Do NOT use generic selectors like div, button alone.
  - Return flat structure only.
Return strictly JSON:
{{
 "test_cases": [],
 "automation_steps": []
}}
IMPORTANT:
- You MUST generate at least one test case.
- You MUST generate at least one automation step if interactive elements exist.
- If input fields exist in DOM, generate at least one interaction.
- Never return empty arrays unless DOM has zero interactive elements.
Test Data:
{json.dumps(test_data)}
DOM:
{dom}
"""
       # 🔥 Force strict JSON
       response = client.chat.completions.create(
           model="gpt-4o-mini",
           messages=[
               {"role": "system", "content": "You are a strict JSON generator."},
               {"role": "user", "content": prompt}
           ],
           response_format={"type": "json_object"},
           temperature=0
       )
       result = json.loads(response.choices[0].message.content) # type: ignore
       # ===============================
       # Extract test cases safely
       # ===============================
       raw_test_cases = result.get("test_cases", [])
       if isinstance(raw_test_cases, list):
           test_cases = raw_test_cases
       else:
           test_cases = []
       # ===============================
       # Extract & normalize steps safely
       # ===============================
       steps = result.get("automation_steps", [])
       # If steps returned as string, try parsing
       if isinstance(steps, str):
           try:
               steps = json.loads(steps)
           except:
               steps = []
       if not isinstance(steps, list):
           steps = []
       # Flatten nested structures if model nested steps
       flattened_steps = []
       for item in steps:
           # Case 1: Proper flat step
           if isinstance(item, dict) and "action" in item:
               flattened_steps.append(item)
           # Case 2: Nested inside "steps"
           elif isinstance(item, dict) and "steps" in item:
               for nested in item["steps"]:
                   if isinstance(nested, dict):
                       flattened_steps.append(nested)
       # ===============================
       # Execute Steps
       # ===============================
       for step in flattened_steps:
           action = step.get("action")
           selector = step.get("selector")
           value = step.get("value", "")
           try:
               if action == "type":
                   await page.fill(selector, value, timeout=3000)
               elif action == "click":
                   await page.click(selector, timeout=3000)
               execution_log.append(f"Executed: {action} on {selector}")
           except Exception as e:
               execution_log.append(
                   f"Failed: {action} on {selector} - {str(e)}"
               )
       # ===============================
       # Capture Screenshot
       # ===============================
       screenshot_bytes = await page.screenshot(full_page=True)
       screenshot_base64 = base64.b64encode(
           screenshot_bytes
       ).decode("utf-8")
       await browser.close()
   return {
       "test_cases": test_cases,
       "execution_log": execution_log,
       "screenshot": screenshot_base64
   }