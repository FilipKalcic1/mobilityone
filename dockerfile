# Koristimo laganu verziju Pythona
FROM python:3.11-slim

# Postavljamo radni direktorij unutar kontejnera
WORKDIR /app

# Prvo kopiramo requirements da iskoristimo Docker cache
COPY requirements.txt .

# Instaliramo biblioteke
RUN pip install --no-cache-dir -r requirements.txt

# Kopiramo ostatak koda
COPY . .

# Ova komanda se gazi u docker-compose, ali je tu za svaki slučaj
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]