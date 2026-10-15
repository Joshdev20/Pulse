# Pulse backend — image for a container host (Render, Railway, Fly.io).
#
# Runs uvicorn as a long-lived process so WebSockets work, which is the whole
# reason the backend is not on a serverless platform.

FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements-prod.txt ./
RUN pip install --no-cache-dir -r requirements-prod.txt

COPY app ./app

# Uploads are written here. Mount a volume at this path so files survive a
# redeploy; without one the container filesystem is replaced on every deploy.
ENV UPLOAD_DIR=/data/uploads
RUN mkdir -p /data/uploads

# The host injects $PORT; default to 8000 for a plain `docker run`.
ENV PORT=8000
EXPOSE 8000

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
