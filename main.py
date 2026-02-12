from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Dict
from openai import OpenAI
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
You are a senior QA automation engineer.
Analyze the webpage DOM and generate:
1. A short human-readable exploratory test plan (max 10 points).
2. Structured automation steps in JSON format.
Return strictly in this JSON format:
{{
 "summary": "short title",
 "test_cases": ["point1", "point2"],
 "automation_steps": [
   {{"action": "click", "selector": "css_selector"}},
   {{"action": "type", "selector": "css_selector", "value": "text"}}
 ]
}}
DOM:
{data.dom[:3000]}
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
   return structured_output
@app.get("/")
def root():
   return {"status": "AI Explorer Backend Running"}