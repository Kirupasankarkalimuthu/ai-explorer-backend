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

You are also given optional test_data as key-value pairs.

==============================

GENERAL BEHAVIOR RULES

==============================

1. If test_data is NOT empty:

   - Match each test_data key with input field id, name, placeholder, or associated label text.

   - Use matching test_data values when generating positive test scenarios.

   - If a test_data key does not match any field in the DOM, ignore it.

   - Do NOT hallucinate fields that do not exist.

2.If test_data IS empty:
   - DO NOT generate positive login using known real credentials.
   - Generate only negative scenarios.
   - Do NOT attempt successful login unless test_data explicitly provides valid credentials.
   - Generate negative test scenarios (invalid input, empty fields, etc.).
   - Do NOT assume successful login unless DOM contains clear success indicators.

3. If input fields and a submit-type button exist:

   - Generate at least one interaction scenario.

   - Never return empty test_cases or automation_steps if interactive elements exist.

==============================

STEP 1 — Identify UI Elements

==============================

Identify:

- Input fields (id, name, placeholder, type)

- Buttons (id, text, type)

- Links

==============================

STEP 2 — Generate Test Cases

==============================

Generate concise exploratory test cases.

Include:

- Positive scenario (using test_data if provided, otherwise dummy data)

- Negative scenarios (invalid input, empty fields, boundary cases)

Each test case must be structured as:

{{

  "description": "short description"

}}

==============================

STEP 3 — Generate automation_steps

==============================

automation_steps MUST be a flat list of structured objects.

Each step MUST strictly follow this format:

{{

  "action": "type" | "click" | "assert_url_contains" | "assert_text" | "assert_visible",

  "selector": "CSS selector",

  "value": "text value (only required for type or assert_text)"

}}

STRICT RULES:

- DO NOT return Playwright-style strings like "#username.type('admin')"

- DO NOT return JavaScript code

- DO NOT return natural language steps

- DO NOT nest steps inside test cases

- DO NOT return strings instead of objects

- automation_steps MUST be a list of dictionaries

- For every test case generated, corresponding automation_steps must exist

Selector Rules:

1. If id exists, ALWAYS use "#id".

2. If no id, use input[name="..."].

3. Prefer selectors that uniquely identify one element.

4. If multiple elements match, choose the most specific selector available.

5. Never hallucinate selectors not present in DOM.

==============================

OUTPUT FORMAT

==============================

Return strictly valid JSON in this exact structure:

{{

  "test_cases": [],

  "automation_steps": []

}}

Never omit required fields.

Never return empty arrays if interactive elements exist.

==============================

TEST DATA

==============================

{json.dumps(test_data)}

==============================

DOM

==============================

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
       print("========== AI RAW RESULT ==========")
       print(result)
       print("===================================")
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