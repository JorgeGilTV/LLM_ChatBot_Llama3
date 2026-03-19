# 👥 Capacidad de Usuarios Concurrentes

## 📊 Resumen

Este documento explica cuántas personas pueden usar OneView GOC AI simultáneamente y qué sucede cuando múltiples usuarios hacen consultas al mismo tiempo.

---

## 🔄 Modos de Operación

### 1️⃣ Modo Desarrollo (Actual por defecto)

**Comando:** `python app.py`

**Capacidad:**
- ✅ **Threading habilitado** (actualización reciente)
- 👥 **10-20 usuarios simultáneos** (depende de recursos del sistema)
- 🔄 Cada request se maneja en un thread separado
- ⏱️ Si varios usuarios consultan al mismo tiempo, todos procesan en paralelo

**Limitaciones:**
- No optimizado para producción
- Menor estabilidad bajo alta carga
- No escala horizontalmente

**Ideal para:**
- ✅ Desarrollo local
- ✅ Equipo pequeño (< 20 usuarios)
- ✅ Testing y demos

---

### 2️⃣ Modo Producción con Gunicorn (Recomendado)

**Comando:** `./start_production.sh`

**Capacidad:**
- 👥 **Hasta 100+ usuarios simultáneos**
- 🚀 Workers múltiples + threads por worker
- 🔄 Load balancing automático
- ⚡ Mejor rendimiento y estabilidad

**Configuración (gunicorn_config.py):**

```python
workers = (2 * CPU_cores) + 1  # Ej: 4 cores = 9 workers
threads = 4                     # 4 threads por worker
max_concurrent = workers * threads  # Ej: 9 * 4 = 36 requests simultáneas
```

**Ejemplo de Capacidad por Hardware:**

| CPU Cores | Workers | Threads | Max Concurrent | Usuarios Estimados |
|-----------|---------|---------|----------------|--------------------|
| 2 cores   | 5       | 4       | 20 requests    | 30-40 usuarios     |
| 4 cores   | 9       | 4       | 36 requests    | 60-80 usuarios     |
| 8 cores   | 17      | 4       | 68 requests    | 100-150 usuarios   |
| 16 cores  | 33      | 4       | 132 requests   | 200-300 usuarios   |

**Ideal para:**
- ✅ Producción
- ✅ Equipos medianos/grandes
- ✅ Uso departamental o empresarial

---

### 3️⃣ Modo Docker (Escalable)

**Comando:** `docker run -p 8080:8080 oneview-goc-ai:latest`

**Capacidad:**
- 👥 **50-100+ usuarios por contenedor**
- 📦 Usa Gunicorn internamente
- 🌐 Se puede escalar horizontalmente (múltiples contenedores)
- ☁️ Compatible con Kubernetes, Docker Swarm, ECS, etc.

**Escalamiento Horizontal:**

```bash
# 1 contenedor = 100 usuarios
# 5 contenedores = 500 usuarios
# 10 contenedores = 1000+ usuarios
```

**Ideal para:**
- ✅ Producción a gran escala
- ✅ Cloud deployment (AWS, Azure, GCP)
- ✅ Auto-scaling basado en carga

---

## 🧪 Escenarios de Uso Real

### Escenario 1: 5 Usuarios Simultáneos (Modo Threading)

| Usuario | Acción | Tiempo Inicio | Tiempo Fin | Resultado |
|---------|--------|---------------|------------|-----------|
| Alice   | Consulta DD + Splunk | 0s | 20s | ✅ Completo |
| Bob     | Consulta MCP_ARLO | 2s | 18s | ✅ Completo |
| Carlos  | Consulta Datadog | 5s | 22s | ✅ Completo |
| Diana   | Consulta PagerDuty | 7s | 15s | ✅ Completo |
| Eduardo | Consulta Confluence | 10s | 25s | ✅ Completo |

**Resultado:** ✅ Todos procesan en paralelo sin esperas

---

### Escenario 2: 50 Usuarios Simultáneos (Modo Gunicorn - 36 slots)

- **Primeros 36 usuarios:** ✅ Procesan inmediatamente en paralelo
- **Usuarios 37-50:** ⏳ Esperan en cola (~5-10 segundos promedio)
- **Todos completan:** ✅ Sin errores 504, todos obtienen respuesta

---

### Escenario 3: 200+ Usuarios (Modo Docker Cluster)

```
Load Balancer
    ├── Container 1 (100 usuarios) ✅
    ├── Container 2 (100 usuarios) ✅
    └── Container 3 (100 usuarios) ✅
    
Total: 300 usuarios sin problemas
```

---

## 🚦 ¿Qué pasa si hay demasiados usuarios?

### Comportamiento con Threading (Actual):
1. **Hasta capacidad máxima:** ✅ Todos procesan en paralelo
2. **Sobre capacidad:** 
   - ⏳ Requests adicionales esperan en cola
   - 🕐 Tiempo de espera: 5-30 segundos típicamente
   - ⚠️ Si espera > 120 seg: Error 504 Gateway Timeout

### Comportamiento con Gunicorn (Producción):
1. **Hasta capacidad máxima:** ✅ Procesamiento óptimo
2. **Sobre capacidad:**
   - 📊 Backlog de 2048 requests en espera
   - ⏱️ Timeout ajustable (default: 120 seg)
   - 🔄 Gestión inteligente de colas

### Soluciones para Alta Demanda:
1. ✅ **Aumentar workers/threads** en gunicorn_config.py
2. ✅ **Escalar horizontalmente** (más contenedores)
3. ✅ **Usar load balancer** (nginx, AWS ALB, etc.)
4. ✅ **Implementar cache** para consultas frecuentes
5. ✅ **Rate limiting** para prevenir abuso

---

## 🔧 Cómo Cambiar de Modo

### Modo Desarrollo → Producción

```bash
# 1. Instalar gunicorn (si no está)
pip install gunicorn

# 2. Iniciar modo producción
./start_production.sh
```

### Producción → Docker

```bash
# 1. Build imagen (ya incluye gunicorn)
docker build -t oneview-goc-ai:latest .

# 2. Run container
docker run -d -p 8080:8080 oneview-goc-ai:latest
```

---

## 📈 Monitoreo de Capacidad

### Logs a revisar:

```bash
# Ver requests activos (Gunicorn)
tail -f gunicorn_access.log

# Ver carga del sistema
htop  # Revisar CPU y memoria

# Estadísticas de requests
curl http://localhost:8080/api/health  # (si implementado)
```

### Métricas importantes:
- **Response time promedio:** < 30 segundos ✅
- **CPU usage:** < 80% ✅
- **Memory:** < 90% ✅
- **Error rate:** < 1% ✅

---

## ✅ Recomendaciones por Tamaño de Equipo

| Tamaño | Usuarios | Modo Recomendado | Hardware Mínimo |
|--------|----------|------------------|-----------------|
| Pequeño | 1-20 | Desarrollo (threading) | 2 cores, 4GB RAM |
| Mediano | 20-100 | Gunicorn | 4 cores, 8GB RAM |
| Grande | 100-500 | Docker (2-5 containers) | 8 cores, 16GB RAM |
| Empresarial | 500+ | Kubernetes cluster | Variable (auto-scale) |

---

## 🆘 Troubleshooting

### Problema: Error 504 con múltiples usuarios

**Solución:**
```bash
# Aumentar timeout en gunicorn_config.py
timeout = 180  # De 120 a 180 segundos

# O reiniciar con más workers
gunicorn --workers 8 --threads 4 app:flask_app
```

### Problema: Respuestas lentas

**Solución:**
1. ✅ Verificar que ejecución paralela esté activa (app.py línea 152)
2. ✅ Revisar recursos del servidor (CPU, RAM)
3. ✅ Considerar escalar horizontalmente

### Problema: "Address already in use"

**Solución:**
```bash
# Matar proceso en puerto 8080
lsof -ti:8080 | xargs kill -9

# Reiniciar
./start_production.sh
```

---

## 🎯 Conclusión

**Configuración Actual (con threading):**
- ✅ Soporta 10-20 usuarios simultáneos
- ✅ Ideal para equipos pequeños
- ✅ Fácil de usar y mantener

**Upgrade Recomendado:**
- 🚀 Modo Gunicorn → 50-100+ usuarios
- 📦 Docker → Escalabilidad ilimitada
- ☁️ Cloud → Miles de usuarios

**Para actualizar a producción:** Ver `start_production.sh` y `gunicorn_config.py`
