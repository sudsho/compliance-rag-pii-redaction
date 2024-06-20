resource "aws_kms_key" "policies" {
  description             = "kms key for compliance-rag policy docs bucket"
  deletion_window_in_days = 30
  enable_key_rotation     = true
  tags = local.common_tags
}

resource "aws_s3_bucket" "policies" {
  bucket        = "compliance-rag-policies-${var.env}-${data.aws_caller_identity.current.account_id}"
  force_destroy = false
  tags = local.common_tags
}

resource "aws_s3_bucket_versioning" "policies" {
  bucket = aws_s3_bucket.policies.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "policies" {
  bucket = aws_s3_bucket.policies.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.policies.arn
    }
  }
}

resource "aws_s3_bucket_public_access_block" "policies" {
  bucket                  = aws_s3_bucket.policies.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}
