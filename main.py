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
STEP 1:
Identify interactive elements:
- input fields (id, name, placeholder)
- buttons (id, text, type)
- links
STEP 2:
Generate concise exploratory negative test cases.
STEP 3:
Generate automation_steps using STRICT selector rules:
1. If id exists, ALWAYS use "#id".
2. If no id, use input[name="..."].
3. Selector must match exactly ONE element in DOM.
4. Do NOT use generic selectors.
5. Return flat structure only.
Return strictly JSON in this format:
{
 "test_cases": [],
 "automation_steps": []
}
For every generated test case, generate corresponding automation_steps.
IMPORTANT:
- Both "test_cases" and "automation_steps" MUST be present.
- If no automation steps can be generated, return an empty list.
- Do NOT omit the automation_steps field.
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