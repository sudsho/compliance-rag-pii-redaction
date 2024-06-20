# least-privilege IAM for the compliance rag service
# principle: allow InvokeModel + ApplyGuardrail on a specific model + guardrail id,
# nothing else in bedrock; read-only on the policy S3 prefix; write on the audit RDS.

data "aws_caller_identity" "current" {}

variable "bedrock_model_arn" {
  type    = string
  default = "arn:aws:bedrock:us-east-1::foundation-model/anthropic.claude-3-5-sonnet-20240620-v1:0"
}

variable "bedrock_titan_embed_arn" {
  type    = string
  default = "arn:aws:bedrock:us-east-1::foundation-model/amazon.titan-embed-text-v2:0"
}

variable "guardrail_arn" {
  type    = string
  default = ""   # set post-guardrail-create
}

resource "aws_iam_role" "app" {
  name = "compliance-rag-app-${var.env}"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ec2.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
  tags = local.common_tags
}

resource "aws_iam_role_policy" "bedrock" {
  name = "bedrock-invoke-model-and-guardrail"
  role = aws_iam_role.app.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "InvokeAllowedModelsOnly"
        Effect = "Allow"
        Action = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"]
        Resource = [
          var.bedrock_model_arn,
          var.bedrock_titan_embed_arn,
        ]
      },
      {
        Sid    = "ApplyGuardrailAllowedOnly"
        Effect = "Allow"
        Action = ["bedrock:ApplyGuardrail"]
        Resource = var.guardrail_arn != "" ? [var.guardrail_arn] : ["*"]
        # in prod always set var.guardrail_arn; the "*" is only for the very first bootstrap
      },
      {
        Sid    = "DenyModelInvokeOnOtherModels"
        Effect = "Deny"
        Action = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"]
        NotResource = [
          var.bedrock_model_arn,
          var.bedrock_titan_embed_arn,
        ]
      }
    ]
  })
}

resource "aws_iam_role_policy" "policy_docs_s3" {
  name = "policy-docs-readonly"
  role = aws_iam_role.app.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = ["s3:GetObject", "s3:ListBucket"]
      Resource = [
        aws_s3_bucket.policies.arn,
        "${aws_s3_bucket.policies.arn}/*",
      ]
    }]
  })
}

resource "aws_iam_instance_profile" "app" {
  name = "compliance-rag-app-${var.env}"
  role = aws_iam_role.app.name
}
