# 1. Base Image
FROM python:3.11-slim

# 2. Security: Kreiraj sistemskog korisnika 'appuser'
# Nikad ne vrti aplikacije kao root!
RUN useradd -m -u 1000 appuser

WORKDIR /app

# 3. Install Dependencies
# Kopiramo samo requirements prvo da iskoristimo Docker cache
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 4. Copy Code
# Kopiramo ostatak koda
COPY . .

# 5. Permissions
# Osiguraj da appuser posjeduje datoteke
RUN chown -R appuser:appuser /app

# 6. Switch User
USER appuser

# 7. Start
# --proxy-headers je bitan ako si iza ngroka ili Nginxa
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]