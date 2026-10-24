# Sandbox di esecuzione codice: nessun accesso a internet (rete interna), utente non privilegiato,
# filesystem di sola lettura tranne /tmp e /work (tmpfs). Vedi docker-compose.yml.
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl ca-certificates gnupg default-jdk-headless \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# Librerie Python più richieste per script, dati e grafici (nessuna rete a runtime, quindi vanno preinstallate)
RUN pip install --no-cache-dir fastapi uvicorn pydantic \
        numpy pandas matplotlib pillow sympy scipy requests openpyxl python-docx pypdf tabulate \
        python-pptx reportlab xlsxwriter markdown

RUN useradd -m -u 10001 -s /usr/sbin/nologin runner && mkdir -p /work && chown runner:runner /work
ENV MPLBACKEND=Agg PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HOME=/tmp
WORKDIR /app
COPY app.py .
USER runner
EXPOSE 8080
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080"]
