FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DATA_DIR=/data

WORKDIR /app

COPY requirements.txt ./
RUN python -m pip install --no-cache-dir -r requirements.txt

# Copy application code only; secrets and local databases stay out of the image.
COPY ponsbot/ ./ponsbot/

# Railway mounts the persistent volume here at runtime.
RUN mkdir -p /data
STOPSIGNAL SIGTERM
CMD ["python", "-m", "ponsbot"]
