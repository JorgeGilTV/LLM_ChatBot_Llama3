# OneView GOC AI - Docker Deployment Guide

## 📦 Archivo Docker

- **Imagen Docker** (ejemplo de release reciente): `oneview-goc-ai_v3.2.7-mcp.tar`
- **Tag**: `oneview-goc-ai:latest` y `oneview-goc-ai:3.2.7-mcp`
- **Base**: Python 3.12-slim
- **Plataforma**: `docker-build-export.sh` usa por defecto `--platform linux/arm64` (Mac Apple Silicon, mismo host). Para un `.tar` que corra en servidores Linux **x86_64** (EKS, VM típicas), genera con: `BUILD_PLATFORM=linux/amd64 ./docker-build-export.sh` (ver *Troubleshooting* si hay *wrong architecture*).

## 🚀 Instrucciones de Deployment

### 0. Subir el `.tar` a la EC2 (errores frecuentes)

Desde **~mediados de marzo 2026** el commit grande de producción dejó la imagen en **~200 MB** (`docker save`). Si “falla al subir”, suele ser el **canal**, no la imagen:

| Síntoma | Causa probable | Qué hacer |
|--------|----------------|-----------|
| `scp` corta / `Connection closed` | Timeout o red inestable | `rsync -avP --partial ./oneview-goc-ai_v*.tar user@ec2:~/` (reanuda) |
| `no space left on device` | Disco lleno en EC2 | `df -h`; borrar `.tar` viejos tras `docker load`; `docker system prune` |
| `unexpected EOF` al `docker load` | `.tar` incompleto (subida truncada) | Volver a subir; comprobar tamaño local vs remoto (`ls -lh`, `wc -c`) |
| `couldn't find env file` / Compose no arranca | Falta `./.env` junto al compose (Compose antiguo) | `touch .env` o `cp .env.example .env`, o usa `./old/scripts/ec2-compose-up.sh` |
| Compose rechaza `required: false` | Docker Compose anterior a 2.24 | Actualiza el plugin `docker compose` o usa `touch .env` junto al YAML |

Comprimir opcional: `gzip -k oneview-goc-ai_v3.2.7-mcp.tar` y subir el `.gz`; en el servidor: `gunzip -c archivo.tar.gz \| docker load`.

### 1. Cargar la Imagen Docker

```bash
docker load -i oneview-goc-ai_v3.2.7-mcp.tar
```

Verificar que se cargó correctamente:
```bash
docker images | grep oneview-goc-ai
```

### 2. Ejecutar el Contenedor

#### Opción A: Modo Simple (desarrollo/pruebas)
```bash
docker run -d \
  --name goc-ai \
  -p 8080:8080 \
  oneview-goc-ai:latest
```

La imagen **no** incluye `.env` (está en `.dockerignore`). La Opción A arranca la UI, pero **sin** `--env-file` ni variables `-e` no tendrás claves de APIs; el entrypoint escribirá un aviso en `docker logs`.

#### Opción B: EC2 / producción — `docker run` con credenciales (sin archivo dentro de la imagen)

Copia tu `.env` al servidor (por un canal seguro) y pásalo al demonio Docker **sin** meterlo en la capa de la imagen:

```bash
docker run -d \
  --name goc-ai \
  -p 8080:8080 \
  -v "$(pwd)/data:/app/data" \
  --env-file /ruta/en/ec2/.env \
  oneview-goc-ai:latest
```

O monta el mismo fichero donde `app.py` lo espera (`load_dotenv()` lee `/app/.env`):

```bash
docker run -d \
  --name goc-ai \
  -p 8080:8080 \
  -v "$(pwd)/data:/app/data" \
  -v /ruta/en/ec2/.env:/app/.env:ro \
  oneview-goc-ai:latest
```

Variables sueltas (nombres reales del proyecto — ver `.env.example`):

```bash
docker run -d \
  --name goc-ai \
  -p 8080:8080 \
  -v "$(pwd)/data:/app/data" \
  -e BEDROCK_API_KEY="ABSK..." \
  -e DATADOG_API_KEY="..." \
  -e DATADOG_APP_KEY="..." \
  -e PAGERDUTY_API_TOKEN="..." \
  -e SPLUNK_TOKEN="..." \
  oneview-goc-ai:latest
```

**EC2**: abre el **security group** entrante para el puerto **8080** (o el que mapees). Desde fuera: `http://IP_PUBLICA_EC2:8080`. En la instancia: `curl -s http://127.0.0.1:8080/api/health`.

#### Opción C: Imagen “lista” y credenciales fuera (sin reconstruir)

La imagen incluye **código y dependencias**, no claves ni webhooks. Eso es intencional: si las metiéramos en la capa de la imagen, cualquiera con el `.tar` podría extraerlas.

**Entrega típica a quien solo ejecuta Docker** (sin tocar el repo ni hacer `docker build`):

1. Archivo `oneview-goc-ai_v*.tar` (esta imagen).
2. Archivo `.env` con valores reales (canal seguro aparte del `.tar`).
3. En la misma carpeta: `docker-compose.prod.yml` (viene en el proyecto) y, si hace falta, `.env.example` como plantilla.

```bash
docker load -i oneview-goc-ai_v3.2.7-mcp.tar
cp .env.example .env   # editar: Slack, Datadog, Bedrock, etc.
mkdir -p logs data
docker compose -f docker-compose.prod.yml up -d
```

Si **no** puedes copiar un `.env` al servidor pero sí usas un orquestador (Kubernetes, ECS, OpenShift), define las mismas variables en el **panel de despliegue** o en **Secrets** del clúster: el contenedor solo necesita las variables en tiempo de ejecución, no un archivo en la imagen.

**Patrón alternativo** (archivo montado como secreto de Compose, leído por el entrypoint): `docker compose -f docker-compose.yml -f docker-compose.secrets.yml up -d` (ver comentarios en `docker-compose.secrets.yml`).

**Imagen ya cargada (`oneview-goc-ai:latest`) + mismo patrón de secretos**: `docker compose -f docker-compose.prod.with-secrets.yml up -d` (el archivo por defecto es `./.env`; otra ruta: `APP_ENV_FILE=/opt/goc/prod.env`).

#### Si tú no tienes acceso al servidor (solo entregas la imagen)

La imagen **no puede** llevar contraseñas dentro de la capa: cualquiera con el `.tar` podría extraerlas. Lo que sí puedes dejar **listo** es:

| Qué incluye el `.tar` | Qué va aparte (siempre) |
|------------------------|-------------------------|
| Código, dependencias, entrypoint | Un solo archivo `.env` **o** variables / secretos en el orquestador |

**Roles típicos**

1. **Tú (desarrollo / release)**: generas `oneview-goc-ai_v*.tar` + documentación + `.env.example` (sin secretos).
2. **Quien despliega** (sí tiene acceso al host o al panel de Kubernetes/ECS/Portainer): coloca el `.env` real **o** define las mismas variables allí. No hace falta reconstruir la imagen.

**Un solo comando en el servidor** (si ya dejaron el `.env` en la carpeta de despliegue):

```bash
docker load -i oneview-goc-ai_v3.2.7-mcp.tar
mkdir -p logs data
docker compose -f docker-compose.prod.yml up -d
```

**Sin archivo `.env` en la carpeta pero con montaje fijo** (solo quien administra el host):

```bash
docker run -d --name goc-ai -p 8080:8080 \
  -v /opt/goc/prod.env:/app/.env:ro \
  -v goc-data:/app/data \
  oneview-goc-ai:latest
```

`app.py` carga `/app/.env` al arrancar; el webhook y el resto de claves pueden ir solo en ese archivo.

**Secretos por archivo** (nombre del fichero bajo `/run/secrets/` → variable): el `docker-entrypoint.sh` rellena, si aún no están definidas, variables como `SLACK_WEBHOOK_URL` desde archivos `slack_webhook` o `slack_webhook_url`, `BEDROCK_API_KEY` desde `bedrock_api_key`, etc. Útil en Kubernetes u orquestadores que montan un secreto por clave.

#### ¿Y si encriptamos las contraseñas?

Sí, pero **solo gana sentido** si el material cifrado y la **clave de descifrado** no van juntos en el mismo paquete público.

| Enfoque | Qué cifras | Dónde queda la clave |
|--------|------------|----------------------|
| **Mal diseño** | `.env` en AES/GPG | La misma clave dentro de la imagen o del `.tar` → quien tiene el artefacto puede descifrar; **no sustituye** secretos en runtime. |
| **SOPS + KMS** (AWS/GCP/Azure) | Fichero versionado en git | Solo cuentas IAM / roles en el despliegue descifran; nada en la imagen. |
| **age** / **GPG** | Fichero que envías por canal inseguro | La clave privada **solo** en el host, en un vault, o inyectada como variable/secret al arrancar el contenedor. |
| **Orquestador** | Nada en disco plano | Kubernetes Secrets, AWS Secrets Manager, Parameter Store: la app recibe variables ya en claro en memoria. |

**Recomendación práctica**: en equipos medianos/grandes, **SOPS** o **secretos del proveedor de nube**; en despliegue simple, **un `.env` o secretos montados** (como arriba) suelen ser más simples que meter descifrado dentro de la imagen (habría que añadir binarios y gestionar claves igualmente).

#### AWS: “vault” nativo vs HashiCorp Vault

En AWS lo habitual **no** es un producto llamado “Vault”, sino:

| Servicio | Uso típico |
|----------|------------|
| **AWS Secrets Manager** | Secretos por nombre/ARN, rotación opcional, integración con RDS, etc. |
| **Systems Manager Parameter Store** | Parámetros (incl. **SecureString** cifrados con KMS); a menudo más barato/simple que Secrets Manager para claves estáticas. |

**Patrón recomendado**: el contenedor **no** incluye contraseñas. La tarea (ECS/EKS) o la instancia tiene un **rol IAM** que permite leer el secreto; la plataforma **inyecta** variables de entorno o archivos en el arranque (por ejemplo [secretOptions en ECS](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/specifying-sensitive-data-secrets.html), [Secrets Store CSI en EKS](https://docs.aws.amazon.com/secretsmanager/latest/userguide/integrate-kubernetes-secrets-store-csi-driver.html)). La aplicación sigue leyendo `SLACK_WEBHOOK_URL`, `BEDROCK_API_KEY`, etc. como hoy: **no hace falta cambiar el código** si el orquestador rellena el entorno antes de ejecutar `app.py`.

**HashiCorp Vault** (autohospedado en EC2/ECS o **HCP Vault** en la nube) es otra opción: agentes o init containers obtienen secretos y los exponen como env o ficheros. Tiene más operación que Secrets Manager/Parameter Store si ya estás en AWS.

**Resumen**: usar **Secrets Manager** o **Parameter Store** + **IAM mínimo** suele ser el camino más directo en AWS; la imagen Docker sigue siendo la misma; solo cambia **cómo** se rellenan las variables en el despliegue.

##### Integración en la app (Secrets Manager → JSON → `os.environ`)

Si el orquestador **no** inyecta cada variable por separado, esta imagen puede **leer un único secreto** al arrancar (`tools/aws_secrets_env.py`) y rellenar el entorno antes de cargar el resto de módulos.

1. En **Secrets Manager**, crea un secreto de tipo **texto** cuyo valor sea un **JSON** con las mismas claves que usarías en `.env`:

```json
{
  "SLACK_WEBHOOK_URL": "https://hooks.slack.com/services/...",
  "BEDROCK_API_KEY": "ABSK...",
  "DATADOG_API_KEY": "...",
  "DATADOG_APP_KEY": "...",
  "SPLUNK_TOKEN": "...",
  "PAGERDUTY_API_TOKEN": "..."
}
```

2. Asigna a la tarea ECS, al rol de Lambda o al rol de instancia EC2 una política IAM que permita:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "ReadOneviewGocSecret",
      "Effect": "Allow",
      "Action": ["secretsmanager:GetSecretValue"],
      "Resource": "arn:aws:secretsmanager:REGION:ACCOUNT_ID:secret:oneview/goc/prod-XXXXXX"
    }
  ]
}
```

(Si el secreto usa una **KMS** propia, añade también `kms:Decrypt` en esa clave.)

3. **Variables de entorno** del contenedor (solo hace falta apuntar al secreto; no hace falta repetir cada API key):

| Variable | Descripción |
|----------|-------------|
| `AWS_SECRETS_MANAGER_SECRET_ID` | Nombre del secreto o ARN completo (obligatoria para activar la carga). |
| `AWS_SECRETS_MANAGER_REGION` | Opcional; por defecto `AWS_REGION` / `AWS_DEFAULT_REGION` / `us-east-1`. |
| `AWS_SECRETS_MANAGER_OVERWRITE` | `1` / `true`: las claves del JSON **pisan** variables ya definidas en el entorno (por defecto no pisan). |
| `AWS_SECRETS_MANAGER_REQUIRED` | `1` / `true`: si falla la lectura del secreto, el proceso **termina** (útil en producción). |

Credenciales de AWS: **perfil de instancia** (EC2), **rol de tarea** (ECS/Fargate), **variables** `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` (solo si tu política lo permite), o `~/.aws` en desarrollo.

**Alternativa sin este código**: en **ECS** puedes mapear cada clave del secreto a una variable de entorno con `secrets` en la definición de tarea; la app sigue igual y **no** necesita `AWS_SECRETS_MANAGER_SECRET_ID`.

**Desde tu `.env` local** (sin subir a git): script de ayuda que convierte el `.env` a JSON y crea o actualiza el secreto en AWS:

```bash
cd multi-agent-mcp
python old/scripts/push_env_to_secrets_manager.py --env-file .env --dry-run --pretty   # solo vista previa
python old/scripts/push_env_to_secrets_manager.py --env-file .env --push --secret-id oneview/goc/prod --region us-east-1
```

Requiere credenciales AWS (`aws configure`, `AWS_PROFILE`, o rol) con permiso `secretsmanager:CreateSecret` / `PutSecretValue` / `DescribeSecret`.

### 3. Verificar Estado

```bash
# Ver logs
docker logs goc-ai

# Ver logs en tiempo real
docker logs -f goc-ai

# Health check
curl http://localhost:8080/api/health
```

### 4. Acceder a la Aplicación

Una vez que el contenedor esté corriendo:
- **Interfaz Principal**: http://localhost:8080
- **Status Monitor**: http://localhost:8080/statusmonitor
- **Health Check**: http://localhost:8080/api/health

## 🛠️ Comandos Útiles

### Detener el Contenedor
```bash
docker stop goc-ai
```

### Reiniciar el Contenedor
```bash
docker restart goc-ai
```

### Eliminar el Contenedor
```bash
docker rm -f goc-ai
```

### Ver uso de recursos
```bash
docker stats goc-ai
```

## 📊 Características de la Imagen

- ✅ Python 3.12-slim
- ✅ Flask con todas las dependencias
- ✅ AWS Bedrock SDK
- ✅ MCP SDK para herramientas internas
- ✅ Health check automático cada 30s
- ✅ Volumen persistente para base de datos SQLite
- ✅ Logs optimizados
- ✅ Puerto 8080 expuesto

## 🔄 Actualización

Para actualizar a una nueva versión:

1. Detener y eliminar el contenedor actual:
```bash
docker rm -f goc-ai
```

2. Eliminar la imagen anterior:
```bash
docker rmi oneview-goc-ai:latest
```

3. Cargar la nueva imagen:
```bash
docker load -i oneview-goc-ai_v3.2.7-mcp.tar
```

4. Ejecutar el nuevo contenedor (ver paso 2)

## 🐛 Troubleshooting

### Contenedor no arranca: `exec format error`, `no matching manifest`, o imagen “wrong architecture”

**Causa**: La arquitectura de la imagen no coincide con el host (p. ej. imagen **arm64** y servidor **amd64**, o al revés).

**Solución**: Vuelve a generar el `.tar` con la plataforma del destino. Por defecto el script construye **linux/arm64**; para despliegue en Linux x86_64: `BUILD_PLATFORM=linux/amd64 ./docker-build-export.sh`. Luego `docker load` y arranca de nuevo.

Comprobar arquitectura de una imagen cargada:

```bash
docker image inspect oneview-goc-ai:latest --format '{{.Architecture}}'
```

Debe coincidir con el host (p. ej. `amd64` en un nodo EKS x86_64, `arm64` en un host ARM).

### `docker compose` no arranca: `couldn't find env file` o `env file ... not found`

**Causa**: `docker-compose.prod.yml` declara `env_file: .env`. Si en la EC2 **no existe** ese archivo en la carpeta del compose, Compose **rechaza** levantar el servicio (no es un fallo de la imagen `.tar`).

**Solución**: En el directorio de despliegue, crea el fichero (aunque sea vacío o copiado de `.env.example`):

```bash
cp .env.example .env && nano .env
# o, solo para probar que el contenedor arranca:
touch .env
docker compose -f docker-compose.prod.yml up -d
```

### Contenedor en bucle de reinicio, `OOMKilled`, o siempre `unhealthy`

**Causas frecuentes**

1. **Demasiados workers de Gunicorn** — Cada proceso carga todo `app.py` (Datadog, Splunk, PagerDuty, etc.). La fórmula antigua `(2 × CPU) + 1` en instancias pequeñas (p. ej. 2 GB RAM) agota memoria y el kernel mata el proceso (`docker inspect … OOMKilled`).
2. **Health check demasiado pronto** — El primer `GET /api/health` puede tardar mientras arrancan los workers; si el probe falla antes, Docker marca el contenedor como no sano y reinicia.

**Qué hace el proyecto ahora**

- Por defecto Gunicorn usa **`sync`** (comportamiento clásico, estable en EC2). Opcional: **`GUNICORN_WORKER_CLASS=gthread`** si quieres hilos por proceso.
- **`WEB_CONCURRENCY`** limita procesos worker (en la imagen y en `docker-compose.prod.yml` suele ir **`2`** en instancias pequeñas).
- Healthcheck del **Dockerfile** y de Compose: **`start_period` ~90s**, más reintentos, y el probe exige respuesta HTTP “ok”.

**Qué puedes ajustar**

```bash
docker run -e WEB_CONCURRENCY=1 -e GUNICORN_WORKER_CLASS=sync -p 8080:8080 oneview-goc-ai:latest
```

Si necesitas más concurrencia I/O en un host grande: `-e GUNICORN_WORKER_CLASS=gthread -e GUNICORN_THREADS=4`.

### Subir la imagen a un registry (ECR, Harbor, etc.): rechazo por tamaño o timeout

- El `.tar` suele ser ~200 MB sin comprimir; muchos registros permiten capas mayores, pero **subidas por UI** a veces limitan el tamaño del archivo. Usa `docker push` desde CLI o sube por capas, o comprime: `gzip oneview-goc-ai_v*.tar` y sube el `.gz` si tu proceso lo acepta.
- Si el error menciona **manifest** o **denied**, revisa `docker login` al registry y permisos del repositorio.

### Error: "Must be connected to Arlo VPN"

Este error aparece cuando el contenedor no puede alcanzar el servidor MCP interno.

**Causa**: El servidor MCP usa un ALB interno de AWS:
```
http://internal-arlochat-mcp-alb-880426873.us-east-1.elb.amazonaws.com:8080
```

#### Solución Rápida - Script de Diagnóstico

1. Copiar el script de test:
```bash
chmod +x docker-network-test.sh
./docker-network-test.sh
```

2. El script verificará:
   - ✅ DNS resolution del ALB interno
   - ✅ Conectividad de red al puerto 8080
   - ✅ Configuración de DNS de la VM
   - ✅ Metadata de AWS (si es EC2)

#### Verificaciones Manuales

**1. DNS Resolution**
```bash
# Dentro del contenedor
docker exec goc-ai nslookup internal-arlochat-mcp-alb-880426873.us-east-1.elb.amazonaws.com

# Desde la VM host
nslookup internal-arlochat-mcp-alb-880426873.us-east-1.elb.amazonaws.com
```

**Solución si DNS falla**:
```bash
# Verificar DNS servers de la VM
cat /etc/resolv.conf

# Debe usar el DNS de la VPC (normalmente VPC_CIDR+2)
# Ejemplo: si VPC es 10.0.0.0/16, DNS debe ser 10.0.0.2
# Ejemplo: si VPC es 172.31.0.0/16, DNS debe ser 172.31.0.2

# Agregar DNS de VPC si no está
sudo bash -c 'echo "nameserver 10.0.0.2" > /etc/resolv.conf'
```

**2. Network Connectivity**
```bash
# Test port 8080
telnet internal-arlochat-mcp-alb-880426873.us-east-1.elb.amazonaws.com 8080
# o
nc -zv internal-arlochat-mcp-alb-880426873.us-east-1.elb.amazonaws.com 8080
# o
curl -v http://internal-arlochat-mcp-alb-880426873.us-east-1.elb.amazonaws.com:8080/sse
```

**Solución si no conecta**:
- **Security Group del ALB**: Debe permitir tráfico entrante en puerto 8080 desde la subnet/SG de la VM
- **Network ACLs**: Verificar que no bloqueen tráfico
- **Route Tables**: Asegurar que la VM puede rutear al ALB

**3. Verificar desde el contenedor**
```bash
# Entrar al contenedor
docker exec -it goc-ai bash

# Verificar DNS y conectividad
apt-get update && apt-get install -y dnsutils curl
nslookup internal-arlochat-mcp-alb-880426873.us-east-1.elb.amazonaws.com
curl -v http://internal-arlochat-mcp-alb-880426873.us-east-1.elb.amazonaws.com:8080/sse
```

#### Checklist AWS

Si estás en AWS EC2:

- [ ] **VM en la misma VPC** que el ALB MCP
- [ ] **DNS Resolution**: VM usa VPC DNS resolver (no DNS públicos como 8.8.8.8)
- [ ] **Security Group del ALB**: Permite inbound en puerto 8080 desde SG de la VM
- [ ] **Security Group de la VM**: Permite outbound en puerto 8080
- [ ] **Network ACLs**: No bloquean tráfico en subnet de VM o ALB
- [ ] **Route Tables**: Subnet de VM tiene ruta al subnet del ALB
- [ ] **ALB Health Check**: Verificar que el ALB está "healthy"

#### Obtener IP del ALB (workaround)

Si DNS no funciona, puedes usar la IP directamente:

```bash
# Resolver IP del ALB
IP=$(nslookup internal-arlochat-mcp-alb-880426873.us-east-1.elb.amazonaws.com | grep Address | tail -1 | awk '{print $2}')
echo $IP

# Modificar código para usar IP directamente (temporal)
# Editar tools/ask_arlochat.py línea 49:
# MCP_SERVER_URL = "http://<IP_DEL_ALB>:8080"
```

### El contenedor no inicia
```bash
docker logs goc-ai
```

### Puerto 8080 ya está en uso
```bash
# Usar otro puerto
docker run -d --name goc-ai -p 9090:8080 oneview-goc-ai:latest
```

### Reiniciar desde cero
```bash
docker rm -f goc-ai
docker rmi oneview-goc-ai:latest
docker load -i oneview-goc-ai_v3.2.7-mcp.tar
docker run -d --name goc-ai -p 8080:8080 oneview-goc-ai:latest
```

## 📝 Notas

- La base de datos SQLite se guarda en `/app/data` dentro del contenedor
- Para persistencia, usa `-v` para montar un volumen local
- Las credenciales se pueden pasar por variables de entorno o estar embebidas en la imagen
- El health check usa el endpoint `/api/health`

## 🔒 Seguridad

- No incluye credenciales sensibles en la imagen
- Usa variables de entorno para configuración
- Base de datos SQLite local (no accesible desde fuera)
- HTTPS/TLS debe configurarse en el balanceador de carga o proxy reverso

---

**Generado**: 2026-03-10
**Versión**: Latest
**Tamaño**: 199 MB (comprimido)
