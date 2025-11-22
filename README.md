1. Environment Setup
Kreiraj .env u rootu i popuni varijable iz config.py:

Bash

APP_ENV=development
REDIS_URL=redis://redis:6379

# Providers
OPENAI_API_KEY=sk-...
INFOBIP_BASE_URL=...
INFOBIP_API_KEY=...
INFOBIP_SENDER_NUMBER=...
INFOBIP_SECRET_KEY=... # HMAC secret

# Upstream API
MOBILITY_API_URL=... # Base URL vanjskog API-ja
2. Dependencies
Osiguraj da je swagger.json prisutan u root direktoriju (app crasha ako ga nema jer ga ToolRegistry parsa pri startupu).

3. Build & Run
Koristi Docker Compose za API, Worker i Redis stack:

Bash

docker-compose up --build -d
4. Networking & Webhook
Exposeaj port 8000 i registriraj webhook na Infobipu:

Bash

ngrok http 8000
Webhook Endpoint: POST <ngrok_url>/webhook/whatsapp Healthcheck: GET /health

