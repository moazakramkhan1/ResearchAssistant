import React, { useEffect, useState } from "react";
import axios from "axios";

const API = import.meta.env.VITE_API_BASE || "http://localhost:8000";

export default function App() {
  const [file, setFile] = useState(null);
  const [papers, setPapers] = useState([]);
  const [selected, setSelected] = useState(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState("");
  const [deletingId, setDeletingId] = useState("");
  const [refreshing, setRefreshing] = useState(false); // NEW: UI state for the Refresh button

  // --- REFRESH (now supports background "silent" refresh) ---
  const refresh = async (opts = { silent: false }) => {
    const silent = !!opts.silent;
    try {
      if (!silent) setRefreshing(true);
      const r = await axios.get(`${API}/api/papers`);
      const items = r.data.items || [];
      setPapers(items);

      // If a paper is open, refresh its details when status/updated_at changed
      if (selected?.id) {
        const row = items.find(p => p.id === selected.id);
        if (row) {
          const changed =
            row.status !== selected.status ||
            (row.updated_at && row.updated_at !== selected.updated_at);
          if (changed) {
            try {
              const r2 = await axios.get(`${API}/api/papers/${selected.id}`);
              setSelected(r2.data);
            } catch (_) { /* ignore detail fetch errors in silent refresh */ }
          }
        } else {
          // It might have been deleted elsewhere
          setSelected(null);
        }
      }
      return items;
    } catch (e) {
      if (!silent) setErr(e?.message || "Failed to load");
      return [];
    } finally {
      if (!silent) setRefreshing(false);
    }
  };

  useEffect(() => { refresh(); }, []);

  const onUpload = async (e) => {
    e.preventDefault();
    if (!file) return;
    setLoading(true);
    setErr("");
    try {
      const form = new FormData();
      form.append("file", file);
      await axios.post(`${API}/api/upload`, form, {
        headers: { "Content-Type": "multipart/form-data" },
      });
      setFile(null);
      // light polling after an upload to pick up processing→ready
      let tries = 0;
      const t = setInterval(async () => {
        tries++;
        const items = await refresh({ silent: true });
        const anyProcessing = items.some(p => p.status === "processing");
        if (!anyProcessing || tries > 10) clearInterval(t);
      }, 2000);
    } catch (e) {
      setErr(e?.response?.data?.detail || e?.message || "Upload failed");
    } finally {
      setLoading(false);
    }
  };

  const openDetail = async (id) => {
    try {
      const r = await axios.get(`${API}/api/papers/${id}`);
      setSelected(r.data);
    } catch (e) {
      setErr(e?.message || "Failed to load paper");
    }
  };

  const deletePaper = async (id) => {
    if (!id) return;
    const yes = window.confirm("Delete this paper permanently?");
    if (!yes) return;

    setErr("");
    setDeletingId(id);
    try {
      await axios.delete(`${API}/api/papers/${id}`);
      if (selected?.id === id) setSelected(null);
      await refresh(); // reflect deletion immediately
    } catch (e) {
      setErr(e?.response?.data?.detail || e?.message || "Delete failed");
    } finally {
      setDeletingId("");
    }
  };

  const fmtAuthors = (a = []) =>
    a.map(x => `${x.given || ""} ${x.family || ""}`.trim()).filter(Boolean).join(", ");

  // Close on Esc (UI only)
  useEffect(() => {
    const onKey = (e) => {
      if (e.key === "Escape" && selected) setSelected(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [selected]);

  // --- BACKGROUND AUTO-POLLING while any item is "processing" ---
  useEffect(() => {
    const hasProcessing = papers.some(p => p.status === "processing");
    if (!hasProcessing) return;

    const iv = setInterval(() => {
      if (!document.hidden) refresh({ silent: true });
    }, 2000);

    const onVis = () => { if (!document.hidden) refresh({ silent: true }); };
    document.addEventListener("visibilitychange", onVis);

    return () => {
      clearInterval(iv);
      document.removeEventListener("visibilitychange", onVis);
    };
  }, [papers]); // re-evaluates when list changes

  return (
    <div className="app-root">
      <style>{`
        :root{
          --bg:#0a0f22; --bg-2:#0e1430; --panel:#0f1637; --panel-2:#0d1330; --card:#11183d;
          --soft:#cfe1ff; --text:#e8ecff; --muted:#9bb0e8; --line:rgba(255,255,255,0.07); --line-2:rgba(255,255,255,0.10);
          --accent-1:#7c5cff; --accent-2:#4dd0ff; --ok:#06d6a0; --warn:#ffd166; --danger:#ff7b7b;
          --shadow:0 20px 60px rgba(0,0,0,.35); --shadow-soft:0 10px 35px rgba(0,0,0,.25); --radius:14px;
        }
        .app-root{ min-height:100vh; background:
          radial-gradient(1200px 600px at 70% -10%, rgba(76, 159, 255, .08), transparent 60%),
          radial-gradient(900px 500px at -20% 30%, rgba(124, 92, 255, .10), transparent 60%),
          var(--bg); color:var(--text); -webkit-font-smoothing:antialiased; -moz-osx-font-smoothing:grayscale; }
        header.app-header{ padding:22px 20px; border-bottom:1px solid var(--line); position:sticky; top:0; z-index:5;
          backdrop-filter: blur(4px); background: linear-gradient(180deg, rgba(10,15,34,.75), rgba(10,15,34,.55)); }
        .container{ max-width:1100px; margin:0 auto; }
        .brand{ display:flex; align-items:center; gap:12px; }
        .logo{ width:38px; height:38px; border-radius:10px; background: conic-gradient(from 180deg, var(--accent-1), var(--accent-2));
          box-shadow: 0 8px 30px rgba(124,92,255,.35); }
        .tagline{opacity:.75; font-size:13px;}
        .toolbar{ display:flex; gap:12px; align-items:center; background:linear-gradient(180deg, rgba(255,255,255,.03), rgba(255,255,255,.01));
          border:1px solid var(--line); border-radius:var(--radius); padding:16px; box-shadow:var(--shadow-soft); }
        .filebox{ flex:1; display:flex; align-items:center; gap:10px; background:var(--panel-2); border:1px solid var(--line);
          border-radius:12px; padding:10px 12px; }
        .filebox input[type="file"]{ width:100%; color:var(--soft); border:0; background:transparent; }
        .btn{ border:0; border-radius:12px; padding:10px 16px; font-weight:700; letter-spacing:.2px;
          transition:transform .1s ease, box-shadow .2s ease, opacity .2s ease; cursor:pointer; }
        .btn:disabled{ opacity:.6; cursor:not-allowed; }
        .btn-primary{ background: linear-gradient(135deg, var(--accent-1), var(--accent-2)); color:#091026; box-shadow:0 14px 34px rgba(124,92,255,.33); }
        .btn-secondary{ background:var(--panel-2); color:var(--soft); border:1px solid var(--line); }
        .btn:hover:not(:disabled){ transform:translateY(-1px) scale(1.01); }
        .grid{ display:grid; grid-template-columns: var(--cols, 1fr); gap:18px; margin-top:22px; }
        .card{ background: linear-gradient(180deg, rgba(255,255,255,.02), rgba(255,255,255,.00)); border:1px solid var(--line);
          border-radius:var(--radius); overflow:hidden; box-shadow:var(--shadow-soft); }
        .card-head{ padding:14px 16px; display:flex; align-items:center; justify-content:space-between;
          border-bottom:1px dashed var(--line); background:linear-gradient(180deg, rgba(255,255,255,.02), transparent); gap:10px; }
        .list{ max-height:60vh; overflow:auto; }
        .row{ display:grid; grid-template-columns:1fr auto auto auto; gap:12px; padding:12px 16px; align-items:center;
          border-bottom:1px dashed var(--line); transition:background .15s ease, transform .06s ease; cursor:pointer; }
        .row:hover{ background:rgba(255,255,255,.03); transform:translateY(-1px); }
        .status{ font-size:12px; padding:4px 10px; border-radius:999px; border:1px solid var(--line); background:#00000022; }
        .status.ok{ color:var(--ok); background:rgba(6,214,160,.08); }
        .status.proc{ color:var(--warn); background:rgba(255,209,102,.10); }
        .badge{ font-size:12px; opacity:.7; border:1px solid var(--line); padding:4px 10px; border-radius:999px; }
        .meta{ display:flex; gap:10px; flex-wrap:wrap; margin-top:8px; }
        .detail{ padding:16px; display:grid; gap:12px; }
        .label{ color:#b8c6ff; font-weight:700; }
        .muted{ color:var(--muted); }
        .link{ color:var(--accent-2); text-decoration:none; font-weight:600; }
        .error{ margin-top:10px; color:var(--danger); background:rgba(255,123,123,.12); border:1px solid rgba(255,123,123,.25);
          padding:10px 12px; border-radius:12px; }
        .icon-btn{ display:inline-flex; align-items:center; justify-content:center; width:32px; height:32px; border-radius:10px;
          background:var(--panel-2); color:var(--soft); border:1px solid var(--line); cursor:pointer;
          transition:transform .12s ease, background .12s ease, opacity .2s; }
        .icon-btn:hover{ transform:translateY(-1px); background:rgba(255,255,255,.04); }
        .icon-btn.danger{ color:var(--danger); border-color:rgba(255,123,123,.35); }
        .icon-btn.danger:hover{ background:rgba(255,123,123,.08); }
        .drawer{ position:relative; }
        @media (max-width: 979px){
          .grid.detail-layout{ --cols: 1fr; }
          .drawer{ position: fixed; inset: 0; z-index: 50; background: rgba(6,10,25,.45); display:flex; justify-content:flex-end; }
          .drawer .card{ height:100%; width:min(520px, 100%); border-left:1px solid var(--line); border-radius:0; box-shadow: var(--shadow);
            animation: slideIn .18s ease-out; }
          @keyframes slideIn{ from{ transform:translateX(12px); opacity:0; } to{ transform:translateX(0); opacity:1; } }
        }
      `}</style>

      <header className="app-header">
        <div className="container" style={{display:"flex",alignItems:"center",justifyContent:"space-between"}}>
          <div className="brand">
            <div className="logo" />
            <h1 style={{margin:0, fontSize:20, letterSpacing:.3}}>Research Assistant</h1>
          </div>
          <div className="tagline">Local · Private · Fast</div>
        </div>
      </header>

      <main className="container" style={{padding:"22px 20px"}}>
        <div className="toolbar">
          <div className="filebox">
            <input type="file" accept="application/pdf" onChange={(e)=>setFile(e.target.files?.[0])} />
          </div>
          <button className="btn btn-primary" onClick={() => refresh()} disabled={refreshing}>
            {refreshing ? "Refreshing..." : "Refresh"}
          </button>
          <button className="btn btn-secondary" onClick={onUpload} disabled={loading || !file}>
            {loading ? "Uploading..." : "Upload"}
          </button>
        </div>

        {err && <div className="error">{err}</div>}

        <div className={`grid ${selected ? "detail-layout" : ""}`}>
          {/* List */}
          <div className="card">
            <div className="card-head">
              <div style={{fontWeight:800, color:"#cfe1ff"}}>Papers</div>
              <div className="badge">{papers.length} items</div>
            </div>
            <div className="list">
              {papers.map(p => (
                <div key={p.id} className="row" onClick={() => openDetail(p.id)}>
                  <div>
                    <div style={{color:"#e8ecff", fontWeight:600, overflow:"hidden", textOverflow:"ellipsis", whiteSpace:"nowrap"}}>
                      {p.filename}
                    </div>
                    <div className="muted" style={{fontSize:12}}>
                      {p.id.slice(0,8)} · {new Date(p.created_at).toLocaleString()}
                    </div>
                  </div>
                  <div className={`status ${p.status === "processing" ? "proc" : "ok"}`}>{p.status}</div>
                  <div className="muted" style={{fontSize:12}}>Open</div>
                  <button
                    className="icon-btn danger"
                    title={deletingId === p.id ? "Deleting..." : "Delete"}
                    aria-label="Delete"
                    onClick={(e) => { e.stopPropagation(); deletePaper(p.id); }}
                    disabled={!!deletingId}
                  >
                    {deletingId === p.id ? "…" : "🗑"}
                  </button>
                </div>
              ))}
            </div>
          </div>

          {/* Detail drawer/panel */}
          {selected && (
            <div
              className="drawer"
              onClick={(e) => { if (e.target === e.currentTarget) setSelected(null); }}
            >
              <div className="card" onClick={(e)=>e.stopPropagation()}>
                <div className="card-head">
                  <div style={{minWidth:0}}>
                    <div style={{fontSize:18, fontWeight:900, color:"#e8ecff", lineHeight:1.25, overflow:"hidden", textOverflow:"ellipsis"}}>
                      {selected?.csl_json?.title || selected.filename}
                    </div>
                    <div className="muted" style={{marginTop:6, fontSize:13}}>
                      Authors: {fmtAuthors(selected?.csl_json?.author)}
                    </div>
                  </div>
                  <div style={{display:"flex", gap:8}}>
                    <button
                      className="icon-btn danger"
                      aria-label="Delete"
                      title={deletingId === selected?.id ? "Deleting..." : "Delete"}
                      onClick={() => deletePaper(selected?.id)}
                      disabled={!!deletingId}
                    >
                      {deletingId === selected?.id ? "…" : "🗑"}
                    </button>
                    <button
                      className="icon-btn"
                      aria-label="Close details"
                      title="Close"
                      onClick={() => setSelected(null)}
                    >
                      ×
                    </button>
                  </div>
                </div>

                <div className="detail">
                  <div><span className="label">Year:</span> {selected?.csl_json?.issued?.["date-parts"]?.[0]?.[0] || "-"}</div>
                  <div><span className="label">One-liner:</span> <span style={{color:"#e8ecff"}}>{selected.one_liner || "-"}</span></div>
                  <div>
                    <span className="label">Summary:</span>
                    <div style={{whiteSpace:"pre-wrap", marginTop:8, background:"var(--card)", border:"1px solid var(--line)", borderRadius:12, padding:"12px"}}>
                      {selected.summary_150w || "-"}
                    </div>
                  </div>
                  <div>
                    <span className="label">Keywords:</span>
                    <div className="meta">
                      {(selected.keywords || []).length
                        ? (selected.keywords || []).map((k, i) => (
                          <span key={i} className="badge" style={{borderColor:"var(--line-2)"}}>{k}</span>
                        ))
                        : <span className="muted">-</span>}
                    </div>
                  </div>
                  <div>
                    <a className="link" href={`${API}/api/files/${selected.id}`} target="_blank" rel="noreferrer">
                      Open PDF
                    </a>
                  </div>
                </div>
              </div>
            </div>
          )}
        </div>
      </main>
    </div>
  );
}
