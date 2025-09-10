# Research Assistant (Local)

Upload PDFs → extract clean metadata with **GROBID** → generate **one-liner + 150-word summary + keywords** with **Ollama** → view the paper’s **reference list** — all locally, for free.

---

## Features

-  100% local: GROBID + Ollama run in Docker on your machine
-  PDF → TEI → **CSL-JSON** metadata
-  LLM summaries (one-liner, ~150 words) + 5 keywords
-  **References extracted** and shown in the UI (from GROBID biblStruct)
-  Minimal web app (upload, list, delete, detail drawer)
-  Orchestrated with **n8n** (importable workflow, no manual tweaks)
-  API (FastAPI) + simple Postgres storage

---

## Quickstart

**Requirements:** Docker Desktop (or Docker Engine + Compose)

```bash
git clone https://github.com/yourname/research-assistant.git
cd research-assistant

# 1) Configure environment
cp .env.example .env
# Open .env and review values (ports, auth, secret, model list)

# 2) Start the stack
docker compose up -d --build
Open:

App → http://localhost:5173

API docs → http://localhost:8000/docs

n8n → http://localhost:5678 (basic auth from .env)

Import & activate the n8n workflow
Open n8n → Workflows → Import from File
Import workflows/pdf-metadata-summary.json from this repo.

Ensure the Webhook node path is paper-uploaded (it is by default).

The backend sends the header x-n8n-secret; make sure it matches N8N_INGEST_SECRET in your .env.

Activate the workflow.

The stack uses an init job to pre-pull the Ollama model listed in OLLAMA_MODELS.
You can also pull manually:

bash
Copy code
docker compose exec -T ollama ollama pull llama3.2:3b-instruct-q4_K_M
docker compose exec -T ollama ollama list
How it works
Upload a PDF in the app (POST /api/upload).

Backend stores the file and triggers n8n webhook with { paper_id, file_path }.

n8n:

Calls backend → GROBID fulltext (PDF → TEI)

Calls backend → TEI parse (TEI → CSL-JSON + Abstract + References)

Calls Ollama for JSON-only summary output

Posts results back to backend ingest hook

UI auto-polls until the item moves from processing → ready.
The detail drawer shows metadata, summary, keywords, and references.
You can also delete a paper.


API
POST /api/upload — multipart PDF upload → returns {id, status:"processing"}

GET /api/papers — list items (id, filename, status, timestamps)

GET /api/papers/{id} — full record (CSL, summary, keywords, citations)

DELETE /api/papers/{id} — delete DB row + best-effort file removal

GET /api/files/{id} — stream the original PDF

Internal (used by n8n/stack):

POST /api/internal/grobid-fulltext — {paper_id} → {tei_xml}

POST /api/internal/tei-parse — {tei_xml} → {csl_json, abstract, citations}

POST /api/hooks/n8n/ingest — called by workflow to persist results

Change the model
Update .env → OLLAMA_MODELS and restart, or pull manually:

bash
Copy code
docker compose exec -T ollama ollama pull mistral:7b-instruct
Update the model name inside the n8n workflow Ollama generate node.

Tip: small models are faster/cheaper but less coherent; tune num_ctx, num_predict, temperature in the workflow.

Troubleshooting
n8n → backend 403: x-n8n-secret mismatch — make sure it equals N8N_INGEST_SECRET.

Ollama manifest errors: pull manually; check internet access in the container:

bash
Copy code
docker compose exec -T ollama ollama pull llama3.2:3b-instruct-q4_K_M
docker compose logs -f ollama ollama-init
Long GROBID times / timeouts: large PDFs can be slow. The workflow and backend use generous timeouts (300s). If you’re on Windows/WSL2, consider raising memory:

Create %UserProfile%\.wslconfig:

ini
Copy code
[wsl2]
memory=4GB
processors=4
swap=8GB
localhostForwarding=true
Then wsl --shutdown and restart Docker Desktop.

Nothing updates after upload: ensure the workflow is Activated and the Webhook path is paper-uploaded.

Dev tips
Backend logs: docker compose logs -f backend

Frontend logs: docker compose logs -f frontend

n8n logs: docker compose logs -f n8n

Reset the stack (careful—wipes data):

bash
Copy code
docker compose down -v
rm -rf data/db data/uploads data/ollama data/n8n
docker compose up -d --build

## Demo

▶️ **Watch on Loom (direct):**  
https://www.loom.com/share/bb6305a288644cea8625f882c0aabe85?sid=61ed08e6-e65e-4896-b1b3-f31ae797960b

<!-- Clickable screenshot that opens the Loom video -->
[![App screenshot](https://github.com/user-attachments/assets/6fba37ab-c5c4-48cd-8716-e93636b87724)](https://www.loom.com/share/bb6305a288644cea8625f882c0aabe85?sid=61ed08e6-e65e-4896-b1b3-f31ae797960b)

