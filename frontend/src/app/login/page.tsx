"use client";

import { useState } from "react";
import { api, describeError } from "@/lib/api";
import { useSession } from "@/components/session";
import { ErrorNote } from "@/components/shell";

export default function LoginPage() {
  const { signIn } = useSession();
  const [mode, setMode] = useState<"login" | "register">("login");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    setBusy(true);
    setError(null);
    try {
      const body =
        mode === "login"
          ? { email: f.get("email"), password: f.get("password") }
          : { email: f.get("email"), password: f.get("password"), full_name: f.get("full_name"), organisation_name: f.get("org") };
      const r = await api<{ access_token: string }>(`/auth/${mode}`, { body });
      await signIn(r.access_token);
    } catch (err) {
      setError(describeError(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div style={{ minHeight: "100vh", display: "grid", placeItems: "center", padding: 16 }}>
      <form onSubmit={submit} className="panel" style={{ width: "min(420px, 100%)" }}>
        <h1>Copperbelt Intelligence</h1>
        <p className="muted">Geological evidence you can trace back to the page it came from.</p>
        <ErrorNote error={error} />
        {mode === "register" && (
          <>
            <label>Your name<input name="full_name" required autoComplete="name" /></label>
            <label>Organisation<input name="org" required minLength={2} /></label>
          </>
        )}
        <label>Email<input name="email" type="email" required autoComplete="email" /></label>
        <label>
          Password
          <input name="password" type="password" required minLength={mode === "register" ? 12 : 1}
            autoComplete={mode === "login" ? "current-password" : "new-password"} />
        </label>
        {mode === "register" && <p className="small muted">At least 12 characters, mixing upper- and lower-case letters and a digit.</p>}
        <button type="submit" disabled={busy}>{mode === "login" ? "Sign in" : "Create organisation"}</button>{" "}
        <button type="button" className="quiet" onClick={() => setMode(mode === "login" ? "register" : "login")}>
          {mode === "login" ? "New organisation" : "I have an account"}
        </button>
      </form>
    </div>
  );
}
