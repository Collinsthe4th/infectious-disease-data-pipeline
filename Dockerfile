FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Africa/Lagos

RUN apt-get update \
 && apt-get install -y --no-install-recommends git libgl1 libglib2.0-0 tzdata \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .
RUN useradd --create-home pipeline && chown -R pipeline /app
USER pipeline

ENTRYPOINT ["python", "-m", "src.orchestration.cli", "--plain-logs"]
CMD ["status"]
