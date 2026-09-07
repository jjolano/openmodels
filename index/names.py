"""Evidence-backed display names; discovery labels never change recipe identity."""
import re

from openmodels.contracts import ContractError

PRIORITY = {"comma": 0, "openpilot-wiki": 1, "sunnypilot": 2, "frogpilot": 3}


def source_label(title):
  """Extract only explicit model titles; this is a source label, not a verified nickname."""
  title = re.sub(r"\s*\(#\d+\)$", "", title).strip()
  match = re.fullmatch(r"(?:New |Driving )?[Mm]odel:\s*(.+)|(.+?) [Mm]odel", title)
  if not match:
    return None
  label = (match[1] or match[2]).strip()
  if not re.fullmatch(r"[A-Za-z][A-Za-z0-9 '\-]{2,70}", label):
    return None
  if re.search(r"\b(new|update|fix|revert|rebase|test|files|big|small|driving|compile|export|wrong|best|old|latest|previous|current|better|bad|good|default|stock|experimental)\b", label, re.I):
    return None
  if re.fullmatch(r"[a-fA-F0-9\-]+", label):
    return None
  return label


def model_metadata(bundle, records):
  claims = []
  artifacts = sorted([{"role": f["role"], "sha256": f["oid"]} for f in bundle["files"]], key=lambda a: a["role"])
  commits = {o["commit"] for o in bundle["occurrences"]}
  for record in records:
    if record["bundle_id"] != bundle["bundle_id"]:
      continue
    if sorted(record["artifacts"], key=lambda a: a["role"]) != artifacts or record["ref"] not in commits:
      raise ContractError("named model source no longer matches archived artifacts")
    claim = {k: record[k] for k in ("name", "source", "url", "method")}
    if claim not in claims:
      claims.append(claim)
  claims.sort(key=lambda c: (PRIORITY.get(c["source"], 99), c["name"], c["url"]))
  introduced = bundle.get("introduced_by", {})
  commit = introduced.get("commit")
  occurrence = next((o for o in bundle["occurrences"] if o["commit"] == commit), {})
  label = None if occurrence.get("is_revert") else source_label(occurrence.get("subject", bundle["name"]))
  if label and commit in commits:
    claim = {"name": label, "source": "comma", "url": "https://github.com/commaai/openpilot/commit/" + commit,
             "method": "source"}
    if not any(c["name"] == label for c in claims):
      claims.append(claim)
  date = str(introduced.get("date") or "undated")[:10]
  kind = {"dmonitoring": "Driver monitoring", "nav": "Navigation"}.get(bundle["kind"], bundle["kind"].capitalize())
  family = bundle.get("family", bundle["kind"])
  fallback = f"{kind} · {family} · {bundle.get('variant', 'standard')} · {date} · {bundle['bundle_id'][:8]}"
  links = list(dict.fromkeys(c["url"] for c in claims))
  if commit and not claims:
    links.append("https://github.com/commaai/openpilot/commit/" + commit)
  return {"id": "commaai/" + bundle["bundle_id"], "name": claims[0]["name"] if claims else fallback,
          "name_kind": claims[0]["method"] if claims else "generated", "names": claims,
          "family": family, "archived": not bundle.get("in_head", False),
          "description": "Original upstream weights and source configurations. Fork naming references do not imply equivalent compiled packages or tuning.",
          "links": links}
