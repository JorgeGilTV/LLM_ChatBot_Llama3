"""
Gunicorn configuration for production deployment.

Docker / small EC2: each worker imports the full Flask app (heavy modules). The old
default (2 * CPU + 1) sync workers often caused OOMKilled or slow boots that failed
HEALTHCHECK. Defaults are container-safe; override with WEB_CONCURRENCY.
"""

import multiprocessing
import os

# Server socket (PORT must be numeric; empty env would break bind)
try:
    _port = int((os.getenv("PORT") or "8080").strip() or "8080")
except ValueError:
    _port = 8080
bind = f"0.0.0.0:{_port}"

backlog = 2048

# Workers: WEB_CONCURRENCY overrides everything (e.g. 1–2 on t3.small / 2 GB RAM)
_cpu = max(1, multiprocessing.cpu_count() or 1)
_wc = (os.getenv("WEB_CONCURRENCY") or "").strip()
if _wc:
    try:
        workers = max(1, min(int(_wc), 32))
    except ValueError:
        workers = max(1, min(_cpu, 4))
else:
    # Cap default so 8+ vCPU hosts do not spawn 17 processes × full app import
    workers = max(1, min(_cpu, 4))

# Default sync: same as classic Gunicorn/Flask deployments (EC2-friendly, avoids rare gthread issues).
# Set GUNICORN_WORKER_CLASS=gthread if you want threaded workers for I/O-bound routes.
worker_class = os.getenv("GUNICORN_WORKER_CLASS", "sync").strip() or "sync"
try:
    threads = int((os.getenv("GUNICORN_THREADS") or "4").strip() or "4")
except ValueError:
    threads = 4
threads = max(1, min(threads, 32))
if worker_class == "sync":
    threads = 1

worker_connections = 1000
# Status wall + APM wall can exceed several minutes (many Datadog calls). ALB/nginx often default to 60s and
# return 504 first — raise those proxies in sync (see DOCKER_DEPLOYMENT.md "504").
try:
    timeout = int((os.getenv("GUNICORN_TIMEOUT") or "600").strip() or "600")
except ValueError:
    timeout = 600
timeout = max(60, min(timeout, 3600))
keepalive = 5

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

print("🚀 Gunicorn config loaded")
print(f"   bind: {bind}")
print(f"   worker_class: {worker_class}")
print(f"   workers: {workers}")
print(f"   threads: {threads}")
print(f"   effective concurrency (approx): {workers * threads}")
print(f"   timeout: {timeout}s")
