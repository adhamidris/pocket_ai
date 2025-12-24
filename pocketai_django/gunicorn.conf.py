# ==============================================================================
# GUNICORN PRODUCTION CONFIGURATION
# ==============================================================================
# Production WSGI server configuration for multi-tenant Django app

import multiprocessing
import os

# Server Socket
bind = f"0.0.0.0:{os.getenv('PORT', '8000')}"
backlog = 2048

# Worker Processes
# Formula: (2 × CPU cores) + 1
workers = int(os.getenv('WEB_CONCURRENCY', multiprocessing.cpu_count() * 2 + 1))
worker_class = 'sync'  # Use 'gevent' or 'eventlet' for async if needed
worker_connections = 1000
max_requests = 1000  # Restart workers after N requests (prevents memory leaks)
max_requests_jitter = 50  # Add randomness to prevent thundering herd
timeout = 120  # 2 minutes (RAG queries can be slow)
graceful_timeout = 30
keepalive = 5

# Process Naming
proc_name = 'pocketai_gunicorn'

# Logging
accesslog = '-'  # stdout
errorlog = '-'   # stderr
loglevel = os.getenv('GUNICORN_LOG_LEVEL', 'info')
access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s" %(D)s'

# Security
limit_request_line = 4096  # Maximum size of HTTP request line
limit_request_fields = 100
limit_request_field_size = 8190

# Server Mechanics
daemon = False  # Don't daemonize (Docker handles this)
pidfile = None
umask = 0
user = None
group = None
tmp_upload_dir = None

# Preload app (faster worker spawn, but doesn't work with code reload)
preload_app = True

# Server Hooks
def on_starting(server):
    """Called just before the master process is initialized."""
    server.log.info("Gunicorn master starting")

def when_ready(server):
    """Called just after the server is started."""
    server.log.info(f"Gunicorn ready. Listening on {bind}")

def on_reload(server):
    """Called to recycle workers during a reload via SIGHUP."""
    server.log.info("Gunicorn reloading")

def worker_int(worker):
    """Called just after a worker exited on SIGINT or SIGQUIT."""
    worker.log.info(f"Worker {worker.pid} interrupted")
