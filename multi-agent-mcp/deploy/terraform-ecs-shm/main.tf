locals {
  container_name = var.name_prefix
}

data "aws_caller_identity" "current" {}

data "aws_ecs_cluster" "hackathon_cluster" {
  cluster_name = var.ecs_cluster_name
}

# -----------------------------------------------------------------------------
# ACM (DNS validation in hosted zone used by Splunk/infra tooling)
# -----------------------------------------------------------------------------
resource "aws_acm_certificate" "load_balancer" {
  domain_name       = var.domain_name
  validation_method = "DNS"

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_route53_record" "load_balancer_validation" {
  for_each = {
    for dvo in aws_acm_certificate.load_balancer.domain_validation_options : dvo.domain_name => {
      name   = dvo.resource_record_name
      record = dvo.resource_record_value
      type   = dvo.resource_record_type
    }
  }

  allow_overwrite = true
  name            = each.value.name
  records         = [each.value.record]
  ttl             = 300
  type            = each.value.type
  zone_id         = var.route53_validation_zone_id
}

resource "aws_acm_certificate_validation" "load_balancer_validation_wait" {
  certificate_arn = aws_acm_certificate.load_balancer.arn
  validation_record_fqdns = [
    for r in aws_route53_record.load_balancer_validation : r.fqdn
  ]
}

# -----------------------------------------------------------------------------
# Logs + ECR
# -----------------------------------------------------------------------------
resource "aws_cloudwatch_log_group" "log_group" {
  name              = "/ecs/${var.name_prefix}"
  retention_in_days = var.log_retention_days

  tags = {
    Name      = var.name_prefix
    ManagedBy = "Terraform"
  }
}

resource "aws_ecr_repository" "ecr_repository" {
  name                 = var.name_prefix
  force_delete         = true
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = {
    Name      = var.name_prefix
    ManagedBy = "Terraform"
  }
}

# Push imagen a ECR antes del apply (docker/skopeo en CI o a mano). Ver README.

# -----------------------------------------------------------------------------
# IAM — ECS task execution (ECR pull + CloudWatch Logs)
# -----------------------------------------------------------------------------
resource "aws_iam_role" "task_execution_role" {
  name = "${var.name_prefix}-execution-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
    }]
  })

  tags = {
    Name      = "${var.name_prefix}-execution-role"
    ManagedBy = "Terraform"
  }
}

resource "aws_iam_role_policy_attachment" "task_execution_policy" {
  role       = aws_iam_role.task_execution_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

# -----------------------------------------------------------------------------
# Security groups — ALB accepts 443; tasks accept traffic only from ALB
# -----------------------------------------------------------------------------
resource "aws_security_group" "load_balancer_sg" {
  name        = "${var.name_prefix}-alb-sg"
  description = "Security group for ${var.name_prefix} ALB"
  vpc_id      = var.vpc_id

  ingress {
    description = "HTTPS"
    from_port     = 443
    to_port       = 443
    protocol      = "tcp"
    cidr_blocks   = ["0.0.0.0/0"]
    ipv6_cidr_blocks = ["::/0"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
    ipv6_cidr_blocks = ["::/0"]
  }
}

resource "aws_security_group" "security_group" {
  name        = "${var.name_prefix}-sg"
  description = "Security group for ${var.name_prefix} tasks"
  vpc_id      = var.vpc_id

  ingress {
    description      = "App port from ALB"
    from_port        = var.container_port
    to_port          = var.container_port
    protocol         = "tcp"
    security_groups  = [aws_security_group.load_balancer_sg.id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
    ipv6_cidr_blocks = ["::/0"]
  }

  tags = {
    Name      = "${var.name_prefix}-sg"
    ManagedBy = "Terraform"
  }
}

# -----------------------------------------------------------------------------
# Target group (created before ALB; attached via listener)
# -----------------------------------------------------------------------------
resource "aws_lb_target_group" "load_balancer_tg" {
  name        = "${var.name_prefix}-alb-tg"
  port        = var.container_port
  protocol    = "HTTP"
  vpc_id      = var.vpc_id
  target_type = "ip"

  deregistration_delay = "300"

  health_check {
    enabled             = true
    healthy_threshold   = 2
    unhealthy_threshold = 3
    timeout             = 5
    interval            = 30
    path                = "/"
    protocol            = "HTTP"
    matcher             = "200-299"
    port                = "traffic-port"
  }
}

# -----------------------------------------------------------------------------
# Application Load Balancer (requires quota available in account/region)
# -----------------------------------------------------------------------------
resource "aws_lb" "load_balancer_lb" {
  name                       = "${var.name_prefix}-alb"
  internal                   = var.internal_alb
  load_balancer_type         = "application"
  security_groups            = [aws_security_group.load_balancer_sg.id]
  subnets                    = var.private_subnet_ids
  enable_deletion_protection = false
  idle_timeout               = var.alb_idle_timeout
}

resource "aws_lb_listener" "load_balancer_listener" {
  load_balancer_arn = aws_lb.load_balancer_lb.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = aws_acm_certificate.load_balancer.arn

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.load_balancer_tg.arn
  }

  depends_on = [
    aws_acm_certificate_validation.load_balancer_validation_wait,
    aws_lb.load_balancer_lb,
    aws_lb_target_group.load_balancer_tg,
  ]
}

resource "aws_route53_record" "route53_record" {
  zone_id = var.route53_alias_zone_id
  name    = var.domain_name
  type    = "A"

  alias {
    name                   = aws_lb.load_balancer_lb.dns_name
    zone_id                = aws_lb.load_balancer_lb.zone_id
    evaluate_target_health = true
  }
}

# -----------------------------------------------------------------------------
# ECS task + service (service after listener so TG is associated to an LB)
# -----------------------------------------------------------------------------
resource "aws_ecs_task_definition" "task_definition" {
  family                   = var.name_prefix
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.task_cpu
  memory                   = var.task_memory
  execution_role_arn       = aws_iam_role.task_execution_role.arn

  runtime_platform {
    cpu_architecture        = "ARM64"
    operating_system_family = "LINUX"
  }

  container_definitions = jsonencode([
    {
      name      = local.container_name
      image     = "${aws_ecr_repository.ecr_repository.repository_url}:${var.ecr_image_tag}"
      essential = true
      portMappings = [
        {
          containerPort = var.container_port
          protocol      = "tcp"
        }
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.log_group.name
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "ecs"
        }
      }
    }
  ])

  tags = {
    Name      = var.name_prefix
    ManagedBy = "Terraform"
  }

  depends_on = [
    aws_ecr_repository.ecr_repository,
    aws_cloudwatch_log_group.log_group,
    aws_iam_role_policy_attachment.task_execution_policy,
  ]
}

resource "aws_ecs_service" "ecs_service" {
  name            = "${var.name_prefix}-service"
  cluster         = data.aws_ecs_cluster.hackathon_cluster.id
  task_definition = aws_ecs_task_definition.task_definition.arn
  desired_count   = var.desired_count
  launch_type     = "FARGATE"

  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200

  network_configuration {
    subnets          = var.private_subnet_ids
    security_groups  = [aws_security_group.security_group.id]
    assign_public_ip = false
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.load_balancer_tg.arn
    container_name   = local.container_name
    container_port   = var.container_port
  }

  depends_on = [aws_lb_listener.load_balancer_listener]

  tags = {
    Name      = "${var.name_prefix}-service"
    ManagedBy = "Terraform"
  }
}
