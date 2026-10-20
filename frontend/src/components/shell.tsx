"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useSession } from "./session";

const NAV = [
  { href: "/", label: "Overview" },
  { href: "/documents", label: "Documents" },
  { href: "/search", label: "Search evidence" },
  { href: "/map", label: "Map" },
  { href: "/projects", label: "Projects" },
  { href: "/admin", label: "Organisation" },
];

export function Shell({ children }: { children: React.ReactNode }) {
  const { me, org, ready, signOut, switchOrg } = useSession();
  const path = usePathname();
  if (path === "/login") return <>{children}</>;
  if (!ready || !me) return <div className="main muted">Loading your session…</div>;
  const active = (href: string) => (href === "/" ? path === "/" : path.startsWith(href));
  return (
    <div className="shell">
      <nav className="rail" aria-label="Main">
        <div className="brand">
          Copperbelt Intelligence
          <small>Evidence before inference</small>
        </div>
        {NAV.map((n) => (
          <Link key={n.href} href={n.href} aria-current={active(n.href) ? "page" : undefined}>
            {n.label}
          </Link>
        ))}
        <div className="foot">
          {me.memberships.length > 1 ? (
            <select aria-label="Organisation" value={org?.org_id} onChange={(e) => switchOrg(e.target.value)}>
              {me.memberships.map((m) => (
                <option key={m.org_id} value={m.org_id}>{m.org_name}</option>
              ))}
            </select>
          ) : (
            <div>{org?.org_name}</div>
          )}
          <div>{me.full_name}, {org?.role}</div>
          <button className="quiet small" style={{ color: "#d6e4dd", marginTop: 8 }} onClick={signOut}>Sign out everywhere</button>
        </div>
      </nav>
      <main className="main" id="content">{children}</main>
    </div>
  );
}

export function Pill({ value }: { value: string }) {
  return <span className={`pill st-${value}`}>{value.replaceAll("_", " ")}</span>;
}

export function ErrorNote({ error }: { error: string | null }) {
  if (!error) return null;
  return <div className="notice error" role="alert">{error}</div>;
}
