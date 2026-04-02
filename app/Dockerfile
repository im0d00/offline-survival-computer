FROM python:3.12-slim

LABEL maintainer="offline-survival-computer"
LABEL description="Offline Survival Computer – Command Center web application"

WORKDIR /srv/app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source
COPY . .

# Create data directories
RUN mkdir -p data/notes data/uploads data/documents data/maps

# Expose the web port
EXPOSE 5000

# Initialise DB and start Gunicorn
CMD ["sh", "-c", "python -c 'from main import init_db; init_db()' && gunicorn --bind 0.0.0.0:5000 --workers 2 --timeout 120 main:app"]
