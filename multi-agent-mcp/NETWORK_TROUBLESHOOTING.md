# 🌐 Network Troubleshooting Guide - OneView GOC AI

## 🚨 Problema: "Must be connected to Arlo VPN"

Este error aparece cuando el contenedor Docker no puede alcanzar el servidor MCP interno de AWS.

## 🔍 Diagnóstico Rápido

### Paso 1: Ejecutar Script de Diagnóstico

```bash
# En la VM donde corre Docker
chmod +x docker-network-test.sh
./docker-network-test.sh
```

Este script verifica:
- ✅ DNS resolution del MCP server
- ✅ Conectividad de red al puerto 8080
- ✅ Configuración DNS de la VM
- ✅ Si está en AWS EC2 y configuración de VPC

### Paso 2: Interpretar Resultados

#### ✅ Escenario Ideal
```
✅ DNS resolves successfully
✅ Port 8080 is open and accessible
✅ MCP Server is responding
```
→ **Todo funciona**, el problema está en otro lado (credenciales, etc.)

#### ❌ DNS no resuelve
```
❌ DNS resolution failed
```
→ **Problema**: VM no puede resolver nombres internos de AWS

#### ❌ Puerto no conecta
```
❌ Cannot connect to port 8080
```
→ **Problema**: Security Groups o Network ACLs bloqueando tráfico

---

## 🔧 Soluciones por Tipo de Error

### Error 1: DNS Resolution Failed

**Síntoma**: 
```bash
nslookup internal-arlochat-mcp-alb-880426873.us-east-1.elb.amazonaws.com
# Returns: server can't find... NXDOMAIN
```

**Causa**: La VM no está usando el DNS resolver de la VPC

**Solución**:

#### Opción A: Configurar DNS de VPC (Recomendado)

1. Identificar el DNS de tu VPC:
```bash
# El DNS de VPC siempre es: [VPC_CIDR_BASE] + 2
# Ejemplos:
# VPC 10.0.0.0/16  → DNS: 10.0.0.2
# VPC 172.31.0.0/16 → DNS: 172.31.0.2
# VPC 192.168.0.0/16 → DNS: 192.168.0.2
```

2. Actualizar `/etc/resolv.conf`:
```bash
sudo bash -c 'cat > /etc/resolv.conf << EOF
nameserver 10.0.0.2
search ec2.internal
EOF'
```

3. Si usas `systemd-resolved`:
```bash
sudo vi /etc/systemd/resolved.conf
# Agregar:
[Resolve]
DNS=10.0.0.2
Domains=ec2.internal
```

```bash
sudo systemctl restart systemd-resolved
```

4. Verificar:
```bash
nslookup internal-arlochat-mcp-alb-880426873.us-east-1.elb.amazonaws.com
# Debe resolver a una IP privada tipo 10.x.x.x o 172.x.x.x
```

#### Opción B: Usar IP directamente (Temporal)

1. Resolver IP desde una máquina que sí tenga acceso:
```bash
# Desde tu laptop con VPN o desde otra EC2
nslookup internal-arlochat-mcp-alb-880426873.us-east-1.elb.amazonaws.com
# Anota la IP: ejemplo 10.20.30.40
```

2. Usar variable de entorno al arrancar contenedor:
```bash
docker run -d \
  --name goc-ai \
  -p 8080:8080 \
  -e MCP_SERVER_HOST="10.20.30.40" \
  oneview-goc-ai:latest
```

### Error 2: Port Not Accessible

**Síntoma**:
```bash
telnet internal-arlochat-mcp-alb-880426873.us-east-1.elb.amazonaws.com 8080
# Returns: Connection refused or timeout
```

**Causa**: Security Groups o Network ACLs bloqueando tráfico

**Solución**:

#### Verificar Security Group del ALB

```bash
# Obtener Security Group del ALB
aws elbv2 describe-load-balancers \
  --names internal-arlochat-mcp-alb-880426873 \
  --query 'LoadBalancers[0].SecurityGroups' \
  --output text

# Ver reglas del Security Group (ejemplo: sg-abc123)
aws ec2 describe-security-groups \
  --group-ids sg-abc123 \
  --query 'SecurityGroups[0].IpPermissions'
```

**Debe tener regla**:
```
Type: Custom TCP
Protocol: TCP
Port: 8080
Source: <SG de la VM> O <CIDR de subnet de la VM>
```

#### Agregar Regla al Security Group del ALB

```bash
# Opción 1: Permitir desde el SG de la VM
aws ec2 authorize-security-group-ingress \
  --group-id sg-abc123 \
  --protocol tcp \
  --port 8080 \
  --source-group sg-def456

# Opción 2: Permitir desde CIDR de la subnet
aws ec2 authorize-security-group-ingress \
  --group-id sg-abc123 \
  --protocol tcp \
  --port 8080 \
  --cidr 10.0.1.0/24
```

#### Verificar Security Group de la VM

```bash
# El SG de la VM debe permitir OUTBOUND en puerto 8080
# Normalmente está permitido por defecto (0.0.0.0/0 all traffic)

# Verificar
aws ec2 describe-security-groups \
  --group-ids <SG_de_VM> \
  --query 'SecurityGroups[0].IpPermissionsEgress'
```

#### Verificar Network ACLs

```bash
# Network ACLs de la subnet de la VM
aws ec2 describe-network-acls \
  --filters "Name=association.subnet-id,Values=<subnet-id-de-VM>"

# Debe permitir:
# - OUTBOUND: puerto 8080 a subnet del ALB
# - INBOUND: puertos efímeros (1024-65535) desde subnet del ALB
```

### Error 3: Running Outside AWS

**Síntoma**:
```
⚠️  Not running in EC2 or metadata service unavailable
```

**Causa**: Docker corriendo en VM local o no-AWS

**Solución**:

Si estás en una VM local que **no está en AWS**:

1. **Necesitas VPN** para acceder a recursos internos de AWS
2. O migrar el contenedor a una EC2 en la misma VPC

Si estás en AWS pero metadata no funciona:
```bash
# Verificar que puedes acceder a metadata service
curl -v http://169.254.169.254/latest/meta-data/instance-id

# Si falla, verificar:
# - IMDSv2 requiere token
TOKEN=$(curl -X PUT "http://169.254.169.254/latest/api/token" -H "X-aws-ec2-metadata-token-ttl-seconds: 21600")
curl -H "X-aws-ec2-metadata-token: $TOKEN" http://169.254.169.254/latest/meta-data/instance-id
```

---

## 📋 Checklist Completo

### Pre-requisitos
- [ ] VM está en la **misma VPC** que el ALB MCP
- [ ] VM está en una subnet con **route al ALB**
- [ ] VM usa **DNS de VPC** (no 8.8.8.8 o 1.1.1.1)

### Connectivity Tests
- [ ] `nslookup` resuelve el hostname del ALB
- [ ] `telnet` o `nc` conecta al puerto 8080
- [ ] `curl` obtiene respuesta HTTP del ALB

### AWS Security
- [ ] **Security Group del ALB**: permite inbound en 8080 desde VM
- [ ] **Security Group de VM**: permite outbound en 8080
- [ ] **Network ACLs**: no bloquean tráfico bidireccional
- [ ] **ALB Target Groups**: tienen targets "healthy"

### Docker
- [ ] Contenedor inició correctamente: `docker ps`
- [ ] No hay errores en logs: `docker logs goc-ai`
- [ ] Contenedor puede resolver DNS: `docker exec goc-ai nslookup <mcp-host>`

---

## 🎯 Comandos Útiles de Diagnóstico

### Desde la VM Host

```bash
# DNS
nslookup internal-arlochat-mcp-alb-880426873.us-east-1.elb.amazonaws.com
dig internal-arlochat-mcp-alb-880426873.us-east-1.elb.amazonaws.com

# Connectivity
telnet internal-arlochat-mcp-alb-880426873.us-east-1.elb.amazonaws.com 8080
nc -zv internal-arlochat-mcp-alb-880426873.us-east-1.elb.amazonaws.com 8080
curl -v http://internal-arlochat-mcp-alb-880426873.us-east-1.elb.amazonaws.com:8080/sse

# Route
ip route get 10.x.x.x  # IP del ALB
traceroute internal-arlochat-mcp-alb-880426873.us-east-1.elb.amazonaws.com
```

### Desde el Contenedor

```bash
# Entrar al contenedor
docker exec -it goc-ai bash

# Instalar herramientas
apt-get update && apt-get install -y dnsutils curl telnet iputils-ping net-tools

# Tests
nslookup internal-arlochat-mcp-alb-880426873.us-east-1.elb.amazonaws.com
curl -v http://internal-arlochat-mcp-alb-880426873.us-east-1.elb.amazonaws.com:8080/sse
cat /etc/resolv.conf
```

### AWS CLI

```bash
# Info del ALB
aws elbv2 describe-load-balancers --names internal-arlochat-mcp-alb-880426873

# Target Health
aws elbv2 describe-target-health --target-group-arn <arn-del-target-group>

# Security Groups
aws ec2 describe-security-groups --group-ids <sg-id>

# Network ACLs
aws ec2 describe-network-acls --network-acl-ids <acl-id>
```

---

## ✅ Solución Confirmada

Una vez que **todos los tests pasen**:

```bash
# Reiniciar el contenedor
docker restart goc-ai

# Verificar logs
docker logs -f goc-ai

# Debería ver:
# ✅ GocBedrock MCP loaded (SDK Async mode - Python 3.10+)
# * Running on http://0.0.0.0:8080
```

Acceder a: **http://<VM_IP>:8080**

---

## 🆘 Si nada funciona

1. **Capturar información completa**:
```bash
./docker-network-test.sh > diagnostics.txt
docker logs goc-ai >> diagnostics.txt
```

2. **Información a compartir**:
   - Salida de `docker-network-test.sh`
   - Logs del contenedor: `docker logs goc-ai`
   - Configuración de red de la VM
   - Security Groups del ALB y VM
   - VPC/Subnet info

3. **Workaround temporal**: Ejecutar directamente en VM sin Docker
```bash
# En la VM
cd /app
pip install -r requirements.txt
python app.py
```

Si funciona así pero no en Docker → problema con networking de Docker (revisar `--network host`)
