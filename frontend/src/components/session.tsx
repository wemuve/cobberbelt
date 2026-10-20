"use client";

import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { api, Me, Membership, restoreSession, setOrgId, setSession } from "@/lib/api";

interface SessionValue {
  me: Me | null;
  org: Membership | null;
  ready: boolean;
  signIn: (token: string) => Promise<void>;
  signOut: () => Promise<void>;
  switchOrg: (orgId: string) => void;
  can: (minRole: Role) => boolean;
}

type Role = "viewer" | "reviewer" | "analyst" | "admin" | "owner";
const RANK: Record<Role, number> = { viewer: 0, reviewer: 1, analyst: 2, admin: 3, owner: 4 };

const Ctx = createContext<SessionValue | null>(null);
const ORG_KEY = "cbi.org";

function readOrg(): string | null {
  try {
    return window.localStorage.getItem(ORG_KEY);
  } catch {
    return null;
  }
}

export function SessionProvider({ children }: { children: React.ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [org, setOrg] = useState<Membership | null>(null);
  const [ready, setReady] = useState(false);
  const router = useRouter();
  const pathname = usePathname();

  const load = useCallback(async (token?: string) => {
    if (token) setSession(token, null);
    const profile = await api<Me>("/auth/me");
    const preferred = readOrg();
    const chosen = profile.memberships.find((m) => m.org_id === preferred) ?? profile.memberships[0] ?? null;
    setMe(profile);
    setOrg(chosen);
    setOrgId(chosen?.org_id ?? null);
  }, []);

  useEffect(() => {
    (async () => {
      if (await restoreSession()) {
        try {
          await load();
        } catch {
          /* fall through to login */
        }
      }
      setReady(true);
    })();
  }, [load]);

  useEffect(() => {
    if (ready && !me && pathname !== "/login") router.replace("/login");
  }, [ready, me, pathname, router]);

  const value: SessionValue = {
    me,
    org,
    ready,
    signIn: async (token) => {
      await load(token);
      router.replace("/");
    },
    signOut: async () => {
      try {
        await api("/auth/logout-all", { method: "POST" });
      } finally {
        setSession(null, null);
        setMe(null);
        setOrg(null);
        router.replace("/login");
      }
    },
    switchOrg: (id) => {
      const m = me?.memberships.find((x) => x.org_id === id) ?? null;
      setOrg(m);
      try {
        window.localStorage.setItem(ORG_KEY, id);
      } catch {
        /* storage unavailable */
      }
      window.location.reload();
    },
    can: (minRole) => !!org && RANK[org.role as Role] >= RANK[minRole],
  };
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useSession() {
  const v = useContext(Ctx);
  if (!v) throw new Error("useSession outside provider");
  return v;
}
