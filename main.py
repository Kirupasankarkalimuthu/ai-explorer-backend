from fastapi import FastAPI
from playwright.async_api import async_playwright # type: ignore
from openai import OpenAI
import json
app = FastAPI()
client = OpenAI()

@app.post("/run")
async def run_exploration(payload: dict):
   url = payload.get("url")
   if not url:
       return {"error": "URL missing"}
   execution_log = []
   async with async_playwright() as p:
       browser = await p.chromium.launch(
           headless=True,
           args=["--no-sandbox", "--disable-dev-shm-usage"]
       )
       page = await browser.new_page()
       await page.goto(url)
       await page.wait_for_load_state("networkidle")
       # 🔥 Capture DOM from SAME Playwright session
       dom = await page.content()
       prompt = f"""
You are analyzing a webpage DOM for automated exploratory testing.
STEP 1:
Identify interactive elements:
- input fields (id, name, placeholder)
- buttons (id, text, type)
- links
STEP 2:
Generate concise exploratory negative test cases only.
STEP 3:
Generate automation_steps using STRICT selector rules:
1. If id exists, ALWAYS use "#id".
2. If no id, use input[name="..."].
3. Selector must match exactly ONE element in DOM.
4. Do NOT use generic selectors.
5. Return flat structure only.
Return strictly JSON:
{{
 "test_cases": [],
 "automation_steps": []
}}
DOM:
{dom}
"""
       # 🔥 Force strict JSON response
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
       test_cases = result.get("test_cases", [])
       steps = result.get("automation_steps", [])
       # 🔥 Defensive parsing in case model returns stringified JSON
       if isinstance(steps, str):
           try:
               steps = json.loads(steps)
           except:
               steps = []
       if not isinstance(steps, list):
           steps = []
       # ===============================
       # Execute Steps
       # ===============================
       for step in steps:
           if not isinstance(step, dict):
               execution_log.append("⚠ Skipping invalid step format")
               continue
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
       await browser.close()
   return {
       "test_cases": test_cases if isinstance(test_cases, list) else [],
       "execution_log": execution_log
   }