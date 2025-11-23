FROM python:3.11-slim

# Kreiramo sistemskog korisnika
RUN useradd -m -u 1000 appuser

WORKDIR /app

# POPRAVAK: Instalacija redis-tools omogućuje 'redis-cli' za healthcheck!
RUN apt-get update && apt-get install -y curl redis-tools && rm -rf /var/lib/apt/lists/*

# Prvo kopiramo requirements radi cachiranja layera
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Kopiramo ostatak koda
COPY . .

# Postavljamo prava pristupa
RUN chown -R appuser:appuser /app

# Prebacujemo se na sigurnog korisnika
USER appuser

# Pokrećemo server
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]