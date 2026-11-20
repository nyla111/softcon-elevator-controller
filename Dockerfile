FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    EMS_RUNS_DIR=/app/runs

WORKDIR /app

COPY pyproject.toml README.md ./
COPY ems ./ems
RUN pip install . && useradd --create-home ems && mkdir -p /app/runs && chown ems /app/runs
USER ems

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/state')"

CMD ["uvicorn", "ems.web.server:app", "--host", "0.0.0.0", "--port", "8000"]
