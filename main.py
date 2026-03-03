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

@app.post("/explore")
async def explore(request: Request):
   payload = await request.json()
   url = payload.get("url")
   if not url:
       return {"error": "URL is required"}
   findings = []
   async with async_playwright() as p:
       browser = await p.chromium.launch(
           headless=HEADLESS,
           args=["--no-sandbox", "--disable-dev-shm-usage"],
       )
       context = await browser.new_context()
       page = await context.new_page()
       # --------------------------
       # STEP 1: Capture interactive elements
       # --------------------------
       await page.goto(url)
       await page.wait_for_load_state("networkidle")
       interactive_elements = await page.evaluate("""
       () => {
           const elements = Array.from(document.querySelectorAll(
               "button, a, [role='button'], input[type='button'], input[type='submit']"
           ));
           return elements
               .filter(el => el.offsetHeight > 0 && el.offsetWidth > 0)
               .map(el => ({
                   tag: el.tagName,
                   id: el.id || null,
                   text: el.innerText ? el.innerText.trim() : null
               }))
               .slice(0, 10);
       }
       """)
       # --------------------------
       # STEP 2: AI decides safe clicks
       # --------------------------
       prompt = f"""
You are an AI exploratory UI tester.
From the interactive elements below, choose up to 5 safe elements to click.
Rules:
- Avoid logout links.
- Avoid external links.
- Avoid destructive actions.
- Only choose elements with visible text or ID.
Return JSON:
{{
 "steps": [
   {{"selector": "...", "description": "..."}}
 ]
}}
Interactive Elements:
{json.dumps(interactive_elements)}
"""
       response = client.chat.completions.create(
           model="gpt-4.1-mini",
           messages=[{"role": "user", "content": prompt}],
           temperature=0.3,
           response_format={"type": "json_object"},
       )
       ai_result = json.loads(response.choices[0].message.content)
       steps = ai_result.get("steps", [])
       # --------------------------
       # STEP 3: Execute Each Action (Option B Mode)
       # --------------------------
       for step in steps:
           selector = step.get("selector")
           description = step.get("description", selector)
           await page.goto(url)
           await page.wait_for_load_state("networkidle")
           initial_url = page.url
           console_errors = []
           network_errors = []
           page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
           page.on("response", lambda response: network_errors.append(
               f"{response.status} - {response.url}"
           ) if response.status >= 400 else None)
           result = "Unknown"
           severity = "Minor"
           try:
               await page.click(selector, timeout=5000)
               await page.wait_for_timeout(1000)
               if page.url != initial_url:
                   result = "Navigation occurred"
                   severity = "Info"
               else:
                   result = "No navigation occurred"
           except Exception as e:
               result = f"Click failed: {str(e)}"
               severity = "Major"
           if console_errors:
               result = f"Console errors detected: {console_errors}"
               severity = "Major"
           if network_errors:
               result = f"Network errors detected: {network_errors}"
               severity = "Critical"
           findings.append({
               "action": description,
               "selector": selector,
               "result": result,
               "severity": severity
           })
       screenshot_bytes = await page.screenshot(full_page=True)
       screenshot_base64 = base64.b64encode(screenshot_bytes).decode("utf-8")
       await browser.close()
   return {
       "findings": findings,
       "screenshot": screenshot_base64
   }