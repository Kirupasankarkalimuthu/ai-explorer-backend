from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Dict
from openai import OpenAI
from playwright.async_api import async_playwright
import os

app = FastAPI()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

app.add_middleware(
   CORSMiddleware,
   allow_origins=["*"],
   allow_credentials=True,
   allow_methods=["*"],
   allow_headers=["*"],
)
class PageData(BaseModel):
   url: str
   dom: str
   networkLogs: List[Dict]
@app.post("/explore")
async def receive_page_data(data: PageData):
   prompt = f"""
You are analyzing a webpage DOM for automated exploratory testing.
STEP 1:
Identify all interactive UI elements present in the DOM:
- Input fields (include name, id, placeholder if available)
- Buttons (include exact visible text OR type attribute)
- Links
List them clearly in "ui_elements".
STEP 2:
Based ONLY on actual elements found above,
generate concise exploratory test cases.
Rules:
- Do NOT assume valid credentials.
- If no valid credentials provided, test only invalid and empty scenarios.
- Do NOT invent button names like "Login" if not present.
- Use EXACT button text as shown in DOM.
- If a button has type="submit", use selector: button[type="submit"]
- For inputs, prefer input[name="..."] if available.
STEP 3:
Generate automation_steps using ONLY selectors that actually exist in DOM.
IMPORTANT:
automation_steps MUST be a simple flat list like this:
[
 {{"action": "type", "selector": "input[name='username']", "value": "invalid_user"}},
 {{"action": "type", "selector": "input[name='password']", "value": "wrong_pass"}},
 {{"action": "click", "selector": "button[type='submit']"}}
]
Each step MUST contain:
- action (string)
- selector (string)
- optional value (string)
Do NOT nest objects.
Do NOT add extra fields.
Do NOT return step numbers.
Return only simple flat objects as shown above.
Return strictly in this JSON format:
{{
 "ui_elements": [],
 "summary": "short title",
 "test_cases": [],
 "automation_steps": []
}}
DOM:
{data.dom}
"""
   response = client.chat.completions.create(
       model="gpt-4o-mini",
       messages=[
           {"role": "system", "content": "You are a strict JSON generator."},
           {"role": "user", "content": prompt}
       ],
       temperature=0
   )
   raw_output = response.choices[0].message.content.strip()
   import json
   import re
   # Extract JSON object using regex
   json_match = re.search(r"\{.*\}", raw_output, re.DOTALL)
   if json_match:
      json_string = json_match.group(0)
   else:
      json_string = raw_output  # fallback
   try:
      structured_output = json.loads(json_string)
   except Exception as e:
      structured_output = {
         "summary": "Parsing failed",
         "test_cases": [raw_output],
         "automation_steps": []
      }
   print("===== AI STRUCTURED OUTPUT =====")
   print(structured_output)
   print("================================")
   return structured_output
@app.post("/execute")
async def execute_steps(payload: dict):
   steps = payload.get("automation_steps", [])
   if isinstance(steps, str):
      try:
         import json
         steps = json.loads(steps)
      except:
         steps = []
   if not isinstance(steps, list):
      steps = []
   url = payload.get("url")
   execution_log = []
   async with async_playwright() as p:
       browser = await p.chromium.launch(headless=True)
       page = await browser.new_page()
       await page.goto(url)
       await page.wait_for_load_state("networkidle")
       for step in steps:
         if not isinstance(step, dict):
               execution_log.append("⚠ Skipping invalid step format")
               continue
         action = step.get("action")
         selector = step.get("selector")
         value = step.get("value", "")
         try:
            if action == "click":
               try:
                     await page.click(selector, timeout=3000)
               except:
      # Fallback 1: text-based
                     await page.get_by_text(selector, exact=False).click(timeout=3000)
            elif action == "type":
               try:
                     await page.fill(selector, value, timeout=3000)
               except:
      # Fallback: try name attribute
                     await page.fill(f'[name="{selector}"]', value, timeout=3000)
            elif action == "assert":
               await page.locator(selector).wait_for(timeout=3000)
            execution_log.append(f"Executed: {action} on {selector}")
         except Exception as e:
            execution_log.append(f"Initial failure: {action} on {selector}")
         # Self-healing attempt
            current_dom = await page.content()
            healing_prompt = f"""
         The following step failed during execution:
         Action: {action}
         Selector: {selector}
         Error: {str(e)}
         Here is the current DOM:
         {current_dom[:4000]}
         Suggest a corrected selector ONLY in JSON format:
         {{"selector": "correct_selector"}}
         """
            healing_response = client.chat.completions.create(
               model="gpt-4o-mini",
               messages=[
                  {"role": "system", "content": "You fix broken CSS selectors."},
                  {"role": "user", "content": healing_prompt}
               ],
               temperature=0
            )
            try:
               import json, re
               raw = healing_response.choices[0].message.content
               match = re.search(r"\{.*\}", raw, re.DOTALL)
               corrected = json.loads(match.group(0))["selector"]
         # Retry once
               if action == "click":
                  await page.click(corrected, timeout=3000)
               elif action == "type":
                  await page.fill(corrected, value, timeout=3000)
               execution_log.append(f"Self-healed: used {corrected}")
            except:
               execution_log.append(f"Final failure: {action} on {selector}")
       await page.screenshot(path="final_state.png")
       await browser.close()
   return {
       "status": "Execution completed",
       "log": execution_log
   }
@app.get("/")
def root():
   return {"status": "AI Explorer Backend Running"}