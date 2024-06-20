module "vpc" {
  source  = "terraform-aws-modules/vpc/aws"
  version = "~> 5.7"

  name = "compliance-rag-${var.env}"
  cidr = var.vpc_cidr
  azs             = ["${var.region}a", "${var.region}b", "${var.region}c"]
  private_subnets = ["10.42.1.0/24", "10.42.2.0/24", "10.42.3.0/24"]
  public_subnets  = ["10.42.101.0/24", "10.42.102.0/24"]

  enable_nat_gateway     = false          # everything private, use VPC endpoints
  enable_dns_hostnames   = true
  enable_flow_log        = true
  create_flow_log_cloudwatch_log_group = true
  create_flow_log_cloudwatch_iam_role  = true

  tags = local.common_tags
}

# Private endpoint for Bedrock runtime; keeps model traffic off the public internet
resource "aws_vpc_endpoint" "bedrock_runtime" {
  vpc_id             = module.vpc.vpc_id
  service_name       = "com.amazonaws.${var.region}.bedrock-runtime"
  vpc_endpoint_type  = "Interface"
  subnet_ids         = module.vpc.private_subnets
  security_group_ids = [aws_security_group.endpoints.id]
  private_dns_enabled = true
  tags = local.common_tags
}

# Private endpoint for Bedrock control plane (guardrail management, model listing)
resource "aws_vpc_endpoint" "bedrock" {
  vpc_id             = module.vpc.vpc_id
  service_name       = "com.amazonaws.${var.region}.bedrock"
  vpc_endpoint_type  = "Interface"
  subnet_ids         = module.vpc.private_subnets
  security_group_ids = [aws_security_group.endpoints.id]
  private_dns_enabled = true
  tags = local.common_tags
}

# S3 gateway endpoint for policy doc uploads (never over public internet)
resource "aws_vpc_endpoint" "s3" {
  vpc_id            = module.vpc.vpc_id
  service_name      = "com.amazonaws.${var.region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = module.vpc.private_route_table_ids
  tags = local.common_tags
}

resource "aws_security_group" "endpoints" {
  name        = "compliance-rag-endpoints-${var.env}"
  vpc_id      = module.vpc.vpc_id
  description = "HTTPS from VPC to interface endpoints"
  ingress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = [var.vpc_cidr]
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
  tags = local.common_tags
}
