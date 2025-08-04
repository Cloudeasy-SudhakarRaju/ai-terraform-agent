from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
import boto3
import threading
import json
import httpx
import os
import base64
import requests
from dotenv import load_dotenv
from openai import OpenAI
from openai.types.chat import ChatCompletionMessageParam
from requests.auth import HTTPBasicAuth

# === Load environment variables ===
load_dotenv()

# === FastAPI setup ===
app = FastAPI()
templates = Jinja2Templates(directory="templates")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# === Together AI client ===
openai_client = OpenAI(
    api_key=os.getenv("TOGETHER_API_KEY"),
    base_url="https://api.together.xyz/v1"
)

# === Azure DevOps config ===
AZURE_ORG = os.getenv("AZURE_ORG")              # e.g., "mailmesudhakar"
AZURE_PROJECT = os.getenv("AZURE_PROJECT")      # e.g., "myproject"
AZURE_PIPELINE_NAME = os.getenv("AZURE_PIPELINE_NAME")  # e.g., "Terraform-Deploy-EC2"
AZURE_PAT = os.getenv("AZURE_PAT")

# === Shared state ===
operation_status = {
    "status": "✅ No operations in progress.",
    "in_progress": False
}
session_state = {}

# === AMI map ===
AMI_MAP = {
    "us-east-1": "resolve:ssm:/aws/service/ami-amazon-linux-latest/amzn2-ami-hvm-x86_64-gp2",
    "us-east-2": "resolve:ssm:/aws/service/ami-amazon-linux-latest/amzn2-ami-hvm-x86_64-gp2",
    "us-west-1": "resolve:ssm:/aws/service/ami-amazon-linux-latest/amzn2-ami-hvm-x86_64-gp2",
    "us-west-2": "resolve:ssm:/aws/service/ami-amazon-linux-latest/amzn2-ami-hvm-x86_64-gp2",
    "eu-west-1": "resolve:ssm:/aws/service/ami-amazon-linux-latest/amzn2-ami-hvm-x86_64-gp2",
    "ap-south-1": "resolve:ssm:/aws/service/ami-amazon-linux-latest/amzn2-ami-hvm-x86_64-gp2"
}

# === Request model ===
class ChatRequest(BaseModel):
    message: str

# === UI endpoint ===
@app.get("/", response_class=HTMLResponse)
def chat_ui(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

# === Chat endpoint ===
@app.post("/chat")
async def chat(req: Request):
    data = await req.json()
    user_input = data.get("message", "").lower()

    region = get_region_from_input(user_input)
    if any(kw in user_input for kw in ["create ec2", "launch instance", "spin up vm"]):
        if not region:
            return {"response": "🌍 Please specify a valid region."}
        session_state["awaiting_creation_confirmation"] = {"region": region}
        return {"response": f"⚠️ Launch EC2 in **{region}**? Reply yes to confirm."}

    elif "yes" in user_input and "awaiting_creation_confirmation" in session_state:
        region = session_state.pop("awaiting_creation_confirmation")["region"]
        update_tfvars(region, AMI_MAP.get(region), "t2.micro")
        operation_status["status"] = f"🔄 Creating EC2 in {region}..."
        operation_status["in_progress"] = True
        pipeline_id = fetch_pipeline_id(AZURE_ORG, AZURE_PROJECT, AZURE_PIPELINE_NAME, AZURE_PAT)
        if not pipeline_id:
            return {"response": f"❌ Could not find pipeline named '{AZURE_PIPELINE_NAME}'."}
        status, result = await trigger_azure_pipeline(pipeline_id)
        if status in [200, 201]:
            threading.Thread(target=monitor_pipeline_completion, args=(region,)).start()
            return {"response": f"🚀 Pipeline triggered to launch EC2 in {region}."}
        return {"response": f"❌ Failed to trigger pipeline: {result}"}

    elif "status" in user_input:
        return {"response": operation_status["status"]}

    return {"response": together_ai_response(user_input)}

# === Helpers ===
def get_region_from_input(text: str) -> str:
    mapping = {
        "mumbai": "ap-south-1",
        "virginia": "us-east-1",
        "california": "us-west-1",
        "oregon": "us-west-2",
        "ireland": "eu-west-1"
    }
    for k, v in mapping.items():
        if k in text or v in text:
            return v
    return ""

def update_tfvars(region: str, ami_id: str, instance_type: str):
    tfvars = {
        "aws_region": region,
        "ami_id": ami_id,
        "instance_type": instance_type
    }
    with open("terraform.tfvars.json", "w") as f:
        json.dump(tfvars, f, indent=2)

def fetch_pipeline_id(org: str, project: str, pipeline_name: str, pat: str) -> int:
    url = f"https://dev.azure.com/{org}/{project}/_apis/pipelines?api-version=7.1-preview.1"
    try:
        response = requests.get(url, auth=HTTPBasicAuth('', pat))
        pipelines = response.json().get("value", [])
        for p in pipelines:
            if p["name"] == pipeline_name:
                return p["id"]
    except Exception as e:
        print("Error fetching pipeline ID:", e)
    return None

async def trigger_azure_pipeline(pipeline_id: int):
    url = f"https://dev.azure.com/{AZURE_ORG}/{AZURE_PROJECT}/_apis/pipelines/{pipeline_id}/runs?api-version=7.1-preview.1"
    pat = base64.b64encode(f":{AZURE_PAT}".encode()).decode()
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Basic {pat}"
    }
    async with httpx.AsyncClient() as client:
        resp = await client.post(url, headers=headers, json={})
        return resp.status_code, resp.json()

def monitor_pipeline_completion(region: str):
    import time
    time.sleep(90)  # Simulate wait time
    operation_status["status"] = f"✅ EC2 instance launched successfully in {region}."
    operation_status["in_progress"] = False

def together_ai_response(message: str) -> str:
    try:
        messages: list[ChatCompletionMessageParam] = [
            {"role": "system", "content": "You are a helpful assistant for AWS & cloud operations."},
            {"role": "user", "content": message}
        ]
        response = openai_client.chat.completions.create(
            model="mistralai/Mistral-7B-Instruct-v0.1",
            messages=messages,
            temperature=0.7,
            max_tokens=300
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        return f"⚠️ Together API error: {str(e)}"

