variable "aws_region" {
  type        = string
  description = "AWS region"
  default     = "us-west-2"
}

variable "name_prefix" {
  type        = string
  description = "Resource name prefix (e.g. shm-new-001)"
  default     = "shm-new-001"
}

variable "domain_name" {
  type        = string
  description = "FQDN for ACM + Route53 alias"
  default     = "shm-new-001.arlocloud.com"
}

variable "ecs_cluster_name" {
  type        = string
  description = "Existing ECS cluster name"
  default     = "hackathon"
}

variable "vpc_id" {
  type        = string
  description = "VPC for ALB, TG, ECS service networking"
  default     = "vpc-0b90847fa69e0d603"
}

variable "private_subnet_ids" {
  type        = list(string)
  description = "Private subnets for ALB + ECS (must span AZs used by TG)"
  default = [
    "subnet-007d16c0fd078abcb",
    "subnet-04c5d49ad3061fda3",
  ]
}

variable "route53_validation_zone_id" {
  type        = string
  description = "Hosted zone ID for ACM DNS validation records"
  default     = "Z24LMZ04CEZXJ5"
}

variable "route53_alias_zone_id" {
  type        = string
  description = "Hosted zone ID for app A/ALIAS record"
  default     = "Z6K5AEJJGGGLV"
}

variable "container_port" {
  type        = number
  description = "Container listen port (must match TG + ECS mapping)"
  default     = 8000
}

variable "desired_count" {
  type        = number
  default     = 1
}

variable "task_cpu" {
  type        = number
  description = "Fargate task CPU units"
  default     = 2048
}

variable "task_memory" {
  type        = number
  description = "Fargate task memory (MiB)"
  default     = 4096
}

variable "alb_idle_timeout" {
  type        = number
  description = "ALB idle timeout seconds (raise for long HTTP requests e.g. 600)"
  default     = 900
}

variable "log_retention_days" {
  type        = number
  default     = 7
}

variable "ecr_image_tag" {
  type        = string
  description = "Image tag deployed to ECR before ECS task runs"
  default     = "latest"
}

variable "internal_alb" {
  type        = bool
  description = "Internal-only ALB (matches original plan)"
  default     = true
}
