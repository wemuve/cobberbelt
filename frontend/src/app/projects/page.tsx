"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api, describeError, Project } from "@/lib/api";
import { useSession } from "@/components/session";
import { ErrorNote } from "@/components/shell";

export default function ProjectsPage() {
  const { can } = useSession();
  const [projects, setProjects] = useState<Project[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = () => api<Project[]>("/projects").then(setProjects).catch((e) => setError(describeError(e)));
  useEffect(() => { load(); }, []);

  async function create(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const form = e.currentTarget;
    const f = new FormData(form);
    try {
      await api("/projects", {
        body: {
          name: f.get("name"), description: f.get("description") || null, country: f.get("country") || null,
          commodities: String(f.get("commodities") || "").split(",").map((s) => s.trim()).filter(Boolean),
        },
      });
      form.reset();
      load();
    } catch (err) {
      setError(describeError(err));
    }
  }

  return (
    <>
      <div className="page-head">
        <div><h1>Projects</h1><p>Each project gathers the evidence, risks and open questions for one area or asset.</p></div>
      </div>
      <ErrorNote error={error} />
      {can("analyst") && (
        <form className="panel" onSubmit={create}>
          <h2>New project</h2>
          <div className="row">
            <label style={{ flex: "2 1 260px" }}>Name<input name="name" required minLength={2} /></label>
            <label>Country<select name="country" defaultValue=""><option value="">–</option><option value="CD">DR Congo</option><option value="ZM">Zambia</option></select></label>
            <label>Commodities<input name="commodities" placeholder="copper, cobalt" /></label>
          </div>
          <label>Purpose<textarea name="description" placeholder="What decision is this project supporting?" /></label>
          <button>Create project</button>
        </form>
      )}
      <section className="panel">
        {!projects ? <p className="muted">Loading…</p> : projects.length === 0 ? (
          <div className="empty">No projects you can see yet. Projects are visible to their members and to administrators.</div>
        ) : (
          <table>
            <thead><tr><th>Project</th><th>Country</th><th>Commodities</th><th>Stage</th><th>Your role</th></tr></thead>
            <tbody>{projects.map((p) => (
              <tr key={p.id}>
                <td><Link href={`/projects/${p.id}`}>{p.name}</Link></td><td>{p.country ?? "–"}</td>
                <td>{p.commodities.join(", ") || "–"}</td><td>{p.stage.replaceAll("_", " ")}</td><td>{p.my_role ?? "–"}</td>
              </tr>
            ))}</tbody>
          </table>
        )}
      </section>
    </>
  );
}
