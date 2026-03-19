# 🚀 Quick Fix - VM Networking Issue

## El Problema

Tu VM en la subnet de Arlo ve el error: **"Must be connected to Arlo VPN"**

Esto significa que el contenedor Docker **no puede alcanzar el MCP server interno**:
```
http://internal-arlochat-mcp-alb-880426873.us-east-1.elb.amazonaws.com:8080
```

---

## ⚡ Solución Rápida (5 minutos)

### Paso 1: Copiar script de diagnóstico a la VM

```bash
# Copiar docker-network-test.sh a tu VM
scp docker-network-test.sh usuario@<IP_VM>:/home/usuario/

# SSH a la VM
ssh usuario@<IP_VM>
```

### Paso 2: Ejecutar diagnóstico

```bash
chmod +x docker-network-test.sh
./docker-network-test.sh
```

### Paso 3: Interpretar resultado

#### ✅ Si dice "All critical tests passed!"
→ El problema es otro (credentials, permisos, etc.)
→ Revisar logs: `docker logs goc-ai`

#### ❌ Si dice "DNS resolution failed"
→ **SOLUCIÓN**:

```bash
# 1. Identificar DNS de tu VPC
# Si tu VPC es 10.x.x.x/16 → DNS es 10.0.0.2
# Si tu VPC es 172.31.x.x/16 → DNS es 172.31.0.2

# 2. Configurar DNS
sudo bash -c 'cat > /etc/resolv.conf << EOF
nameserver 10.0.0.2
search ec2.internal
EOF'

# 3. Verificar
nslookup internal-arlochat-mcp-alb-880426873.us-east-1.elb.amazonaws.com
# Debe mostrar una IP tipo 10.x.x.x
```

#### ❌ Si dice "Cannot connect to port 8080"
→ **SOLUCIÓN**: Security Group del ALB no permite tráfico desde tu VM

**Opción A: Agregar regla al Security Group del ALB** (recomendado)
```bash
# Obtener SG ID del ALB
aws elbv2 describe-load-balancers \
  --names internal-arlochat-mcp-alb-880426873 \
  --query 'LoadBalancers[0].SecurityGroups[0]' \
  --output text

# Obtener SG ID de tu VM
aws ec2 describe-instances \
  --instance-ids <tu-instance-id> \
  --query 'Reservations[0].Instances[0].SecurityGroups[0].GroupId' \
  --output text

# Agregar regla (permite tráfico desde SG de VM al ALB)
aws ec2 authorize-security-group-ingress \
  --group-id <SG_del_ALB> \
  --protocol tcp \
  --port 8080 \
  --source-group <SG_de_VM>
```

**Opción B: Contactar al equipo de infraestructura**
- Pedir que agreguen el Security Group de tu VM al inbound del ALB
- Puerto: 8080
- Protocolo: TCP

### Paso 4: Reiniciar contenedor

```bash
docker restart goc-ai
docker logs -f goc-ai
```

---

## 🎯 Validación Final

1. **Desde la VM, probar directamente**:
```bash
curl -v http://internal-arlochat-mcp-alb-880426873.us-east-1.elb.amazonaws.com:8080/sse
```

Debe responder con:
- HTTP 200 OK, o
- HTTP 400/404 (está bien, el server responde)

**NO debe** responder:
- Connection refused
- Connection timeout
- Could not resolve host

2. **Acceder a la app**:
```
http://<IP_de_tu_VM>:8080
```

3. **Probar una query**:
- Ir a la interfaz web
- Hacer click en AI button
- Preguntar algo simple: "hello"

Si funciona → ✅ Problema resuelto!

---

## 📋 Checklist Pre-requisitos

Antes de contactar soporte, verifica:

- [ ] VM está en la **misma VPC** que el ALB MCP
- [ ] VM está en una **subnet privada** con acceso al ALB
- [ ] VM usa **DNS de la VPC** (no 8.8.8.8)
- [ ] Security Group del ALB permite inbound desde VM
- [ ] Docker está corriendo: `docker ps`
- [ ] Puerto 8080 no está bloqueado por firewall local

---

## 🆘 Comandos de Emergencia

Si nada funciona:

```bash
# 1. Ver logs del contenedor
docker logs goc-ai | tail -50

# 2. Entrar al contenedor y probar
docker exec -it goc-ai bash
apt-get update && apt-get install -y curl dnsutils
nslookup internal-arlochat-mcp-alb-880426873.us-east-1.elb.amazonaws.com
curl -v http://internal-arlochat-mcp-alb-880426873.us-east-1.elb.amazonaws.com:8080/sse

# 3. Si DNS falla DENTRO del contenedor pero funciona FUERA
# El contenedor no hereda el DNS de la VM correctamente
# Solución: usar --dns flag
docker rm -f goc-ai
docker run -d \
  --name goc-ai \
  -p 8080:8080 \
  --dns 10.0.0.2 \
  oneview-goc-ai:latest

# 4. Si NADA funciona, usar network host mode
docker rm -f goc-ai
docker run -d \
  --name goc-ai \
  --network host \
  oneview-goc-ai:latest
```

---

## 📚 Documentación Completa

Para más detalles:
- **NETWORK_TROUBLESHOOTING.md**: Guía completa de troubleshooting
- **DOCKER_DEPLOYMENT.md**: Instrucciones de deployment
- **docker-network-test.sh**: Script de diagnóstico automático

---

**Última actualización**: 2026-03-10
