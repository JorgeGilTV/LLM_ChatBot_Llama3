#!/bin/bash

# Script to easily manage Arlo GenAI Docker container
# Usage: ./docker-run.sh [start|stop|restart|logs|build|clean]

set -e

PROJECT_NAME="arlo-genai"
COMPOSE_FILE="docker-compose.yml"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

function print_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

function print_warning() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

function print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

function check_env_file() {
    if [ ! -f .env ]; then
        print_error ".env file not found!"
        print_info "Creating .env from .env.example..."
        if [ -f .env.example ]; then
            cp .env.example .env
            print_warning "Please edit .env file with your credentials before starting the application."
            exit 1
        else
            print_error ".env.example not found. Please create a .env file manually."
            exit 1
        fi
    fi
}

function start() {
    print_info "Starting $PROJECT_NAME..."
    check_env_file
    docker-compose up -d
    print_info "Container started successfully!"
    print_info "Access the application at: http://localhost:5001"
    print_info "To view logs, run: ./docker-run.sh logs"
}

function stop() {
    print_info "Stopping $PROJECT_NAME..."
    docker-compose down
    print_info "Container stopped successfully!"
}

function restart() {
    print_info "Restarting $PROJECT_NAME..."
    stop
    sleep 2
    start
}

function logs() {
    print_info "Showing logs for $PROJECT_NAME (Ctrl+C to exit)..."
    docker-compose logs -f
}

function build() {
    print_info "Building $PROJECT_NAME..."
    check_env_file
    docker-compose build --no-cache
    print_info "Build completed successfully!"
}

function rebuild() {
    print_info "Rebuilding and restarting $PROJECT_NAME..."
    docker-compose down
    docker-compose build --no-cache
    docker-compose up -d
    print_info "Rebuild and restart completed!"
    print_info "Access the application at: http://localhost:5001"
}

function clean() {
    print_warning "This will remove all containers, volumes, and images for $PROJECT_NAME"
    read -p "Are you sure? (yes/no): " confirmation
    if [ "$confirmation" == "yes" ]; then
        print_info "Cleaning up..."
        docker-compose down -v
        docker rmi ${PROJECT_NAME}:latest 2>/dev/null || true
        print_info "Cleanup completed!"
    else
        print_info "Cleanup cancelled."
    fi
}

function status() {
    print_info "Checking status of $PROJECT_NAME..."
    docker-compose ps
    echo ""
    print_info "Container health:"
    docker inspect --format='{{.Name}}: {{.State.Health.Status}}' $(docker-compose ps -q) 2>/dev/null || echo "No health check available"
}

function shell() {
    print_info "Opening shell in $PROJECT_NAME container..."
    docker-compose exec arlo-genai bash
}

# Main script
case "$1" in
    start)
        start
        ;;
    stop)
        stop
        ;;
    restart)
        restart
        ;;
    logs)
        logs
        ;;
    build)
        build
        ;;
    rebuild)
        rebuild
        ;;
    clean)
        clean
        ;;
    status)
        status
        ;;
    shell)
        shell
        ;;
    *)
        echo "Usage: $0 {start|stop|restart|logs|build|rebuild|clean|status|shell}"
        echo ""
        echo "Commands:"
        echo "  start    - Start the application"
        echo "  stop     - Stop the application"
        echo "  restart  - Restart the application"
        echo "  logs     - View application logs"
        echo "  build    - Build the Docker image"
        echo "  rebuild  - Rebuild and restart the application"
        echo "  clean    - Remove all containers and images"
        echo "  status   - Show container status"
        echo "  shell    - Open a shell in the container"
        exit 1
        ;;
esac

exit 0
