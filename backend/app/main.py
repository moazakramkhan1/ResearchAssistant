from __future__ import annotations

import hashlib
import json
import os
import shutil
import uuid
from typing import List, Dict, Any, Tuple, Optional

import requests
from fastapi import FastAPI, UploadFile, File, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from lxml import etree
from pydantic import BaseModel
from pydantic_settings import BaseSettings
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker


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


def init_db() -> None:
    """Create table if needed and ensure `citations` exists (for upgrades)."""
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
            citations JSONB,
            created_at TIMESTAMP NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMP NOT NULL DEFAULT NOW()
        );
        """))
        # Backward-compatible: add citations if missing on older DBs
        conn.execute(text("ALTER TABLE papers ADD COLUMN IF NOT EXISTS citations JSONB;"))


@app.on_event("startup")
def startup() -> None:
    init_db()
    os.makedirs(settings.STORAGE_DIR, exist_ok=True)


@app.get("/healthz")
def healthz():
    return {"ok": True}



class UploadResponse(BaseModel):
    id: str
    status: str


class IngestPayload(BaseModel):
    paper_id: str
    csl_json: Optional[dict] = None
    one_liner: Optional[str] = None
    summary_150w: Optional[str] = None
    keywords: Optional[List[str]] = None
    citations: Optional[List[dict]] = None
    ollama_response: Optional[str] = None
    error: Optional[str] = None


class TEIPayload(BaseModel):
    tei_xml: str


class GrobidFulltextRequest(BaseModel):
    paper_id: str



def _txt(el: Optional[etree._Element]) -> str:
    return (el.text or "").strip() if el is not None else ""


def tei_to_csl_abstract_citations(tei_xml: str) -> Tuple[dict, Optional[str], List[dict]]:
    """
    Parse a TEI XML string (from GROBID) and produce:
      - csl_json (minimal, for the main paper)
      - abstract (string or None)
      - citations (list of CSL-like dicts)

    Uses .xpath() (with namespaces) for expressions that require unions/predicates.
    """
    ns = {"tei": "http://www.tei-c.org/ns/1.0"}
    try:
        root = etree.fromstring(tei_xml.encode("utf-8"))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid TEI XML: {e}")

    title_el = root.find(".//tei:teiHeader//tei:titleStmt/tei:title", ns)
    title = _txt(title_el)


    authors: List[dict] = []
    for a in root.findall(".//tei:teiHeader//tei:sourceDesc//tei:analytic/tei:author", ns):
        surname = _txt(a.find(".//tei:surname", ns))
        forename = _txt(a.find(".//tei:forename", ns))
        if not surname and not forename:
            pn = a.find(".//tei:persName", ns)
            authors.append({"family": _txt(pn), "given": ""})
        else:
            authors.append({"family": surname, "given": forename})

    year: Optional[str] = None
    date_el = root.find(".//tei:teiHeader//tei:sourceDesc//tei:monogr/tei:imprint/tei:date", ns)
    if date_el is not None:
        when = date_el.get("when")
        if when and len(when) >= 4:
            year = when[:4]
        else:
            txt = _txt(date_el)
            digits = "".join(c for c in txt if c.isdigit())
            year = digits[:4] if digits else None

   
    container_el = root.find(".//tei:teiHeader//tei:sourceDesc//tei:monogr/tei:title", ns)
    container_title = _txt(container_el)


    doi: Optional[str] = None
    id_doi = root.find(".//tei:teiHeader//tei:sourceDesc//tei:idno[@type='DOI']", ns)
    if id_doi is not None:
        doi = _txt(id_doi)
    else:
        
        for i in root.findall(".//tei:idno", ns):
            tt = _txt(i)
            if "10." in tt and "/" in tt:
                doi = tt.strip()
                break

    abstract: Optional[str] = None
    abs_el = root.find(".//tei:teiHeader//tei:profileDesc//tei:abstract", ns)
    if abs_el is not None:
        abstract = " ".join(abs_el.itertext()).strip()
    if not abstract:
        paras = root.findall(".//tei:text/tei:body//tei:p", ns)
        if paras:
            snippet = " ".join(" ".join(p.itertext()).strip() for p in paras[:3])
            abstract = snippet[:3000]


    citations: List[dict] = []
    try:
        bibl_structs = root.xpath(
            ".//tei:back//tei:listBibl//tei:biblStruct | "
            ".//tei:back//tei:div[@type='references']//tei:biblStruct",
            namespaces=ns
        )
    except etree.XPathEvalError:
      
        bibl_structs = root.xpath(".//tei:back//tei:listBibl//tei:biblStruct", namespaces=ns)
        if not bibl_structs:
            bibl_structs = root.xpath(".//tei:back//tei:div[@type='references']//tei:biblStruct", namespaces=ns)

    for bs in bibl_structs:
        
        ref_authors: List[dict] = []
        for a in bs.xpath(".//tei:analytic/tei:author | .//tei:monogr/tei:author", namespaces=ns):
            surname = _txt(a.find(".//tei:surname", ns))
            forename = _txt(a.find(".//tei:forename", ns))
            if not surname and not forename:
                pn = a.find(".//tei:persName", ns)
                full = _txt(pn)
                if full:
                    parts = full.split()
                    if len(parts) > 1:
                        ref_authors.append({"given": " ".join(parts[:-1]), "family": parts[-1]})
                    else:
                        ref_authors.append({"given": "", "family": full})
            else:
                ref_authors.append({"given": forename, "family": surname})

       
        ref_title_el = bs.find(".//tei:analytic/tei:title", ns) or bs.find(".//tei:monogr/tei:title", ns)
        ref_title = _txt(ref_title_el)

   
        cont_el = bs.find(".//tei:monogr/tei:title", ns)
        cont_title = _txt(cont_el)

 
        ref_year: Optional[str] = None
        ref_date_el = bs.find(".//tei:imprint/tei:date", ns)
        if ref_date_el is not None:
            when = ref_date_el.get("when")
            if when and len(when) >= 4:
                ref_year = when[:4]
            else:
                txt = _txt(ref_date_el)
                digits = "".join(c for c in txt if c.isdigit())
                ref_year = digits[:4] if digits else None

       
        ref_doi: Optional[str] = None
        idno_el = bs.find(".//tei:idno[@type='DOI']", ns)
        if idno_el is not None:
            ref_doi = _txt(idno_el)
        else:
            for i in bs.findall(".//tei:idno", ns):
                tt = _txt(i)
                if "10." in tt and "/" in tt:
                    ref_doi = tt.strip()
                    break

        ref_url: Optional[str] = None
    
        url_el = bs.xpath(".//tei:ref[@type='url'] | .//tei:ptr[@type='url']", namespaces=ns)
        if url_el:
            el = url_el[0]
            ref_url = el.get("target") or _txt(el)

        citations.append({
            "type": "article-journal",
            "title": ref_title or None,
            "author": ref_authors or None,
            "issued": {"date-parts": [[int(ref_year)]]} if (ref_year and ref_year.isdigit()) else None,
            "container-title": cont_title or None,
            "DOI": ref_doi or None,
            "URL": ref_url or None,
        })

   
    csl = {
        "type": "article-journal",
        "title": title or None,
        "author": authors or None,
        "issued": {"date-parts": [[int(year)]]} if (year and str(year).isdigit()) else None,
        "container-title": container_title or None,
        "DOI": doi or None,
    }
    csl = {k: v for k, v in csl.items() if v}

  
    citations = [{k: v for k, v in c.items() if v is not None and v != []} for c in citations]

    return csl, abstract, citations



@app.post("/api/upload", response_model=UploadResponse)
def upload_pdf(file: UploadFile = File(...)) -> UploadResponse:
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported.")

    file_id = str(uuid.uuid4())
    dest = os.path.join(settings.STORAGE_DIR, f"{file_id}.pdf")

    with open(dest, "wb") as f:
        shutil.copyfileobj(file.file, f)

  
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

   
    try:
        webhook_url = "http://n8n:5678/webhook/paper-uploaded"
        headers = {"x-n8n-secret": settings.N8N_INGEST_SECRET}
        payload = {"paper_id": file_id, "file_path": dest}
        requests.post(webhook_url, headers=headers, json=payload, timeout=10)
    except Exception:
       
        pass

    return UploadResponse(id=file_id, status="processing")


@app.post("/api/hooks/n8n/ingest")
async def ingest_from_n8n(req: Request):
    if req.headers.get("x-n8n-secret") != settings.N8N_INGEST_SECRET:
        raise HTTPException(status_code=403, detail="Forbidden")
    data = await req.json()
    body = IngestPayload(**data)
    return _ingest_impl(body)


def _ingest_impl(body: IngestPayload):
    
    if (not body.one_liner or not body.summary_150w) and body.ollama_response:
        try:
            parsed = json.loads(body.ollama_response)
            body.one_liner = body.one_liner or parsed.get("one_line_takeaway") or parsed.get("one_liner")
            body.summary_150w = body.summary_150w or parsed.get("summary_150w") or parsed.get("summary")
            if not body.keywords:
                kw = parsed.get("keywords") or parsed.get("tags")
                if isinstance(kw, list):
                    body.keywords = kw
        except Exception:
            if not body.one_liner and not body.summary_150w:
                body.error = (body.error or "Could not parse Ollama JSON")

    status = "ready" if not body.error else "error"
    with engine.begin() as conn:
        conn.execute(text("""
            UPDATE papers
               SET csl_json   = :csl,
                   one_liner  = :ol,
                   summary_150w = :sum,
                   keywords   = :kw,
                   citations  = :cit,
                   status     = :st,
                   updated_at = NOW()
             WHERE id = :id
        """), {
            "csl": json.dumps(body.csl_json) if body.csl_json else None,
            "ol": body.one_liner,
            "sum": body.summary_150w,
            "kw": json.dumps(body.keywords) if body.keywords else None,
            "cit": json.dumps(body.citations) if body.citations else None,
            "st": status,
            "id": body.paper_id
        })
    return {"ok": True, "status": status}


@app.post("/api/internal/tei-parse")
def tei_parse(body: TEIPayload):
    try:
        csl, abstract, citations = tei_to_csl_abstract_citations(body.tei_xml)
        return {"csl_json": csl, "abstract": abstract, "citations": citations}
    except etree.XPathEvalError as e:
        raise HTTPException(status_code=400, detail=f"TEI parse error: {e}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"TEI parse error: {e}")


@app.post("/api/internal/grobid-fulltext")
def grobid_fulltext(req: GrobidFulltextRequest):
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT file_path FROM papers WHERE id = :id"),
            {"id": req.paper_id}
        ).mappings().first()
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
        row = conn.execute(
            text("SELECT file_path, filename FROM papers WHERE id = :id"),
            {"id": paper_id}
        ).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(row["file_path"], media_type="application/pdf", filename=row["filename"])


@app.get("/api/papers")
def list_papers():
    with engine.begin() as conn:
        rows = conn.execute(text("""
            SELECT id, filename, status, created_at, updated_at
              FROM papers
          ORDER BY created_at DESC
        """)).mappings().all()
    return {"items": [dict(r) for r in rows]}


@app.get("/api/papers/{paper_id}")
def get_paper(paper_id: str):
    with engine.begin() as conn:
        row = conn.execute(text("SELECT * FROM papers WHERE id = :id"), {"id": paper_id}).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Not found")
    return dict(row)


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
