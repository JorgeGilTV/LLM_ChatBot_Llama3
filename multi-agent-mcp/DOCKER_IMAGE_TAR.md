# OneView GOC AI - Docker Image (.tar)

## 📦 Información de la Imagen

- **Archivo**: `oneview-goc-ai_v3.0.0-mcp.tar`
- **Tamaño**: ~178 MB (comprimido)
- **Versión**: 3.0.0-MCP
- **Tags**: 
  - `oneview-goc-ai:latest`
  - `oneview-goc-ai:3.0.0-mcp`
- **Base**: Python 3.12-slim
- **Puerto**: 8080

## 🚀 Cómo Usar el Archivo .tar

### Opción 1: Cargar la Imagen desde el .tar

```bash
# Cargar la imagen de Docker desde el archivo .tar
docker load -i oneview-goc-ai_v3.0.0-mcp.tar

# Verificar que la imagen se cargó correctamente
docker images | grep oneview-goc-ai
```

Salida esperada:
```
oneview-goc-ai   latest       d573057f0688   816MB
oneview-goc-ai   3.0.0-mcp    d573057f0688   816MB
```

### Opción 2: Ejecutar el Contenedor

#### Método Simple (sin variables de entorno):
```bash
docker run -d \
  --name oneview-goc-ai \
  -p 8080:8080 \
  oneview-goc-ai:latest
```

#### Método Completo (con variables de entorno):
```bash
docker run -d \
  --name oneview-goc-ai \
  -p 8080:8080 \
  --env-file .env \
  -v $(pwd)/static/search_history.json:/app/static/search_history.json \
  -v $(pwd)/agent_tool_logs.log:/app/agent_tool_logs.log \
  oneview-goc-ai:latest
```

#### Método con Docker Compose:
```bash
# Usa el docker-compose.yml existente
docker-compose up -d
```

### Opción 3: Verificar el Contenedor

```bash
# Ver contenedores en ejecución
docker ps

# Ver logs del contenedor
docker logs oneview-goc-ai

# Seguir logs en tiempo real
docker logs -f oneview-goc-ai

# Verificar el health check
docker inspect oneview-goc-ai | grep -A 10 Health
```

### Opción 4: Acceder a la Aplicación

Una vez que el contenedor esté corriendo:

```bash
# Abrir en el navegador
open http://localhost:8080

# O verificar con curl
curl http://localhost:8080/api/tools
curl http://localhost:8080/mcp/info
```

## 🔧 Variables de Entorno Requeridas

Crea un archivo `.env` con las siguientes variables:

```bash
# Datadog
DATADOG_API_KEY=your_datadog_api_key
DATADOG_APP_KEY=your_datadog_app_key

# PagerDuty
PAGERDUTY_API_KEY=your_pagerduty_api_key

# Confluence
CONFLUENCE_API_TOKEN=your_confluence_token
CONFLUENCE_EMAIL=your_email@arlo.com

# Jira
JIRA_API_TOKEN=your_jira_token
JIRA_EMAIL=your_email@arlo.com

# Splunk (opcional)
SPLUNK_TOKEN=your_splunk_token
SPLUNK_HOST=your_splunk_host

# MCP ArloChat
MCP_SERVER_URL=your_arlochat_mcp_url
MCP_SERVER_TOKEN=your_arlochat_token

# Google Gemini (opcional)
GOOGLE_API_KEY=your_google_api_key
```

## 📋 Comandos Útiles

### Gestión del Contenedor

```bash
# Detener el contenedor
docker stop oneview-goc-ai

# Iniciar el contenedor
docker start oneview-goc-ai

# Reiniciar el contenedor
docker restart oneview-goc-ai

# Eliminar el contenedor
docker rm -f oneview-goc-ai

# Entrar al contenedor (debugging)
docker exec -it oneview-goc-ai /bin/bash
```

### Gestión de la Imagen

```bash
# Ver información detallada de la imagen
docker inspect oneview-goc-ai:latest

# Ver el historial de capas
docker history oneview-goc-ai:latest

# Eliminar la imagen
docker rmi oneview-goc-ai:latest

# Limpiar imágenes no usadas
docker image prune -a
```

### Backup y Compartir

```bash
# Comprimir el .tar (para compartir/transferir)
gzip oneview-goc-ai_v3.0.0-mcp.tar
# Resultado: oneview-goc-ai_v3.0.0-mcp.tar.gz (~60-70 MB)

# Descomprimir
gunzip oneview-goc-ai_v3.0.0-mcp.tar.gz

# Transferir a otro servidor
scp oneview-goc-ai_v3.0.0-mcp.tar.gz user@server:/path/

# En el servidor remoto:
gunzip oneview-goc-ai_v3.0.0-mcp.tar.gz
docker load -i oneview-goc-ai_v3.0.0-mcp.tar
```

## 🌐 Endpoints Disponibles

Una vez que el contenedor esté corriendo:

### Web UI
- **Dashboard**: http://localhost:8080/

### API Endpoints
- **Tools List**: http://localhost:8080/api/tools
- **Arlo Status**: http://localhost:8080/api/arlo-status
- **PagerDuty Monitor**: http://localhost:8080/api/pagerduty-monitor
- **Deployments**: http://localhost:8080/api/deployments/upcoming

### MCP Endpoints (NEW in v3.0)
- **MCP Info**: http://localhost:8080/mcp/info
- **MCP SSE**: http://localhost:8080/mcp/sse

## 🐛 Troubleshooting

### El contenedor no inicia
```bash
# Ver logs de error
docker logs oneview-goc-ai

# Verificar que el puerto 8080 no esté en uso
lsof -i :8080

# Reiniciar Docker Desktop
```

### No puede conectarse a APIs externas
```bash
# Verificar variables de entorno
docker exec oneview-goc-ai printenv | grep API

# Verificar conectividad de red
docker exec oneview-goc-ai ping -c 3 google.com
```

### Health check falla
```bash
# Ver estado del health check
docker inspect oneview-goc-ai | grep -A 10 Health

# Entrar al contenedor y probar manualmente
docker exec -it oneview-goc-ai /bin/bash
curl http://localhost:8080/api/tools
```

### Problemas de permisos con volúmenes
```bash
# En el host, dar permisos correctos
chmod 666 static/search_history.json
chmod 666 agent_tool_logs.log

# O ejecutar sin volúmenes montados
docker run -d --name oneview-goc-ai -p 8080:8080 oneview-goc-ai:latest
```

## 📊 Recursos del Contenedor

### Uso de Recursos
- **RAM**: ~500 MB - 1 GB (depende de la carga)
- **CPU**: 0.5 - 2 cores (depende de las queries)
- **Disco**: 816 MB (imagen) + logs

### Límites Recomendados
```bash
docker run -d \
  --name oneview-goc-ai \
  -p 8080:8080 \
  --memory="1g" \
  --cpus="1.0" \
  oneview-goc-ai:latest
```

## 🔐 Seguridad

### Mejores Prácticas

1. **No incluyas el .env en la imagen**
   - Siempre usa `--env-file` o variables de entorno al ejecutar

2. **Usa secrets de Docker (producción)**
   ```bash
   echo "your_api_key" | docker secret create datadog_key -
   ```

3. **Ejecuta con usuario no-root** (actualiza Dockerfile):
   ```dockerfile
   RUN useradd -m -u 1000 appuser
   USER appuser
   ```

4. **Escanea la imagen por vulnerabilidades**
   ```bash
   docker scan oneview-goc-ai:latest
   ```

## 📚 Referencias

- **Documentación Principal**: [README.md](README.md)
- **Setup MCP**: [SETUP_MCP.md](SETUP_MCP.md)
- **Docker Guide**: [DOCKER_README.md](DOCKER_README.md)
- **Deployment**: [DEPLOYMENT.md](DEPLOYMENT.md)

## 🎉 ¿Qué Incluye esta Imagen?

### Aplicación Completa v3.0
- ✅ Flask Web UI con dashboard interactivo
- ✅ 15 herramientas integradas (Datadog, PagerDuty, Jira, Splunk, Confluence)
- ✅ MCP Server (expone herramientas vía protocolo MCP)
- ✅ MCP Client (consume 73+ herramientas de ArloChat)
- ✅ Auto-refresh monitors (Arlo Status, PagerDuty, Deployments)
- ✅ Health checks automáticos
- ✅ Logging completo

### Dependencias
- Python 3.12
- Flask 3.1+
- MCP SDK 1.26+
- Todas las librerías necesarias (ver requirements.txt)

---

**Fecha de creación**: Feb 19, 2026  
**Versión**: 3.0.0-MCP  
**Build**: Multi-architecture (supports ARM64/AMD64)
