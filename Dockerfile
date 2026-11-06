FROM python:3.12-slim

WORKDIR /upisas
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
ENV CROWDNAV_ENDPOINT=http://http-server:8080

COPY requirements.txt .
RUN python -m pip install --no-cache-dir -r requirements.txt

COPY UPISAS/__init__.py UPISAS/strategy.py UPISAS/knowledge.py UPISAS/exceptions.py ./UPISAS/
COPY UPISAS/exemplars/ ./UPISAS/exemplars/
COPY UPISAS/strategies/ ./UPISAS/strategies/
COPY run_crawdnav.py .
COPY run_challenge.py .

ENTRYPOINT ["python", "run_crawdnav.py"]
