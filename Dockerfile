FROM python:3.13-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && useradd -m -u 10001 miclist && mkdir /data && chown miclist /data
COPY --chown=miclist:miclist app ./app
COPY --chown=miclist:miclist assets ./assets
USER miclist
ENV DATABASE_PATH=/data/miclist.sqlite3 LOCAL_DEV=0 SCHEDULER_ENABLED=1 GEOCODING_ENABLED=1
EXPOSE 8000
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1 --proxy-headers --forwarded-allow-ips ${TRUSTED_PROXY_IPS:-127.0.0.1}"]
