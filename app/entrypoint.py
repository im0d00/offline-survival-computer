#!/usr/bin/env python3
"""Entrypoint used by the Docker container."""
from main import app, init_db, get_monitor

if __name__ == "__main__":
    init_db()
    get_monitor().start()
    app.run(host="0.0.0.0", port=5000)
