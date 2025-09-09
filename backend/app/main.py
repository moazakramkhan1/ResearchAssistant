from fastapi import FastAPI, UploadFile, File, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic_settings import BaseSettings
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
import hashlib, os, shutil, requests, json, uuid
from lxml import etree

class Settings(BaseSettings):
    DATABASE_URL: str = "postgresql://app:password@db:5432/research"
    STORAGE_DIR: str = "/data/uploads"
    GROBID_URL: str = "http://grobid:8070"
    OLLAMA_URL: str = "http://ollama:11434"
    N8N_INGEST_SECRET: str = "change_me"
    CORS_ORIGINS: str = "http://localhost:5173"

settings = Settings()

engine = create_engine(settings.DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine)

app = FastAPI(title="Research Assistant API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.CORS_ORIGINS],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def init_db():
    # Use TEXT for id (we generate UUID in Python)
    with engine.begin() as conn:
        conn.execute(text("""
        CREATE TABLE IF NOT EXISTS papers (
            id TEXT PRIMARY KEY,
            filename TEXT NOT NULL,
            file_path TEXT NOT NULL,
            md5_hash TEXT,
            status TEXT NOT NULL DEFAULT 'processing',
            csl_json JSONB,
            one_liner TEXT,
            summary_150w TEXT,
            keywords JSONB,
            created_at TIMESTAMP NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMP NOT NULL DEFAULT NOW()
        );
        """))

@app.on_event("startup")
def startup():
    init_db()
    os.makedirs(settings.STORAGE_DIR, exist_ok=True)

@app.get("/healthz")
def healthz():
    return {"ok": True}

class UploadResponse(BaseModel):
    id: str
    status: str

@app.post("/api/upload", response_model=UploadResponse)
def upload_pdf(file: UploadFile = File(...)):
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported.")
    file_id = str(uuid.uuid4())
    dest = os.path.join(settings.STORAGE_DIR, f"{file_id}.pdf")
    with open(dest, "wb") as f:
        shutil.copyfileobj(file.file, f)

    # md5
    md5 = hashlib.md5()
    with open(dest, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            md5.update(chunk)
    md5_hex = md5.hexdigest()

    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO papers (id, filename, file_path, md5_hash, status)
            VALUES (:id, :fn, :fp, :md5, 'processing')
        """), {"id": file_id, "fn": file.filename, "fp": dest, "md5": md5_hex})

    # Trigger n8n (internal docker network)
    try:
        webhook_url = "http://n8n:5678/webhook/paper-uploaded"
        headers = {"x-n8n-secret": settings.N8N_INGEST_SECRET}
        payload = {"paper_id": file_id, "file_path": dest}
        requests.post(webhook_url, headers=headers, json=payload, timeout=10)
    except Exception:
        # n8n may be down during dev; keep API responsive
        pass

    return {"id": file_id, "status": "processing"}

# ---- N8N ingest (accept ollama_response and parse it server-side) ----
class IngestPayload(BaseModel):
    paper_id: str
    csl_json: dict | None = None
    one_liner: str | None = None
    summary_150w: str | None = None
    keywords: list[str] | None = None
    ollama_response: str | None = None
    error: str | None = None

@app.post("/api/hooks/n8n/ingest")
async def ingest_from_n8n(req: Request):
    if req.headers.get("x-n8n-secret") != settings.N8N_INGEST_SECRET:
        raise HTTPException(status_code=403, detail="Forbidden")
    data = await req.json()
    body = IngestPayload(**data)
    return _ingest_impl(body)

def _ingest_impl(body: IngestPayload):
    # If needed, parse the raw JSON string from Ollama
    if (not body.one_liner or not body.summary_150w) and body.ollama_response:
        try:
            parsed = json.loads(body.ollama_response)
            body.one_liner = body.one_liner or parsed.get("one_line_takeaway")
            body.summary_150w = body.summary_150w or parsed.get("summary_150w")
            if not body.keywords:
                kw = parsed.get("keywords")
                if isinstance(kw, list):
                    body.keywords = kw
        except Exception:
            if not body.one_liner and not body.summary_150w:
                body.error = (body.error or "Could not parse Ollama JSON")

    status = "ready" if not body.error else "error"
    with engine.begin() as conn:
        conn.execute(text("""
            UPDATE papers
            SET csl_json = :csl,
                one_liner = :ol,
                summary_150w = :sum,
                keywords = :kw,
                status = :st,
                updated_at = NOW()
            WHERE id = :id
        """), {
            "csl": json.dumps(body.csl_json) if body.csl_json else None,
            "ol": body.one_liner,
            "sum": body.summary_150w,
            "kw": json.dumps(body.keywords) if body.keywords else None,
            "st": status,
            "id": body.paper_id
        })
    return {"ok": True, "status": status}

# ---- List / Read ----
@app.get("/api/papers")
def list_papers():
    with engine.begin() as conn:
        rows = conn.execute(text("""
            SELECT id, filename, status, created_at, updated_at
            FROM papers ORDER BY created_at DESC
        """)).mappings().all()
    return {"items": [dict(r) for r in rows]}

@app.delete("/api/papers/{paper_id}")
def delete_paper(paper_id: str):
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT file_path FROM papers WHERE id = :id"),
            {"id": paper_id}
        ).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Not found")
    fp = row["file_path"]
    try:
        if fp and os.path.exists(fp):
            os.remove(fp)
    except Exception:
        pass
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM papers WHERE id = :id"), {"id": paper_id})

    return {"ok": True, "id": paper_id, "deleted": True}

@app.get("/api/papers/{paper_id}")
def get_paper(paper_id: str):
    with engine.begin() as conn:
        row = conn.execute(text("""
            SELECT * FROM papers WHERE id = :id
        """), {"id": paper_id}).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Not found")
    return dict(row)
class TEIPayload(BaseModel):
    tei_xml: str

def _t(el): return (el.text or "").strip() if el is not None else ""

def tei_to_csl_and_abstract(tei_xml: str):
    ns = {"tei": "http://www.tei-c.org/ns/1.0"}
    root = etree.fromstring(tei_xml.encode("utf-8"))

    title_el = root.find(".//tei:teiHeader//tei:titleStmt/tei:title", ns)
    title = _t(title_el)

    authors = []
    for a in root.findall(".//tei:teiHeader//tei:sourceDesc//tei:analytic/tei:author", ns):
        surname = _t(a.find(".//tei:surname", ns))
        forename = _t(a.find(".//tei:forename", ns))
        if not surname and not forename:
            pn = a.find(".//tei:persName", ns)
            authors.append({"family": _t(pn), "given": ""})
        else:
            authors.append({"family": surname, "given": forename})

    year = None
    date_el = root.find(".//tei:teiHeader//tei:sourceDesc//tei:monogr/tei:imprint/tei:date", ns)
    if date_el is not None:
        when = date_el.get("when")
        if when and len(when) >= 4:
            year = when[:4]
        else:
            txt = _t(date_el)
            digits = "".join([c for c in txt if c.isdigit()])
            year = digits[:4] if digits else None

    container_el = root.find(".//tei:teiHeader//tei:sourceDesc//tei:monogr/tei:title", ns)
    container_title = _t(container_el)

    doi = None
    idno = root.find(".//tei:teiHeader//tei:sourceDesc//tei:idno[@type='DOI']", ns)
    if idno is not None:
        doi = _t(idno)
    else:
        for i in root.findall(".//tei:idno", ns):
            tt = _t(i)
            if "10." in tt and "/" in tt:
                doi = tt.strip(); break

    abstract = None
    abs_el = root.find(".//tei:teiHeader//tei:profileDesc//tei:abstract", ns)
    if abs_el is not None:
        abstract = " ".join(abs_el.itertext()).strip()
    if not abstract:
        paras = root.findall(".//tei:text/tei:body//tei:p", ns)
        if paras:
            snippet = " ".join(" ".join(p.itertext()).strip() for p in paras[:3])
            abstract = snippet[:3000]

    csl = {
        "type": "article-journal",
        "title": title or None,
        "author": authors or None,
        "issued": {"date-parts": [[int(year)]]} if year and year.isdigit() else None,
        "container-title": container_title or None,
        "DOI": doi or None,
    }
    csl = {k: v for k, v in csl.items() if v}
    return csl, abstract

@app.post("/api/internal/tei-parse")
def tei_parse(body: TEIPayload):
    try:
        csl, abstract = tei_to_csl_and_abstract(body.tei_xml)
        return {"csl_json": csl, "abstract": abstract}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"TEI parse error: {e}")

class GrobidFulltextRequest(BaseModel):
    paper_id: str

@app.post("/api/internal/grobid-fulltext")
def grobid_fulltext(req: GrobidFulltextRequest):
    with engine.begin() as conn:
        row = conn.execute(text("SELECT file_path FROM papers WHERE id = :id"),
                           {"id": req.paper_id}).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Paper not found")
    file_path = row["file_path"]
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="PDF file missing")

    try:
        with open(file_path, "rb") as f:
            r = requests.post(
                f"{settings.GROBID_URL}/api/processFulltextDocument",
                files={"input": ("paper.pdf", f, "application/pdf")},
                timeout=300
            )
        r.raise_for_status()
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"GROBID error: {e}")

    return {"tei_xml": r.text}

@app.get("/api/files/{paper_id}")
def get_pdf(paper_id: str):
    with engine.begin() as conn:
        row = conn.execute(text("SELECT file_path, filename FROM papers WHERE id = :id"),
                           {"id": paper_id}).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(row["file_path"], media_type="application/pdf", filename=row["filename"])
