#!/bin/bash
# Run OneView with persistent data storage
# This script creates a Docker volume for metrics database persistence

set -e

IMAGE_NAME="oneview-goc-ai:3.0.1-enhanced"
CONTAINER_NAME="oneview-goc"
VOLUME_NAME="oneview-metrics-data"
PORT="5000"

# Colors
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${BLUE}🚀 OneView GOC AI - Docker Runner with Persistent Storage${NC}"
echo ""

# Check if image exists
if ! docker images | grep -q "oneview-goc-ai"; then
    echo -e "${RED}❌ Docker image not found: $IMAGE_NAME${NC}"
    echo ""
    echo "Please load the image first:"
    echo "  docker load -i oneview-goc-ai-3.0.1-enhanced.tar"
    exit 1
fi

# Check if container is already running
if docker ps | grep -q $CONTAINER_NAME; then
    echo -e "${YELLOW}⚠️  Container $CONTAINER_NAME is already running${NC}"
    echo ""
    docker ps | grep $CONTAINER_NAME
    echo ""
    read -p "Do you want to stop and restart it? (y/n): " restart
    if [ "$restart" = "y" ]; then
        echo -e "${BLUE}Stopping existing container...${NC}"
        docker stop $CONTAINER_NAME
        docker rm $CONTAINER_NAME
    else
        echo "Exiting..."
        exit 0
    fi
fi

# Check if stopped container exists
if docker ps -a | grep -q $CONTAINER_NAME; then
    echo -e "${BLUE}Removing stopped container...${NC}"
    docker rm $CONTAINER_NAME
fi

# Create volume if it doesn't exist
if ! docker volume ls | grep -q $VOLUME_NAME; then
    echo -e "${BLUE}Creating persistent volume: $VOLUME_NAME${NC}"
    docker volume create $VOLUME_NAME
fi

# Run container with persistent volume
echo -e "${BLUE}Starting container with persistent storage...${NC}"
echo ""

docker run -d \
    --name $CONTAINER_NAME \
    -p $PORT:8080 \
    -v $VOLUME_NAME:/app/data \
    --restart unless-stopped \
    $IMAGE_NAME

echo -e "${GREEN}✅ Container started successfully!${NC}"
echo ""
echo -e "${GREEN}📊 Application:${NC} http://localhost:$PORT"
echo -e "${GREEN}🔌 API Docs:${NC} See API_DOCUMENTATION.md"
echo -e "${GREEN}💾 Data Volume:${NC} $VOLUME_NAME"
echo ""
echo "Commands:"
echo "  View logs:    docker logs -f $CONTAINER_NAME"
echo "  Stop:         docker stop $CONTAINER_NAME"
echo "  Restart:      docker restart $CONTAINER_NAME"
echo "  Shell:        docker exec -it $CONTAINER_NAME bash"
echo "  Health:       curl http://localhost:$PORT/api/health"
echo ""
echo "Volume Management:"
echo "  Inspect:      docker volume inspect $VOLUME_NAME"
echo "  Backup:       docker run --rm -v $VOLUME_NAME:/data -v \$(pwd):/backup alpine tar czf /backup/metrics-backup.tar.gz -C /data ."
echo "  Remove:       docker volume rm $VOLUME_NAME (⚠️  deletes all historical data)"
echo ""

# Wait a moment and check health
sleep 5
echo -e "${BLUE}Checking container health...${NC}"
docker ps | grep $CONTAINER_NAME
echo ""

# Test API endpoint
echo -e "${BLUE}Testing API endpoint...${NC}"
if curl -s http://localhost:$PORT/api/health > /dev/null; then
    echo -e "${GREEN}✅ API is responding${NC}"
    curl -s http://localhost:$PORT/api/health | python3 -m json.tool
else
    echo -e "${YELLOW}⚠️  API not responding yet, may need more time to start${NC}"
fi
