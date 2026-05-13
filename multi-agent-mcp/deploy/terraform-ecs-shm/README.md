# ECS Fargate + internal ALB — plantilla Terraform

Generada a partir del plan que falló por **cuota de ALB** y por **orden TG vs ECS**. Esta versión:

- Crea **ACM** + validación DNS + ** listener HTTPS** antes del **ECS service** (`depends_on` en el listener).
- Restringe el SG de tareas al SG del **ALB** (mejor que `0.0.0.0/0` en el puerto de la app).
- Variables para **VPC**, subnets, zonas Route53 y prefijo de nombre (por defecto coincide con `shm-new-001`).

## Requisitos

- Terraform ≥ 1.5
- AWS CLI configurado
- Cluster ECS existente (`hackathon` por defecto)
- Cuota libre para **Application Load Balancers** en la región
- Imagen **linux/arm64** si usas `runtime_platform` ARM64 como en el plan original

## Backend S3

Copia `backend.tf.example` → `backend.tf` con tu bucket.

## Comandos

```bash
cd deploy/terraform-ecs-shm
terraform init
terraform plan
terraform apply
```

## Imagen en ECR

Antes de que el servicio ECS arranque, sube la imagen al repo que crea Terraform (mismo patrón que Harness con `skopeo copy` / `docker push`):

```bash
aws ecr get-login-password --region us-west-2 | docker login --username AWS --password-stdin <account>.dkr.ecr.us-west-2.amazonaws.com
docker tag tu-imagen:tag $(terraform output -raw ecr_repository_url):latest
docker push $(terraform output -raw ecr_repository_url):latest
```

## Health check

El target group usa `path = "/"`. Si tu app solo responde en `/api/health`, cambia `health_check { path = "/api/health" }` en `aws_lb_target_group.load_balancer_tg`.

## Timeouts largos (Status Monitor)

Sube `alb_idle_timeout` (p. ej. `600`) y alinea `GUNICORN_TIMEOUT` en la task (variables de entorno en la definición del contenedor — ampliar `container_definitions`).

## OneView GOC AI

La imagen del proyecto escucha **8080** por defecto; este stack usa **8000** como en tu plan. Ajusta `container_port` o el `EXPOSE`/`CMD` de la imagen para que coincidan.
