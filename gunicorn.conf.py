"""Gunicorn configuration for the hosted deployment.

`preload_app` imports the application once in the master process, so the
first-run schema creation happens exactly once rather than racing across
workers.  Because that import opens database connections, every worker
disposes the inherited engine immediately after forking — a forked child
must never reuse a socket opened before the fork.
"""
import os

bind = f"0.0.0.0:{os.environ.get('PORT', '10000')}"
workers = int(os.environ.get("WEB_CONCURRENCY", "2"))
threads = int(os.environ.get("GUNICORN_THREADS", "4"))
worker_class = "gthread"
preload_app = True

timeout = 120
graceful_timeout = 30
keepalive = 5

accesslog = "-"
errorlog = "-"
loglevel = os.environ.get("LOG_LEVEL", "info")


def post_fork(server, worker):
    """Drop any database connection inherited from the master process."""
    try:
        from app import app
        from models import db
        with app.app_context():
            db.engine.dispose()
    except Exception as exc:                       # never block a worker starting
        server.log.warning("post_fork engine dispose skipped: %s", exc)
