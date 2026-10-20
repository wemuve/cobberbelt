"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { api, describeError, Doc, Listing, Source } from "@/lib/api";
import { useSession } from "@/components/session";
import { ErrorNote, Pill } from "@/components/shell";

const ACCESS = [
  ["public_domain", "Public domain"],
  ["open_licence", "Open licence permits this use"],
  ["authorised", "Written authorisation on file"],
  ["pending", "Access requested (cannot ingest yet)"],
  ["unknown", "Not yet established (cannot ingest)"],
] as const;
const INGESTIBLE = new Set(["public_domain", "open_licence", "authorised"]);

export default function DocumentsPage() {
  const { can } = useSession();
  const [docs, setDocs] = useState<Listing<Doc> | null>(null);
  const [sources, setSources] = useState<Source[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [q, setQ] = useState("");

  const load = useCallback(async () => {
    try {
      const [d, s] = await Promise.all([api<Listing<Doc>>(`/documents?limit=100${q ? `&q=${encodeURIComponent(q)}` : ""}`), api<Source[]>("/sources")]);
      setDocs(d);
      setSources(s);
    } catch (e) {
      setError(describeError(e));
    }
  }, [q]);

  useEffect(() => {
    load();
  }, [load]);

  // Poll while anything is still processing.
  useEffect(() => {
    if (!docs?.items.some((d) => d.status === "queued" || d.status === "processing")) return;
    const t = setTimeout(load, 2500);
    return () => clearTimeout(t);
  }, [docs, load]);

  async function addSource(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const form = e.currentTarget;
    const f = new FormData(form);
    setError(null);
    try {
      await api("/sources", {
        body: {
          name: f.get("name"), institution: f.get("institution") || null, access_status: f.get("access_status"),
          licence: f.get("licence") || null, country: (f.get("country") as string) || null,
        },
      });
      form.reset();
      setNotice("Source registered.");
      load();
    } catch (err) {
      setError(describeError(err));
    }
  }

  async function uploadDoc(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const form = e.currentTarget;
    const f = new FormData(form);
    for (const [k, v] of [...f.entries()]) if (v === "") f.delete(k);
    setBusy(true);
    setError(null);
    try {
      const r = await api<{ document: Doc; created: boolean }>("/documents", { form: f });
      setNotice(r.created ? `Uploaded “${r.document.title}”. Processing has started.` : "This exact file is already in your organisation; opened the existing record.");
      form.reset();
      load();
    } catch (err) {
      setError(describeError(err));
    } finally {
      setBusy(false);
    }
  }

  const usable = sources.filter((s) => INGESTIBLE.has(s.access_status));

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Documents</h1>
          <p>Originals are stored unchanged and identified by their SHA-256 checksum.</p>
        </div>
      </div>
      <ErrorNote error={error} />
      {notice && <div className="notice ok" role="status">{notice}</div>}

      {can("analyst") && (
        <div className="grid2">
          <form className="panel" onSubmit={uploadDoc}>
            <h2>Upload a document</h2>
            {usable.length === 0 ? (
              <p className="muted">Register a source with an established access basis first. Documents can only be ingested from sources you are permitted to use.</p>
            ) : (
              <>
                <label>File (PDF, PNG, JPEG, TIFF or text)<input name="file" type="file" required accept=".pdf,.png,.jpg,.jpeg,.tif,.tiff,.txt" /></label>
                <label>Source
                  <select name="source_id" required>{usable.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}</select>
                </label>
                <label>Title<input name="title" required maxLength={500} /></label>
                <div className="row">
                  <label>Year published<input name="publication_year" type="number" min={1800} max={2100} /></label>
                  <label>Country (ISO)<input name="country" maxLength={2} placeholder="CD or ZM" /></label>
                </div>
                <div className="row">
                  <label>Visibility
                    <select name="classification" defaultValue="institutional">
                      <option value="institutional">Everyone in the organisation</option>
                      <option value="project_restricted">Members of linked projects</option>
                      <option value="confidential">Admins and named people only</option>
                    </select>
                  </label>
                  <label>Authors (separate with ;)<input name="authors" /></label>
                </div>
                <button disabled={busy}>{busy ? "Uploading…" : "Upload and process"}</button>
              </>
            )}
          </form>
          <form className="panel" onSubmit={addSource}>
            <h2>Register a source</h2>
            <label>Name<input name="name" required minLength={2} placeholder="e.g. Client data room — Project X" /></label>
            <label>Institution<input name="institution" /></label>
            <label>Access basis
              <select name="access_status" defaultValue="authorised">{ACCESS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select>
            </label>
            <div className="row">
              <label>Licence or agreement reference<input name="licence" /></label>
              <label>Country<input name="country" maxLength={2} /></label>
            </div>
            <button className="quiet">Register source</button>
          </form>
        </div>
      )}

      <section className="panel">
        <div className="row" style={{ marginBottom: 10 }}>
          <h2 style={{ margin: 0 }}>All documents {docs && <span className="muted small">({docs.total})</span>}</h2>
          <input aria-label="Filter by title" placeholder="Filter by title" value={q} onChange={(e) => setQ(e.target.value)} style={{ maxWidth: 300 }} />
        </div>
        {!docs ? <p className="muted">Loading…</p> : docs.items.length === 0 ? (
          <div className="empty">No documents match. Upload one above to begin.</div>
        ) : (
          <table>
            <thead><tr><th>Title</th><th>Source</th><th>Status</th><th className="num">Pages</th><th>Visibility</th><th>Year</th></tr></thead>
            <tbody>
              {docs.items.map((d) => (
                <tr key={d.id}>
                  <td><Link href={`/documents/${d.id}`}>{d.title}</Link><div className="small muted">{d.original_filename}</div></td>
                  <td className="small">{sources.find((s) => s.id === d.source_id)?.name ?? "–"}</td>
                  <td><Pill value={d.status} /></td>
                  <td className="num">{d.page_count ?? "–"}</td>
                  <td className="small">{d.classification.replaceAll("_", " ")}</td>
                  <td>{d.publication_year ?? "–"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </>
  );
}
