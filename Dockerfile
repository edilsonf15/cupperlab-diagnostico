FROM mcr.microsoft.com/playwright/python:v1.49.1-noble

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8000 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /srv

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# La imagen oficial de Playwright ya trae Chromium y sus dependencias de sistema
# (evita el fallo de apt con ttf-unifont/ttf-ubuntu-font-family en Debian nuevo).
# Aseguramos el navegador para la version instalada, sin --with-deps (las libs ya estan).
RUN playwright install chromium

COPY app/ ./app/

WORKDIR /srv/app
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/salud',timeout=4).status==200 else 1)" || exit 1

# Un worker: el analisis es I/O-bound y el PDF corre en segundo plano.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--timeout-keep-alive", "65"]
