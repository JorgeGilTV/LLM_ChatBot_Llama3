"""
Gunicorn configuration for production deployment
Optimized for handling multiple concurrent users
"""

import multiprocessing
import os

# Server socket
bind = f"0.0.0.0:{os.getenv('PORT', 8080)}"
backlog = 2048

# Worker processes
# Formula: (2 x num_cores) + 1
workers = multiprocessing.cpu_count() * 2 + 1
worker_class = 'sync'
worker_connections = 1000
timeout = 120  # Increased timeout for long-running queries (Datadog, Splunk)
keepalive = 5

# Worker threads (for I/O bound operations like API calls)
threads = 4  # Each worker can handle 4 concurrent requests

# Logging
accesslog = '-'  # Log to stdout
errorlog = '-'   # Log errors to stdout
loglevel = 'info'
access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s" %(D)s'

# Process naming
proc_name = 'oneview-goc-ai'

# Server mechanics
daemon = False
pidfile = None
umask = 0
user = None
group = None
tmp_upload_dir = None

# SSL (for HTTPS in production)
# keyfile = None
# certfile = None

# Maximum concurrent requests this configuration can handle:
# workers * threads = (2 * CPU_cores + 1) * 4
# Example: On a 4-core machine = 9 workers * 4 threads = 36 concurrent requests

print(f"🚀 Gunicorn config loaded")
print(f"   Workers: {workers}")
print(f"   Threads per worker: {threads}")
print(f"   Max concurrent requests: {workers * threads}")
print(f"   Timeout: {timeout}s")
