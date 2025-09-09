# Research Assistant (Local, Open-Source)

Upload PDFs → extract clean metadata with GROBID → generate summaries with Ollama → view, edit, and copy formatted citations (APA/MLA/Chicago) in a simple web app. All local. Free.

## Quickstart

**Requirements:** Docker Desktop (or Docker + docker-compose)

```bash
git clone https://github.com/yourname/research-assistant.git
cd research-assistant
cp .env.example .env
docker compose up -d --build
./bin/bootstrap.sh
```

Open:

- App: http://localhost:5173
- API docs: http://localhost:8000/docs
- n8n (import the provided workflow): http://localhost:5678

Security: n8n is local and basic-auth protected; Ollama and GROBID are on the internal Docker network (not public).

## How it works

1. Upload PDF in the app (POST /api/upload).
2. Backend stores file and triggers an internal n8n webhook with {paper_id, file_path}.
3. n8n calls GROBID (PDF → TEI), converts TEI → CSL-JSON via backend, calls Ollama for summaries, and POSTs results back to backend.
4. App shows the paper with metadata + summaries; copy APA/MLA/Chicago via citeproc.

## Dev tips

- Backend logs: `docker compose logs -f backend`
- Frontend logs: `docker compose logs -f frontend`
- To pull a different model: `docker compose exec -T ollama bash -lc "ollama pull mistral:7b"`

## License

Apache-2.0
