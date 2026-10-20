"use client";

import Link from "next/link";
import { useState } from "react";
import { api, describeError, Feature } from "@/lib/api";
import { ErrorNote, Pill } from "@/components/shell";

interface PageHit {
  document_id: string; document_title: string; page_number: number; snippet: string; score: number;
  publication_year: number | null; source: string; extraction_method: string; ocr_confidence: number | null;
}
interface Result {
  query: { expanded: string | null; commodities: string[] };
  retrieval_modes: string[];
  pages: { total: number; items: PageHit[] };
  features: { items: (Feature & { score: number })[] };
}

/** Render ts_headline's «…» markers as <strong> without trusting any HTML. */
function Snippet({ text }: { text: string }) {
  const parts = text.split(/(«[^»]*»)/g);
  return (
    <span className="snippet">
      {parts.map((p, i) => (p.startsWith("«") ? <strong key={i}>{p.slice(1, -1)}</strong> : <span key={i}>{p}</span>))}
    </span>
  );
}

export default function SearchPage() {
  const [res, setRes] = useState<Result | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function run(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    const commodity = (f.get("commodity") as string).trim();
    const body = {
      q: (f.get("q") as string).trim() || null,
      commodities: commodity ? [commodity] : [],
      country: (f.get("country") as string) || null,
      year_from: f.get("year_from") ? Number(f.get("year_from")) : null,
      year_to: f.get("year_to") ? Number(f.get("year_to")) : null,
      limit: 50,
    };
    setBusy(true);
    setError(null);
    try {
      setRes(await api<Result>("/search", { body }));
    } catch (err) {
      setError(describeError(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Search evidence</h1>
          <p>English and French. Commodity words include their minerals: “cuivre” also finds chalcopyrite and malachite.</p>
        </div>
      </div>
      <form className="panel" onSubmit={run}>
        <div className="row">
          <label style={{ flex: "3 1 300px" }}>Words, names or phrases<input name="q" placeholder={'e.g. Kambove "Mines subgroup" sondage'} /></label>
          <label>Commodity<input name="commodity" placeholder="copper, cobalt…" /></label>
        </div>
        <div className="row">
          <label>Country<select name="country" defaultValue=""><option value="">Any</option><option value="CD">DR Congo</option><option value="ZM">Zambia</option></select></label>
          <label>Published from<input name="year_from" type="number" min={1800} max={2100} /></label>
          <label>to<input name="year_to" type="number" min={1800} max={2100} /></label>
          <button disabled={busy}>{busy ? "Searching…" : "Search"}</button>
        </div>
      </form>
      <ErrorNote error={error} />

      {res && (
        <>
          <p className="small muted">
            Methods used: {res.retrieval_modes.join(", ").replaceAll("_", " ")}.
            {res.query.expanded && <> Query expanded to: <code>{res.query.expanded}</code></>}
          </p>
          <div className="grid2">
            <section className="panel" style={{ gridColumn: "span 2" }}>
              <h2>Pages <span className="muted small">({res.pages.total})</span></h2>
              {res.pages.items.length === 0 ? <div className="empty">No pages match. Try a broader word or remove a filter.</div> : (
                <table>
                  <thead><tr><th>Evidence</th><th>Document</th><th className="num">Page</th><th>Read by</th></tr></thead>
                  <tbody>
                    {res.pages.items.map((h) => (
                      <tr key={`${h.document_id}-${h.page_number}`}>
                        <td><Snippet text={h.snippet} /></td>
                        <td><Link href={`/documents/${h.document_id}?page=${h.page_number}`}>{h.document_title}</Link>
                          <div className="small muted">{h.source}{h.publication_year ? `, ${h.publication_year}` : ""}</div></td>
                        <td className="num"><Link href={`/documents/${h.document_id}?page=${h.page_number}`}>{h.page_number}</Link></td>
                        <td className="small">{h.extraction_method === "ocr" ? `OCR ${h.ocr_confidence ?? "?"}%` : "text layer"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </section>
          </div>
          <section className="panel">
            <h2>Map features</h2>
            {res.features.items.length === 0 ? <div className="empty">No geological features match.</div> : (
              <table>
                <thead><tr><th>Name</th><th>Commodities</th><th>Location</th><th>Review</th></tr></thead>
                <tbody>
                  {res.features.items.map((f) => (
                    <tr key={f.id}>
                      <td><Link href={`/map?feature=${f.id}`}>{f.name}</Link></td>
                      <td>{f.commodities.join(", ")}</td>
                      <td className="small">{f.location_precision.replaceAll("_", " ")}, {f.uncertainty_m != null ? `±${Math.round(f.uncertainty_m)} m` : "uncertainty unknown"}</td>
                      <td><Pill value={f.review_status} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </section>
        </>
      )}
    </>
  );
}
