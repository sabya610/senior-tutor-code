FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app/backend

COPY backend/requirements.txt ./requirements.txt
RUN pip install -r requirements.txt

COPY backend/app ./app
COPY frontend /app/frontend

RUN useradd --system --uid 10001 --create-home tutor \
    && mkdir -p /data /repos \
    && chown -R tutor:tutor /data

ENV FRONTEND_DIR=/app/frontend \
    DATA_DIR=/data \
    LOG_LEVEL=INFO

USER tutor
VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4).status == 200 else 1)"

CMD ["uvicorn", "app.main:app_factory", "--factory", "--host", "0.0.0.0", "--port", "8000"]
