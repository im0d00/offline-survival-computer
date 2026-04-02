#!/usr/bin/env python3
"""Entrypoint used by the Docker container."""
from main import app, init_db

if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=5000)
