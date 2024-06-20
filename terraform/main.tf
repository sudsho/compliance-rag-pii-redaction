terraform {
  required_version = ">= 1.6"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.50"
    }
  }
}

provider "aws" {
  region = var.region
}

# canonical tags for cost + compliance reporting
locals {
  common_tags = {
    app          = "compliance-rag"
    env          = var.env
    hipaa_scoped = "true"
    data_class   = "phi"
    owner        = var.owner
  }
}

variable "region"  { type = string  default = "us-east-1" }
variable "env"     { type = string  default = "prod" }
variable "owner"   { type = string  default = "ai-platform" }
variable "vpc_cidr" { type = string default = "10.42.0.0/16" }
