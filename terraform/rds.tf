resource "aws_kms_key" "audit_db" {
  description             = "kms key for compliance-rag audit rds"
  deletion_window_in_days = 30
  enable_key_rotation     = true
  tags = local.common_tags
}

resource "aws_db_subnet_group" "audit" {
  name       = "compliance-rag-audit-${var.env}"
  subnet_ids = module.vpc.private_subnets
  tags       = local.common_tags
}

resource "aws_security_group" "audit_db" {
  name        = "compliance-rag-audit-${var.env}"
  vpc_id      = module.vpc.vpc_id
  description = "Postgres 5432 from app SG only"

  ingress {
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.app.id]
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
  tags = local.common_tags
}

resource "aws_security_group" "app" {
  name   = "compliance-rag-app-${var.env}"
  vpc_id = module.vpc.vpc_id
  tags   = local.common_tags
}

resource "random_password" "audit_master" {
  length  = 32
  special = false
}

resource "aws_secretsmanager_secret" "audit_master" {
  name       = "compliance-rag/audit/master"
  kms_key_id = aws_kms_key.audit_db.arn
  tags       = local.common_tags
}

resource "aws_secretsmanager_secret_version" "audit_master" {
  secret_id     = aws_secretsmanager_secret.audit_master.id
  secret_string = jsonencode({ username = "auditmaster", password = random_password.audit_master.result })
}

resource "aws_db_instance" "audit" {
  identifier                     = "compliance-rag-audit-${var.env}"
  engine                         = "postgres"
  engine_version                 = "16.3"
  instance_class                 = "db.t4g.medium"
  allocated_storage              = 100
  max_allocated_storage          = 500
  storage_type                   = "gp3"
  storage_encrypted              = true
  kms_key_id                     = aws_kms_key.audit_db.arn
  username                       = "auditmaster"
  password                       = random_password.audit_master.result
  db_name                        = "audit"
  db_subnet_group_name           = aws_db_subnet_group.audit.name
  vpc_security_group_ids         = [aws_security_group.audit_db.id]
  publicly_accessible            = false
  multi_az                       = true
  backup_retention_period        = 35
  delete_automated_backups       = false
  deletion_protection            = true
  performance_insights_enabled   = true
  performance_insights_kms_key_id = aws_kms_key.audit_db.arn
  iam_database_authentication_enabled = true
  enabled_cloudwatch_logs_exports = ["postgresql", "upgrade"]
  auto_minor_version_upgrade      = true
  monitoring_interval             = 60
  copy_tags_to_snapshot           = true

  tags = merge(local.common_tags, {
    backup_policy   = "35d-pitr"
    retention_class = "audit-7yr"
  })
}

output "audit_endpoint" {
  value     = aws_db_instance.audit.address
  sensitive = true
}
