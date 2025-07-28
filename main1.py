from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
import boto3
import threading
import os
from dotenv import load_dotenv
from openai import OpenAI  # ✅ New OpenAI import

# Load env vars
load_dotenv()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))  # ✅ New OpenAI client instance

app = FastAPI()

# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# For rendering templates
templates = Jinja2Templates(directory="templates")

# Shared status
operation_status = {
    "status": "✅ No operations in progress.",
    "in_progress": False
}

@app.get("/", response_class=HTMLResponse)
async def chat_ui(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.post("/chat")
async def chat(request: Request):
    data = await request.json()
    user_input = data.get("message", "").lower()

    if "hi" in user_input or "hello" in user_input:
        return {"response": "👋 Hello! I’m **Terraform-Agent**. How can I assist you today?"}

    elif "account" in user_input and "detail" in user_input:
        return {"response": get_account_details()}

    elif "region" in user_input:
        return {"response": get_total_regions()}

    elif "total instance" in user_input:
        return {"response": get_total_instances()}

    elif "create ec2" in user_input:
        region = get_region_from_input(user_input)
        if operation_status["in_progress"]:
            return {"response": "⚠️ Another operation is already in progress. Please wait."}
        thread = threading.Thread(target=create_ec2_instance, args=(region,))
        thread.start()
        return {"response": f"🚀 Creating EC2 instance in **{region}**. Please wait..."}

    elif "terminate ec2" in user_input or "destroy ec2" in user_input:
        region = get_region_from_input(user_input)
        if operation_status["in_progress"]:
            return {"response": "⚠️ Another operation is already in progress. Please wait."}
        thread = threading.Thread(target=terminate_ec2_instance, args=(region,))
        thread.start()
        return {"response": f"💣 Terminating EC2 instance(s) in **{region}**. Please wait..."}

    elif "status" in user_input:
        return {"response": operation_status["status"]}

    else:
        gpt_reply = gpt_nlp_response(user_input)
        return {"response": f"🤖 GPT Assist: {gpt_reply}"}


def get_account_details():
    try:
        sts = boto3.client("sts")
        identity = sts.get_caller_identity()
        return f"👤 **Account ID:** {identity['Account']}\n🔗 **ARN:** {identity['Arn']}"
    except Exception as e:
        return f"❌ Unable to retrieve account details: {str(e)}"


def get_total_regions():
    try:
        ec2 = boto3.client("ec2")
        regions = ec2.describe_regions()
        names = [r['RegionName'] for r in regions['Regions']]
        return f"🌍 Available AWS Regions:\n\n" + "\n".join([f"• {name}" for name in names])
    except Exception as e:
        return f"❌ Unable to fetch regions: {str(e)}"


def get_total_instances():
    try:
        ec2 = boto3.resource("ec2", region_name="us-east-1")
        instances = list(ec2.instances.all())
        return f"📦 You have **{len(instances)}** EC2 instance(s) in us-east-1."
    except Exception as e:
        return f"❌ Unable to fetch instances: {str(e)}"


def create_ec2_instance(region):
    try:
        operation_status["in_progress"] = True
        operation_status["status"] = f"🛠️ Creating EC2 instance in {region}..."

        ec2 = boto3.resource("ec2", region_name=region)
        instance = ec2.create_instances(
            ImageId="ami-0c02fb55956c7d316",
            MinCount=1,
            MaxCount=1,
            InstanceType="t2.micro",
            TagSpecifications=[{
                'ResourceType': 'instance',
                'Tags': [{'Key': 'Name', 'Value': 'Terraform-Agent-Instance'}]
            }]
        )[0]

        operation_status["status"] = "⏳ Launching instance... Please wait."
        instance.wait_until_running()
        instance.reload()

        operation_status["status"] = f"✅ EC2 Instance **{instance.id}** is running in {region}."
    except Exception as e:
        operation_status["status"] = f"❌ Failed to create instance: {str(e)}"
    finally:
        operation_status["in_progress"] = False


def terminate_ec2_instance(region):
    try:
        operation_status["in_progress"] = True
        operation_status["status"] = f"🧨 Looking for instances to terminate in {region}..."

        ec2 = boto3.resource("ec2", region_name=region)
        instances = ec2.instances.filter(
            Filters=[
                {'Name': 'tag:Name', 'Values': ['Terraform-Agent-Instance']},
                {'Name': 'instance-state-name', 'Values': ['running', 'pending']}
            ]
        )
        to_terminate = [i.id for i in instances]

        if not to_terminate:
            operation_status["status"] = "ℹ️ No matching EC2 instances found to terminate."
            return

        ec2.instances.filter(InstanceIds=to_terminate).terminate()
        operation_status["status"] = f"🛑 Terminating instance(s): {', '.join(to_terminate)}..."

        waiter = boto3.client("ec2", region_name=region).get_waiter('instance_terminated')
        waiter.wait(InstanceIds=to_terminate)

        operation_status["status"] = "✅ All matching EC2 instances terminated successfully."

    except Exception as e:
        operation_status["status"] = f"❌ Termination failed: {str(e)}"
    finally:
        operation_status["in_progress"] = False


def get_region_from_input(user_input: str):
    region = "us-east-1"
    if "mumbai" in user_input or "india" in user_input:
        region = "ap-south-1"
    elif "singapore" in user_input:
        region = "ap-southeast-1"
    elif "frankfurt" in user_input:
        region = "eu-central-1"
    return region


# ✅ Updated GPT function using openai>=1.0.0
def gpt_nlp_response(message: str) -> str:
    try:
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "You are a helpful AI Terraform Assistant for AWS operations."},
                {"role": "user", "content": message}
            ],
            temperature=0.5,
            max_tokens=100
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        return f"⚠️ GPT error: {str(e)}"

