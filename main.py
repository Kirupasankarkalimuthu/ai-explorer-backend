import json
import base64
from fastapi import FastAPI
from pydantic import BaseModel
from playwright.async_api import async_playwright # type: ignore
app = FastAPI()

class ExploreRequest(BaseModel):
   url: str

@app.post("/explore")
async def explore(request: ExploreRequest):
   findings = []
   async with async_playwright() as p:
       browser = await p.chromium.launch(headless=True)
       page = await browser.new_page()
       await page.goto(request.url)
       await page.wait_for_load_state("networkidle")
       initial_url = page.url
       # Capture interactive elements safely
       elements = await page.evaluate("""
       () => {
           const items = [];
           document.querySelectorAll("a, button, input[type='submit']").forEach(el => {
               const text = el.innerText || el.value || "";
               items.push({
                   id: el.id || null,
                   text: text.trim(),
                   tag: el.tagName
               });
           });
           return items;
       }
       """)
       # Select first 6 clickable elements
       elements = elements[:6]
       for el in elements:
           action_desc = ""
           try:
               if el["id"]:
                   selector = f"#{el['id']}"
                   action_desc = f"Click ID: {el['id']}"
                   await page.click(selector, timeout=5000)
               elif el["text"]:
                   action_desc = f"Click TEXT: {el['text']}"
                   await page.get_by_text(el["text"], exact=False).first.click(timeout=5000)
               else:
                   continue
               await page.wait_for_timeout(1000)
               # Detect URL change
               if page.url != initial_url:
                   result = "Navigation occurred"
                   severity = "Info"
                   await page.go_back()
                   await page.wait_for_load_state("networkidle")
               else:
                   result = "No navigation occurred"
                   severity = "Minor"
           except Exception as e:
               result = f"Click failed: {str(e)}"
               severity = "Major"
           findings.append({
               "action": action_desc,
               "selector": el["id"] if el["id"] else el["text"],
               "result": result,
               "severity": severity
           })
       # Capture screenshot
       screenshot_bytes = await page.screenshot()
       screenshot_base64 = base64.b64encode(screenshot_bytes).decode()
       await browser.close()
   return {
       "findings": findings,
       "screenshot": screenshot_base64
   }