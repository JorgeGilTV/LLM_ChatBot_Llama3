# OneView GOC AI - Docker Deployment Guide

## 📦 Archivo Docker

- **Imagen Docker**: `oneview-goc-ai-latest.tar`
- **Tamaño**: 199 MB
- **Tag**: `oneview-goc-ai:latest`
- **Base**: Python 3.12-slim

## 🚀 Instrucciones de Deployment

### 1. Cargar la Imagen Docker

```bash
docker load -i oneview-goc-ai-latest.tar
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

#### Opción B: Con Variables de Entorno y Volumen Persistente
```bash
docker run -d \
  --name goc-ai \
  -p 8080:8080 \
  -v $(pwd)/data:/app/data \
  -e AWS_BEARER_TOKEN_BEDROCK="your_bedrock_token" \
  -e DD_API_KEY="your_datadog_api_key" \
  -e DD_APP_KEY="your_datadog_app_key" \
  -e PD_API_KEY="your_pagerduty_key" \
  oneview-goc-ai:latest
```

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
docker load -i oneview-goc-ai-latest.tar
```

4. Ejecutar el nuevo contenedor (ver paso 2)

## 🐛 Troubleshooting

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
docker load -i oneview-goc-ai-latest.tar
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
