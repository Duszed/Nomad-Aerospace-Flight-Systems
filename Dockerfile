# Nomad Atlas server image: verify page + Telegram bot.
# Deploy on a server in Uzbekistan. The image contains no data and no keys.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MPLCONFIGDIR=/tmp/matplotlib \
    ATLAS_DATA_DIR=/data

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY atlas ./atlas
COPY telemetry ./telemetry

RUN useradd --create-home atlas && mkdir /data && chown atlas /data
USER atlas
VOLUME /data
EXPOSE 8000

CMD ["python", "-m", "atlas", "serve", "--host", "0.0.0.0", "--port", "8000"]
