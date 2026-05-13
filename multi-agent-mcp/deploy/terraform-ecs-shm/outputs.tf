output "application_url" {
  description = "HTTPS URL for the app"
  value       = "https://${var.domain_name}"
}

output "ecr_repository_url" {
  value = aws_ecr_repository.ecr_repository.repository_url
}

output "alb_dns_name" {
  value = aws_lb.load_balancer_lb.dns_name
}

output "ecs_service_name" {
  value = aws_ecs_service.ecs_service.name
}

output "target_group_arn" {
  value = aws_lb_target_group.load_balancer_tg.arn
}
