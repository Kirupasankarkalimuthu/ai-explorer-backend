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
   print("\n===== AI ANALYZING PAGE =====")
   print("URL:", data.url)
   prompt = f"""
You are an expert QA test automation planner.
Given this webpage DOM (partial) and network logs,
create a short exploration test plan.
Return steps in numbered format.
DOM:
{data.dom[:3000]}
Network Logs:
{data.networkLogs[-5:]}
"""
   response = client.chat.completions.create(
       model="gpt-4.1-mini",
       messages=[
           {"role": "system", "content": "You are a senior QA automation engineer."},
           {"role": "user", "content": prompt}
       ]
   )
   plan = response.choices[0].message.content
   print("\n===== AI PLAN =====")
   print(plan)
   return {
       "message": "AI Plan Generated",
       "plan": plan
   }
@app.get("/")
def root():
   return {"status": "AI Explorer Backend Running"}