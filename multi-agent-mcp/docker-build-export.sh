#!/bin/bash

###############################################################################
# OneView GOC AI - Docker Build & Export Script
# 
# Este script:
# 1. Limpia imágenes viejas de Docker
# 2. Construye una nueva imagen de Docker
# 3. Exporta la imagen a un archivo .tar
###############################################################################

set -e  # Exit on error

# macOS: Docker Desktop CLI no siempre está en PATH
if ! command -v docker >/dev/null 2>&1; then
    MAC_DOCKER="/Applications/Docker.app/Contents/Resources/bin/docker"
    if [ -x "$MAC_DOCKER" ]; then
        export PATH="/Applications/Docker.app/Contents/Resources/bin:$PATH"
    fi
fi
if ! command -v docker >/dev/null 2>&1; then
    echo "Error: no se encuentra el comando docker. Instala Docker Desktop o añádelo al PATH."
    exit 1
fi
if ! docker info >/dev/null 2>&1; then
    echo "Error: el daemon de Docker no responde. Abre Docker Desktop y espera a que arranque, luego vuelve a ejecutar este script."
    exit 1
fi

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
IMAGE_NAME="oneview-goc-ai"
VERSION="3.2.28-mcp"
TAR_FILE="${IMAGE_NAME}_v${VERSION}.tar"
# Imagen: por defecto linux/arm64 (EKS/EC2/VM ARM, Graviton, Mac Apple Silicon).
# Servidores x86_64 (amd64 clásico): BUILD_PLATFORM=linux/amd64 ./docker-build-export.sh
BUILD_PLATFORM="${BUILD_PLATFORM:-linux/arm64}"

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}OneView GOC AI - Docker Build & Export${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""

# Step 1: Clean old images
echo -e "${YELLOW}[1/4] Limpiando imágenes viejas de Docker...${NC}"
# Contenedores que siguen usando la imagen impiden docker rmi (cualquier tag)
for IMG in $(docker images -q ${IMAGE_NAME} 2>/dev/null); do
    CIDS=$(docker ps -aq --filter ancestor="${IMG}" 2>/dev/null || true)
    if [ -n "$CIDS" ]; then
        echo "   Eliminando contenedores que usan ${IMAGE_NAME} (${IMG:0:12})..."
        docker rm -f $CIDS 2>/dev/null || true
    fi
done
OLD_IMAGES=$(docker images -q ${IMAGE_NAME} 2>/dev/null)
if [ -n "$OLD_IMAGES" ]; then
    echo "   Eliminando imágenes existentes..."
    docker rmi -f $OLD_IMAGES 2>/dev/null || true
    echo -e "${GREEN}   ✓ Imágenes viejas eliminadas${NC}"
else
    echo "   No hay imágenes viejas para eliminar"
fi
echo ""

# Step 2: Build new image
echo -e "${YELLOW}[2/4] Construyendo nueva imagen de Docker (platform ${BUILD_PLATFORM})...${NC}"
docker build --platform "${BUILD_PLATFORM}" -t ${IMAGE_NAME}:latest -t ${IMAGE_NAME}:${VERSION} .
echo -e "${GREEN}   ✓ Imagen construida exitosamente${NC}"
echo ""

# Step 3: Export to .tar
echo -e "${YELLOW}[3/4] Exportando imagen a archivo .tar...${NC}"
# Remove prior plain .tar exports (same image name; not .tar.gz)
find . -maxdepth 1 -type f \( -name "${IMAGE_NAME}_v*.tar" -o -name "${IMAGE_NAME}-latest.tar" \) -exec rm -f {} \; 2>/dev/null || true
docker save ${IMAGE_NAME}:latest -o "$TAR_FILE"
echo -e "${GREEN}   ✓ Imagen exportada a: ${TAR_FILE}${NC}"
echo ""

# Step 4: Show summary
echo -e "${YELLOW}[4/4] Resumen:${NC}"
echo ""
echo "📦 Archivo .tar creado:"
ls -lh "$TAR_FILE"
echo ""
echo "🐳 Imágenes de Docker disponibles:"
docker images --format "table {{.Repository}}\t{{.Tag}}\t{{.Size}}" | grep -E "REPOSITORY|${IMAGE_NAME}"
echo ""

echo -e "${BLUE}Export sin comprimir (.tar). Para .tar.gz: gzip ${TAR_FILE} (opcional)${NC}"
echo ""

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}✓ Proceso completado exitosamente${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""

echo "📚 Para usar la imagen:"
echo "   1. Cargar: docker load -i ${TAR_FILE}"
echo "   2. Credenciales NO van dentro de la imagen. Crea o monta un .env en el host (canal seguro; ver DOCKER_DEPLOYMENT.md)."
echo "   3. Producción (recomendado): mkdir -p logs data && docker compose -f docker-compose.prod.with-secrets.yml up -d"
echo "   4. O en una línea: docker run -d -p 8080:8080 --env-file .env ${IMAGE_NAME}:latest"
echo "   5. Verificar: curl http://localhost:8080/api/health"
echo ""
echo "📖 Documentación: DOCKER_DEPLOYMENT.md (entrega tar + .env aparte)"
