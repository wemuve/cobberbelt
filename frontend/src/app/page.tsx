"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api, describeError, Doc, Job, Listing } from "@/lib/api";
import { ErrorNote, Pill } from "@/components/shell";

interface Overview {
  docs: Listing<Doc>;
  unreviewed: number;
  features: number;
  jobs: Job[];
  providers: { id: string; name: string; status: string; missing_configuration: string[] }[];
}

export default function OverviewPage() {
  const [d, setD] = useState<Overview | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    (async () => {
      try {
        const [docs, claims, features, jobs, prov] = await Promise.all([
          api<Listing<Doc>>("/documents?limit=8"),
          api<Listing<unknown>>("/claims?review_status=unreviewed&limit=1"),
          api<Listing<unknown>>("/geological-features?limit=1"),
          api<Job[]>("/jobs?limit=10"),
          api<{ providers: Overview["providers"] }>("/providers"),
        ]);
        setD({ docs, unreviewed: claims.total, features: features.total, jobs, providers: prov.providers });
      } catch (e) {
        setError(describeError(e));
      }
    })();
  }, []);

  if (error) return <ErrorNote error={error} />;
  if (!d) return <p className="muted">Loading…</p>;
  const failing = d.jobs.filter((j) => j.status === "dead" || j.status === "failed");

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Overview</h1>
          <p>What is in your evidence base and what needs a person’s attention.</p>
        </div>
        <Link className="btn" href="/documents">Add documents</Link>
      </div>

      {d.docs.total === 0 && (
        <div className="panel">
          <h2>Start with one authorised document</h2>
          <p>Register where your documents come from and on what basis you may use them, then upload a report or scan. Processing runs in the background; you can review what it found page by page.</p>
          <Link className="btn" href="/documents">Register a source and upload</Link>
        </div>
      )}

      <div className="grid2">
        <section className="panel">
          <h2>Needs review</h2>
          <p><strong className="num">{d.unreviewed}</strong> machine-extracted claims are unreviewed. Nothing reaches a map feature or an approved assessment until a person accepts it.</p>
          <p><strong className="num">{d.features}</strong> geological features on the map.</p>
          {failing.length > 0 && (
            <div className="notice error">{failing.length} processing job(s) failed. Open the document to see the error and retry.</div>
          )}
        </section>
        <section className="panel">
          <h2>External data connections</h2>
          <table>
            <tbody>
              {d.providers.map((p) => (
                <tr key={p.id}>
                  <td>{p.name}</td>
                  <td><Pill value={p.status} /></td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="small muted" style={{ marginTop: 8 }}>“Unconfigured” means no credentials are set; nothing is simulated.</p>
        </section>
      </div>

      <section className="panel">
        <h2>Recent documents</h2>
        {d.docs.items.length === 0 ? <div className="empty">No documents yet.</div> : (
          <table>
            <thead><tr><th>Title</th><th>Status</th><th className="num">Pages</th><th>Added</th></tr></thead>
            <tbody>
              {d.docs.items.map((doc) => (
                <tr key={doc.id}>
                  <td><Link href={`/documents/${doc.id}`}>{doc.title}</Link></td>
                  <td><Pill value={doc.status} /></td>
                  <td className="num">{doc.page_count ?? "–"}</td>
                  <td className="muted">{new Date(doc.created_at).toLocaleDateString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </>
  );
}
