#!/bin/bash
# Production startup script with Gunicorn

echo "🚀 Starting OneView GOC AI in production mode..."

# Activate virtual environment if it exists
if [ -d "venv" ]; then
    source venv/bin/activate
fi

# Install gunicorn if not present
pip list | grep gunicorn > /dev/null || pip install gunicorn

# Start with Gunicorn
exec gunicorn \
    --config gunicorn_config.py \
    --bind 0.0.0.0:8080 \
    --workers 4 \
    --threads 4 \
    --timeout 120 \
    --access-logfile - \
    --error-logfile - \
    --log-level info \
    app:flask_app
