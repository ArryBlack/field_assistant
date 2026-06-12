FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Ensure Python can import top-level modules from the backend folder
ENV PYTHONPATH=/app/packages/backend:/app

# System deps for PostgreSQL client and build tools
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc libpq-dev curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Copy only requirements first for better cache utilization
COPY packages/backend/requirements.txt /app/requirements.txt

# Install python deps and gunicorn/uvicorn for production
RUN pip install --no-cache-dir -r /app/requirements.txt \
    && pip install --no-cache-dir gunicorn uvicorn

# Copy repository into image
COPY . /app

# Copy start script from package folder
COPY packages/backend/start.sh /usr/local/bin/start.sh
# Ensure LF line endings (avoid CRLF issues from Windows hosts)
RUN sed -i 's/\r$//' /usr/local/bin/start.sh \
    && chmod +x /usr/local/bin/start.sh

# Create non-root user AND change ownership of the app directory
RUN useradd --create-home appuser && \
    chown -R appuser:appuser /app

USER appuser

EXPOSE 8000

# Default: run API. Start script supports modes (api, fetcher, both)
CMD ["/usr/local/bin/start.sh", "api"]
