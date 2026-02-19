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

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
IMAGE_NAME="oneview-goc-ai"
VERSION="3.0.0-mcp"
TAR_FILE="${IMAGE_NAME}_v${VERSION}.tar"

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}OneView GOC AI - Docker Build & Export${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""

# Step 1: Clean old images
echo -e "${YELLOW}[1/4] Limpiando imágenes viejas de Docker...${NC}"
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
echo -e "${YELLOW}[2/4] Construyendo nueva imagen de Docker...${NC}"
docker build -t ${IMAGE_NAME}:latest -t ${IMAGE_NAME}:${VERSION} .
echo -e "${GREEN}   ✓ Imagen construida exitosamente${NC}"
echo ""

# Step 3: Export to .tar
echo -e "${YELLOW}[3/4] Exportando imagen a archivo .tar...${NC}"
if [ -f "$TAR_FILE" ]; then
    echo "   Eliminando archivo .tar existente..."
    rm -f "$TAR_FILE"
fi
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

# Optional: Compress
echo -e "${BLUE}¿Deseas comprimir el archivo .tar? (reduce tamaño ~60%)${NC}"
echo -e "Ejecuta: ${GREEN}gzip ${TAR_FILE}${NC}"
echo ""

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}✓ Proceso completado exitosamente${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""

echo "📚 Para usar la imagen:"
echo "   1. Cargar: docker load -i ${TAR_FILE}"
echo "   2. Ejecutar: docker run -d -p 8080:8080 --env-file .env ${IMAGE_NAME}:latest"
echo "   3. Verificar: curl http://localhost:8080/api/tools"
echo ""
echo "📖 Documentación completa: DOCKER_IMAGE_TAR.md"
