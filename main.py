from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
import boto3
import threading
import os
from dotenv import load_dotenv
from openai import OpenAI
from openai.types.chat import ChatCompletionMessageParam

# Load environment variables
load_dotenv()

client = OpenAI(
    api_key=os.getenv("TOGETHER_API_KEY"),
    base_url="https://api.together.xyz/v1"
)

app = FastAPI()

# CORS settings
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

templates = Jinja2Templates(directory="templates")

operation_status = {
    "status": "✅ No operations in progress.",
    "in_progress": False
}

session_state = {}

@app.get("/", response_class=HTMLResponse)
async def chat_ui(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.post("/chat")
async def chat(request: Request):
    data = await request.json()
    user_input = data.get("message", "").lower()

    if "awaiting_termination_confirmation" in session_state:
        details = session_state.pop("awaiting_termination_confirmation")
        instance_name = details["instance_name"]
        region = details["region"]
        if "yes" in user_input or "confirm" in user_input:
            thread = threading.Thread(target=terminate_ec2_instance, args=(region, instance_name))
            thread.start()
            return {"response": f"💣 Confirmed. Terminating **{instance_name}** in **{region}**. Please wait..."}
        else:
            return {"response": "❎ Termination cancelled."}

    if "awaiting_creation_confirmation" in session_state:
        details = session_state.pop("awaiting_creation_confirmation")
        region = details["region"]
        if "yes" in user_input or "confirm" in user_input:
            thread = threading.Thread(target=create_ec2_instance, args=(region,))
            thread.start()
            return {"response": f"🚀 Creating EC2 instance in **{region}**. Please wait..."}
        else:
            return {"response": "❎ EC2 creation cancelled."}

    if "hi" in user_input or "hello" in user_input:
        return {"response": "👋 Hello! I’m **Terraform-Agent**. How can I assist you today?"}

    elif "account" in user_input and "detail" in user_input:
        return {"response": get_account_details()}

    elif "region" in user_input:
        return {"response": get_total_regions()}

    elif "total instance" in user_input:
        region = get_region_from_input(user_input) or "us-east-1"
        return {"response": get_total_instances(region)}

    elif any(kw in user_input for kw in ["create ec2", "launch instance", "spin up vm", "create vm", "start server", "create server"]):
        region = get_region_from_input(user_input)
        if not region:
            return {"response": "🌍 Please specify the AWS region you want to launch the EC2 instance in (e.g., Mumbai, Singapore)."}
        if operation_status["in_progress"]:
            return {"response": "⚠️ Another operation is already in progress. Please wait."}
        session_state["awaiting_creation_confirmation"] = {"region": region}
        return {"response": f"⚠️ Do you want to launch an EC2 instance in **{region}**? Reply with **yes** to confirm or **no** to cancel."}

    elif any(kw in user_input for kw in ["terminate ec2", "destroy ec2", "remove ec2", "delete ec2", "terminate instance", "delete vm", "remove instance"]):
        region = get_region_from_input(user_input)
        instance_name = "Terraform-Agent-Instance"
        if not region:
            return {"response": "🌍 Please specify the region of the EC2 instance you want to terminate (e.g., Mumbai, Singapore)."}
        if operation_status["in_progress"]:
            return {"response": "⚠️ Another operation is already in progress. Please wait."}
        session_state["awaiting_termination_confirmation"] = {
            "region": region,
            "instance_name": instance_name
        }
        return {"response": f"⚠️ Are you sure you want to terminate **{instance_name}** in **{region}**? Reply with **yes** to confirm or **no** to cancel."}

    elif "status" in user_input:
        return {"response": operation_status["status"]}

    else:
        reply = together_ai_response(user_input)
        return {"response": f"🤖 AI Assist: {reply}"}


def together_ai_response(message: str) -> str:
    try:
        messages: list[ChatCompletionMessageParam] = [
            {"role": "system", "content": "You are a helpful assistant for AWS & cloud operations."},
            {"role": "user", "content": message}
        ]
        response = client.chat.completions.create(
            model="mistralai/Mistral-7B-Instruct-v0.1",
            messages=messages,
            temperature=0.7,
            max_tokens=300
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        return f"⚠️ Together API error: {str(e)}"


def get_account_details():
    try:
        sts = boto3.client("sts")
        identity = sts.get_caller_identity()
        return f"👤 **Account ID:** {identity['Account']}\n🔗 **ARN:** {identity['Arn']}"
    except Exception as e:
        return f"❌ Unable to retrieve account details: {str(e)}"


def get_total_regions():
    regions = [
        "ap-south-1", "eu-north-1", "eu-west-3", "eu-west-2", "eu-west-1",
        "ap-northeast-3", "ap-northeast-2", "ap-northeast-1", "ca-central-1",
        "sa-east-1", "ap-southeast-1", "ap-southeast-2", "eu-central-1",
        "us-east-1", "us-east-2", "us-west-1", "us-west-2"
    ]
    return "🌍 Available AWS Regions:\n\n" + "\n".join([f"• {r}" for r in regions])


def get_total_instances(region="us-east-1"):
    try:
        ec2 = boto3.resource("ec2", region_name=region)
        instances = list(ec2.instances.all())
        return f"📦 You have **{len(instances)}** EC2 instance(s) in **{region}**."
    except Exception as e:
        return f"❌ Unable to fetch instances in {region}: {str(e)}"


def create_ec2_instance(region):
    try:
        print(f"🔧 Creating EC2 in region: {region}")
        operation_status["in_progress"] = True
        operation_status["status"] = f"🚰 Creating EC2 instance in {region}..."

        session = boto3.session.Session(region_name=region)
        ec2 = session.resource("ec2")

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

        operation_status["status"] = (
            f"✅ EC2 Instance **{instance.id}** is running in **{region}**.\n"
            f"🔗 Public DNS: {instance.public_dns_name or 'N/A'}\n"
            f"🔒 Private IP: {instance.private_ip_address or 'N/A'}"
        )
    except Exception as e:
        operation_status["status"] = f"❌ Failed to create instance: {str(e)}"
    finally:
        operation_status["in_progress"] = False


def terminate_ec2_instance(region, instance_name):
    try:
        print(f"🔧 Terminating EC2 in region: {region}")
        operation_status["in_progress"] = True
        operation_status["status"] = f"🤸 Looking for instances named **{instance_name}** to terminate in **{region}**..."

        session = boto3.session.Session(region_name=region)
        ec2 = session.resource("ec2")

        instances = ec2.instances.filter(
            Filters=[
                {'Name': 'tag:Name', 'Values': [instance_name]},
                {'Name': 'instance-state-name', 'Values': ['running', 'pending']}
            ]
        )
        to_terminate = [i.id for i in instances]

        if not to_terminate:
            operation_status["status"] = f"ℹ️ No instance named **{instance_name}** found running in **{region}**."
            return

        instance_ids = ', '.join(to_terminate)
        operation_status["status"] = f"🐛 Destroying instance(s): **{instance_ids}** in **{region}**..."

        ec2.instances.filter(InstanceIds=to_terminate).terminate()
        ec2_client = session.client("ec2")
        ec2_client.get_waiter('instance_terminated').wait(InstanceIds=to_terminate)

        operation_status["status"] = f"✅ Instance(s) **{instance_ids}** successfully destroyed in **{region}**."
    except Exception as e:
        operation_status["status"] = f"❌ Termination failed: {str(e)}"
    finally:
        operation_status["in_progress"] = False


def get_region_from_input(user_input: str):
    region_map = {
        "mumbai": "ap-south-1",
        "india": "ap-south-1",
        "singapore": "ap-southeast-1",
        "frankfurt": "eu-central-1",
        "virginia": "us-east-1",
        "ohio": "us-east-2",
        "oregon": "us-west-2",
        "california": "us-west-1"
    }

    for keyword, region in region_map.items():
        if keyword in user_input:
            return region

    aws_regions = [
        "ap-south-1", "eu-north-1", "eu-west-3", "eu-west-2", "eu-west-1",
        "ap-northeast-3", "ap-northeast-2", "ap-northeast-1", "ca-central-1",
        "sa-east-1", "ap-southeast-1", "ap-southeast-2", "eu-central-1",
        "us-east-1", "us-east-2", "us-west-1", "us-west-2"
    ]

    for region in aws_regions:
        if region in user_input:
            return region

    return None

