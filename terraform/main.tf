provider "aws" {
  region = var.aws_region
}

resource "aws_instance" "chatops_ec2" {
  ami           = var.ami_id
  instance_type = var.instance_type

  tags = {
    Name = "ChatOpsEC2"
  }
}

