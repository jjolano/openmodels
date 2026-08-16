import { useEffect, useMemo, useState } from "react";
import { Badge } from "@appica/ui-react/badge";
import {
  Card,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@appica/ui-react/card";
import { Input } from "@appica/ui-react/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@appica/ui-react/select";
import { Skeleton } from "@appica/ui-react/skeleton";

// ponytail: index.json field shapes — mirrors web/render.py render_browse
interface Bundle {
  name: string;
  slug: string;
  bundle_id: string;
  kind: string;
  family: string;
  variant: string;
  in_head: boolean;
  upstream_reachable?: boolean;
  introduced_by: { date: string; pr: number | null };
  files: { role: string; size: number }[];
  occurrences: { status: string }[];
}

const CHECKPOINT_NAME =
  /^([0-9a-f]{8})-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}(?:\/(\d+))?$/i;

const prettyName = (n: string) => {
  const m = CHECKPOINT_NAME.exec(n);
  return m ? `Training run ${m[1]}${m[2] ? ` · step ${m[2]}` : ""}` : n;
};

const humanBytes = (n: number) =>
  n < 2 ** 30 ? `${Math.round(n / 2 ** 20)} MB` : `${(n / 2 ** 30).toFixed(2)} GB`;

const statusVariant = (s: string) =>
  s === "merged"
    ? "success"
    : s === "pr_only"
      ? "warning"
      : ("secondary" as const);

function hideFallback() {
  document.getElementById("fallback-browse")?.setAttribute("hidden", "");
}

export function BrowseIsland() {
  const [bundles, setBundles] = useState<Bundle[] | null>(null);
  const [q, setQ] = useState("");
  const [kind, setKind] = useState("");
  const [status, setStatus] = useState("");

  useEffect(() => {
    hideFallback();
    fetch("index.json")
      .then((r) => r.json())
      .then((d) => {
        setBundles(
          [...d.bundles].sort((a: Bundle, b: Bundle) =>
            a.introduced_by.date < b.introduced_by.date ? 1 : -1,
          ),
        );
      })
      .catch(() => setBundles([]));
  }, []);

  const kinds = useMemo(
    () => [...new Set((bundles ?? []).map((b) => b.kind))].sort(),
    [bundles],
  );
  const statuses = useMemo(
    () =>
      [
        ...new Set(
          (bundles ?? []).flatMap((b) => b.occurrences.map((o) => o.status)),
        ),
      ].sort(),
    [bundles],
  );

  const filtered = useMemo(() => {
    const t = q.toLowerCase();
    return (bundles ?? []).filter((b) => {
      if (kind && b.kind !== kind) return false;
      const sts = b.occurrences.map((o) => o.status);
      if (status && !sts.includes(status)) return false;
      if (!t) return true;
      const roles = b.files.map((f) => f.role.replace("_", "-")).join(" ");
      return `${prettyName(b.name)} ${b.name} ${b.slug} ${b.bundle_id} ${roles}`
        .toLowerCase()
        .includes(t);
    });
  }, [bundles, q, kind, status]);

  if (!bundles) {
    return (
      <div className="flex flex-col gap-4">
        <Skeleton className="h-10 w-full" />
        <div className="grid grid-cols-[repeat(auto-fill,minmax(280px,1fr))] gap-4">
          {Array.from({ length: 6 }, (_, i) => (
            <Skeleton key={i} className="h-40 w-full" />
          ))}
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <p className="text-sm text-foreground-muted">
        This registry asserts{" "}
        <strong className="text-foreground">
          blob identity and upstream provenance
        </strong>
        . It does not assert that a model is safe to drive, or that two models
        are interchangeable. Each entry links to the exact upstream commit so
        you can verify every claim yourself.
      </p>
      <div className="flex flex-wrap gap-2">
        <Input
          className="min-w-52 flex-1"
          type="search"
          placeholder={`Search ${bundles.length} models by name or id…`}
          aria-label="Search models"
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
        <Select value={kind} onValueChange={(v) => setKind(v as string)}>
          <SelectTrigger className="w-40" aria-label="Filter by kind">
            <SelectValue placeholder="All kinds" />
          </SelectTrigger>
          <SelectContent>
            {kinds.map((k) => (
              <SelectItem key={k} value={k}>
                {k}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Select value={status} onValueChange={(v) => setStatus(v as string)}>
          <SelectTrigger className="w-44" aria-label="Filter by status">
            <SelectValue placeholder="Any status" />
          </SelectTrigger>
          <SelectContent>
            {statuses.map((s) => (
              <SelectItem key={s} value={s}>
                {s.replace("_", " ")}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
      <p className="text-sm text-foreground-muted">
        {filtered.length} of {bundles.length} models
      </p>
      <div className="grid grid-cols-[repeat(auto-fill,minmax(280px,1fr))] gap-4">
        {filtered.map((b) => {
          const sts = [...new Set(b.occurrences.map((o) => o.status))].sort();
          const size = b.files.reduce((s, f) => s + f.size, 0);
          return (
            <a key={b.bundle_id} href={`models/${b.bundle_id}.html`}>
              <Card frame className="h-full transition-colors hover:border-border-emphasis">
                <CardHeader>
                  <CardTitle className="text-base">
                    {prettyName(b.name)}
                  </CardTitle>
                  <CardDescription>
                    {b.introduced_by.date.slice(0, 10)} · {humanBytes(size)} ·{" "}
                    {b.kind}
                  </CardDescription>
                </CardHeader>
                <div className="flex flex-wrap gap-1">
                  {sts.map((s) => (
                    <Badge key={s} size="sm" variant={statusVariant(s)}>
                      {s.replace("_", " ")}
                    </Badge>
                  ))}
                  <Badge size="sm" variant="outline">
                    {b.family}
                  </Badge>
                  <Badge size="sm" variant="outline">
                    {b.variant}
                  </Badge>
                  {b.in_head && (
                    <Badge size="sm" variant="info">
                      in HEAD
                    </Badge>
                  )}
                  {b.upstream_reachable === false && (
                    <Badge size="sm" variant="warning">
                      upstream ref gone
                    </Badge>
                  )}
                </div>
                <p className="text-sm text-foreground-muted">
                  {b.files.map((f) => f.role.replace("_", "-")).join(" · ")}
                </p>
                <p className="font-mono text-xs text-foreground-subtle">
                  {b.bundle_id}
                  {b.introduced_by.pr && (
                    <span> · #{b.introduced_by.pr}</span>
                  )}
                </p>
              </Card>
            </a>
          );
        })}
      </div>
    </div>
  );
}
