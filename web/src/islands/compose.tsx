import { useEffect, useMemo, useState } from "react";
import { Badge } from "@appica/ui-react/badge";
import { Button } from "@appica/ui-react/button";
import { Card } from "@appica/ui-react/card";
import { Checkbox } from "@appica/ui-react/checkbox";
import { CopyButton } from "@appica/ui-react/copy-button";
import { Input } from "@appica/ui-react/input";
import { Skeleton } from "@appica/ui-react/skeleton";

// ponytail: mirrors index/code.py + COMPOSE_JS — shapes are append-only, roles are encoding order
const SHAPES = [
  ["vision", "on_policy"],
  ["vision", "on_policy", "off_policy"],
  ["supercombo"],
  ["vision", "off_policy"],
  ["big_vision", "big_on_policy"],
  ["big_vision", "big_on_policy", "big_off_policy"],
  ["big_supercombo"],
  ["dmonitoring"],
  ["navmodel"],
] as const;
const VERSION = 3;
const ALPHABET = "0123456789ACDEFHJKMNPRVWXY";

interface FileRec {
  oid: string;
  size: number;
  filenames: string[];
  metadata?: {
    lineage?: { self?: string; vision?: string };
    input_shapes?: Record<string, number[]>;
  };
}
interface BundleMember {
  oid: string;
  role: string;
}
interface BundleRec {
  name: string;
  in_head: boolean;
  introduced_by: { date: string };
  occurrences: { status: string }[];
  files: BundleMember[];
}
interface Catalog {
  files: FileRec[];
  bundles: BundleRec[];
  attested_pairings?: [string, string][];
}

const ck = (f: FileRec) => {
  const l = f.metadata?.lineage ?? {};
  return l.self ?? l.vision ?? null;
};
const seam = (f: FileRec) => f.metadata?.input_shapes?.features_buffer?.[2] ?? null;
const variantOf = (f: FileRec) =>
  f.filenames.some((n) => n.startsWith("big_")) ? "big" : "standard";
const policyRole = (f: FileRec) =>
  f.filenames.some((n) => n.includes("off_policy")) ? "off_policy" : "on_policy";
const optionsOf = (files: FileRec[], role: string) =>
  files.filter((f) =>
    f.filenames.some((n) =>
      role === "vision" ? n.includes("vision") : n.includes("policy") && policyRole(f) === role,
    ),
  );

function b26(bytes: number[], chars: number) {
  let v = 0n;
  for (const b of bytes) v = (v << 8n) | BigInt(b);
  let out = "";
  const B = BigInt(ALPHABET.length);
  for (let j = 0; j < chars; j++) {
    out = ALPHABET[Number(v % B)] + out;
    v /= B;
  }
  return out;
}
const charsFor = (n: number) => Math.ceil((n * 8) / Math.log2(ALPHABET.length));
const sha256 = async (s: string) =>
  Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(s))));

async function makeCode(sel: Record<string, string>) {
  const roles = Object.keys(sel);
  const si = SHAPES.findIndex(
    (s) => s.length === roles.length && s.every((r) => roles.includes(r)),
  );
  if (si < 0) return "(no code shape for this combination)";
  const body: number[] = [(VERSION << 5) | si];
  for (const r of SHAPES[si]) {
    const hex = sel[r].slice(0, 6);
    for (let i = 0; i < 6; i += 2) body.push(parseInt(hex.slice(i, i + 2), 16));
  }
  const digest = await sha256(
    [...roles].sort().map((r) => `${r}:${sel[r]}`).join(""),
  );
  body.push(...digest.slice(0, 1));
  return `OM3-${b26(body, charsFor(body.length)).replace(/.{4}/g, "$&-").slice(0, -1)}`;
}

const CHECKPOINT_NAME =
  /^([0-9a-f]{8})-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}(?:\/(\d+))?$/i;
const prettyName = (n: string) => {
  const m = CHECKPOINT_NAME.exec(n);
  return m ? `Training run ${m[1]}${m[2] ? ` · step ${m[2]}` : ""}` : n;
};

// per-oid facts come from the newest bundle shipping each oid
interface OidFacts { date: string; name: string; statuses: string[]; inHead: boolean }

type Role = "vision" | "on_policy" | "off_policy";
type Sel = Partial<Record<Role, string>>;

function hideFallback() {
  document.getElementById("fallback-compose")?.setAttribute("hidden", "");
}

export function ComposeIsland() {
  const [cat, setCat] = useState<Catalog | null>(null);
  const [facts, setFacts] = useState<Record<string, OidFacts>>({});
  const [sel, setSel] = useState<Sel>({});
  const [filters, setFilters] = useState({ presets: "", vision: "", on_policy: "", off_policy: "" });
  const [attestedOnly, setAttestedOnly] = useState(false);
  const [code, setCode] = useState("");

  useEffect(() => {
    hideFallback();
    fetch("index.json")
      .then((r) => r.json())
      .then((d: Catalog) => {
        const f: Record<string, OidFacts> = {};
        for (const b of d.bundles ?? []) {
          const dt = b.introduced_by?.date ?? "";
          for (const m of b.files ?? []) {
            if (dt > (f[m.oid]?.date ?? "")) {
              f[m.oid] = {
                date: dt,
                name: prettyName(b.name ?? ""),
                statuses: [...new Set(b.occurrences.map((o) => o.status))].sort(),
                inHead: !!b.in_head,
              };
            }
          }
        }
        setFacts(f);
        setCat(d);
      })
      .catch(() => setCat({ files: [], bundles: [] }));
  }, []);

  const byOid = useMemo(
    () => Object.fromEntries((cat?.files ?? []).map((f) => [f.oid, f])),
    [cat],
  );
  const attested = (vc: string | null, pc: string | null) =>
    !!vc && !!pc && !!cat?.attested_pairings?.some((p) => p[0] === vc && p[1] === pc);
  const vCk = sel.vision ? ck(byOid[sel.vision]) : null;
  const vVariant = sel.vision ? variantOf(byOid[sel.vision]) : null;

  const variantsAgree =
    sel.vision &&
    sel.on_policy &&
    variantOf(byOid[sel.vision]) === variantOf(byOid[sel.on_policy]) &&
    (!sel.off_policy || variantOf(byOid[sel.off_policy]) === vVariant);

  useEffect(() => {
    if (!sel.vision || !sel.on_policy || !variantsAgree) {
      setCode("");
      return;
    }
    const s: Record<string, string> = { vision: sel.vision, on_policy: sel.on_policy };
    if (sel.off_policy) s.off_policy = sel.off_policy;
    let live = true;
    makeCode(s).then((c) => live && setCode(c));
    return () => {
      live = false;
    };
  }, [sel, variantsAgree]);

  if (!cat) {
    return (
      <div className="flex flex-col gap-4">
        <Skeleton className="h-10 w-full" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }

  const presets = cat.bundles
    .filter((b) => {
      const roles = new Set(b.files.map((m) => m.role));
      return roles.has("vision") && roles.has("on_policy");
    })
    .sort((a, b) => (a.introduced_by.date < b.introduced_by.date ? 1 : -1))
    .filter((b) =>
      `${prettyName(b.name)} ${b.introduced_by.date}`
        .toLowerCase()
        .includes(filters.presets.toLowerCase()),
    );

  const pick = (role: Role, oid: string) =>
    setSel((s) => ({ ...s, [role]: s[role] === oid ? undefined : oid }));

  const pickGrid = (role: Role, required: boolean) => {
    const term = filters[role].toLowerCase();
    const cards = optionsOf(cat.files, role)
      .filter((f) => role === "vision" || !vVariant || variantOf(f) === vVariant)
      .filter((f) => {
        const o = facts[f.oid];
        return `${o?.name ?? f.filenames[0]} ${f.filenames.join(" ")} ${o?.date ?? ""}`
          .toLowerCase()
          .includes(term);
      })
      .sort((a, b) => {
        if (vCk) {
          const d = Number(attested(vCk, ck(b))) - Number(attested(vCk, ck(a)));
          if (d) return d;
        }
        return (facts[a.oid]?.date ?? "") < (facts[b.oid]?.date ?? "") ? 1 : -1;
      })
      .filter((f) => role === "vision" || !attestedOnly || (vCk && attested(vCk, ck(f))));
    return (
      <div className="flex min-w-0 flex-1 flex-col gap-2">
        <p className="text-sm text-foreground-muted">
          {role.replace("_", "-")}{" "}
          <span className="text-foreground-subtle">{required ? "required" : "optional"}</span>
        </p>
        <Input
          type="search"
          inputSize="sm"
          placeholder={`filter ${role.replace("_", "-")} components…`}
          aria-label={`Filter ${role.replace("_", "-")} components`}
          value={filters[role]}
          onChange={(e) => setFilters({ ...filters, [role]: e.target.value })}
        />
        <div className="flex max-h-96 flex-col gap-2 overflow-y-auto pr-1">
          {cards.map((f) => {
            const o = facts[f.oid];
            const selected = sel[role] === f.oid;
            return (
              <Card
                key={f.oid}
                frame
                role="button"
                tabIndex={0}
                aria-pressed={selected}
                className={`cursor-pointer gap-1 transition-colors ${
                  selected
                    ? "border-primary bg-primary-subtle"
                    : "hover:border-border-emphasis"
                }`}
                onClick={() => pick(role, f.oid)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    pick(role, f.oid);
                  }
                }}
              >
                <p className="text-sm font-medium">{o?.name ?? f.filenames[0]}</p>
                <p className="font-mono text-xs text-foreground-muted">{f.filenames[0]}</p>
                <p className="text-xs text-foreground-subtle">
                  {o?.date.slice(0, 10) || "undated"} · {Math.round(f.size / 1048576)} MB
                </p>
                <div className="flex flex-wrap gap-1">
                  {(o?.statuses ?? []).map((s) => (
                    <Badge key={s} size="xs" variant={s === "merged" ? "success" : "warning"}>
                      {s.replace("_", " ")}
                    </Badge>
                  ))}
                  {o?.inHead && <Badge size="xs" variant="info">in HEAD</Badge>}
                  <Badge size="xs" variant="outline">{variantOf(f)}</Badge>
                  {vCk && attested(vCk, ck(f)) && (
                    <Badge size="xs" variant="success">attested</Badge>
                  )}
                </div>
              </Card>
            );
          })}
        </div>
      </div>
    );
  };

  const selList = (["vision", "on_policy", "off_policy"] as Role[]).filter((r) => sel[r]);
  const notes = (() => {
    if (!sel.vision || !sel.on_policy) return null;
    if (!variantsAgree)
      return {
        warn: true,
        text: `Components disagree on hardware target — ${variantOf(byOid[sel.vision])} and ${variantOf(byOid[sel.on_policy])} run on different devices (QCOM vs USBGPU/AMD). Compose refuses this combination.`,
      };
    const pc1 = ck(byOid[sel.on_policy]);
    const pc2 = sel.off_policy ? ck(byOid[sel.off_policy]) : null;
    const a1 = attested(vCk, pc1);
    const a2 = sel.off_policy ? attested(vCk, pc2) : null;
    if (!vCk || !pc1 || (sel.off_policy && !pc2))
      return { warn: true, text: "Lineage unknown for one component — whether these were built for each other cannot be determined." };
    if (sel.off_policy && a1 && a2)
      return { warn: false, text: "Attested pairing. All three components shipped together upstream." };
    if (sel.off_policy && a1)
      return { warn: true, text: "on-policy attested with this vision; the off-policy component is cross-lineage." };
    if (sel.off_policy && a2)
      return { warn: true, text: "off-policy attested with this vision; the on-policy component is cross-lineage." };
    if (a1)
      return { warn: false, text: "Attested pairing. These components shipped together upstream." };
    return {
      warn: true,
      text: "Cross-lineage. No shipped pairing is recorded in this catalog. The latent between them is untyped, so this will load and run regardless of whether the numbers mean the same thing.",
    };
  })();

  return (
    <div className="flex flex-col gap-8">
      <div className="flex flex-col gap-2">
        <h1 className="text-2xl font-semibold">Compose a model</h1>
        <p className="text-sm text-foreground-muted">
          Combine a vision component with an on-policy component — and optionally an
          off-policy component alongside it — and get a code you can paste into a model
          picker. But{" "}
          <strong className="text-foreground">
            the exact combination you build here has never been driven
          </strong>
          .
        </p>
      </div>

      <div className="flex flex-col gap-2">
        <p className="text-sm text-foreground-muted">
          Every shipped combination is a preset — merged or PR-only. Pick one to fill the
          components exactly as it shipped upstream:
        </p>
        <Input
          type="search"
          placeholder="filter presets…"
          aria-label="Filter presets"
          value={filters.presets}
          onChange={(e) => setFilters({ ...filters, presets: e.target.value })}
        />
        <div className="flex max-h-64 flex-col gap-2 overflow-y-auto pr-1">
          {presets.map((b, i) => (
            <Button
              key={`${b.introduced_by.date}-${i}`}
              variant="outline"
              className="h-auto justify-start gap-2 py-2"
              onClick={() =>
                setSel(
                  Object.fromEntries(
                    b.files.map((m) => [m.role as Role, m.oid]),
                  ),
                )
              }
            >
              <span className="truncate font-medium">{prettyName(b.name)}</span>
              <span className="shrink-0 text-xs text-foreground-subtle">
                {b.introduced_by.date.slice(0, 10)}
              </span>
              <span className="flex shrink-0 gap-1">
                {[...new Set(b.occurrences.map((o) => o.status))].sort().map((s) => (
                  <Badge key={s} size="xs" variant={s === "merged" ? "success" : "warning"}>
                    {s.replace("_", " ")}
                  </Badge>
                ))}
                {b.in_head && <Badge size="xs" variant="info">in HEAD</Badge>}
                {b.files.some((m) => m.role === "off_policy") && (
                  <Badge size="xs" variant="outline">3 components</Badge>
                )}
              </span>
            </Button>
          ))}
        </div>
      </div>

      <div className="flex flex-wrap gap-4">
        {pickGrid("vision", true)}
        {pickGrid("on_policy", true)}
        {pickGrid("off_policy", false)}
      </div>

      <label className="flex items-center gap-2 text-sm text-foreground-muted">
        <Checkbox
          checked={attestedOnly}
          onCheckedChange={(c) => setAttestedOnly(!!c)}
        />
        only show policies attested with the picked vision
      </label>

      {notes && (
        <Card
          frame
          className={notes.warn ? "border-warning bg-warning-subtle" : "border-success bg-success-subtle"}
        >
          <p className="text-sm">{notes.text}</p>
        </Card>
      )}

      <div className="flex flex-col gap-3">
        <h2 className="text-lg font-semibold">
          Your code{" "}
          <span className="text-sm font-normal text-foreground-subtle">
            paste this into a picker
          </span>
        </h2>
        {selList.length > 0 && variantsAgree && (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border text-left text-foreground-muted">
                  <th className="py-1 pr-4 font-medium">role</th>
                  <th className="py-1 pr-4 font-medium">file</th>
                  <th className="py-1 pr-4 font-medium">size</th>
                  <th className="py-1 pr-4 font-medium">checkpoint</th>
                  <th className="py-1 font-medium">seam width</th>
                </tr>
              </thead>
              <tbody>
                {selList.map((r) => {
                  const f = byOid[sel[r]!];
                  return (
                    <tr key={r} className="border-b border-border-subtle">
                      <td className="py-1 pr-4">{r}</td>
                      <td className="py-1 pr-4 font-mono text-xs">{f.filenames[0]}</td>
                      <td className="py-1 pr-4">{Math.round(f.size / 1048576)} MB</td>
                      <td className="py-1 pr-4 font-mono text-xs">{ck(f) ?? "—"}</td>
                      <td className="py-1">{seam(f) ?? "—"}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
        <pre className="overflow-x-auto rounded-lg border border-border bg-background-subtle p-4 font-mono text-lg">
          {code || "Pick a vision component and an on-policy component."}
        </pre>
        <div>
          <CopyButton value={() => code} disabled={!code}>
            Copy
          </CopyButton>
        </div>
        <p className="text-sm text-foreground-muted">
          The code carries which files you picked, not a promise about them. Redeeming it
          — <code className="font-mono">GET /v1/compose/&lt;code&gt;</code>, or{" "}
          <code className="font-mono">redeem_code()</code> in the runtime library — resolves
          it against the catalog and re-runs every check. A damaged code fails to resolve
          rather than quietly naming different weights.
        </p>
        <p className="text-sm text-foreground-muted">
          Structural checks passing is not a safety result. The components may carry
          different host constants, since they came from different commits — redeem the code
          to see both and choose deliberately. Then compile on-device and qualify it
          yourself.
        </p>
      </div>
    </div>
  );
}
