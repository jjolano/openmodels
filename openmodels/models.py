"""Named discovery groups; every selectable variant retains its exact recipe ID."""
from copy import deepcopy
from .contracts import ContractError, dumps, sha256


def models(catalog, *, include_archive=True, query="", kind=None, publisher=None):
  groups = {}
  for entry in catalog._data["entries"]:
    recipe = catalog.resolve(entry["recipe"])
    members = recipe.data["members"]
    metadata = entry.get("model", {
      "id": entry["publisher"] + "/" + sha256(dumps([entry["kind"], entry["name"]]).encode())[:16],
      "name": entry["name"], "family": " / ".join(sorted(members)),
      "description": "Publisher-submitted model package.", "links": [], "archived": False})
    identity = metadata["id"]
    base = {**deepcopy(metadata), "model_class": metadata.get("model_class", "unknown"),
            "publisher": entry["publisher"], "kind": entry["kind"]}
    hardware = base.pop("hardware", [])
    if identity in groups and groups[identity][0] != base:
      raise ContractError(f"conflicting model metadata: {identity}")
    _, variants = groups.setdefault(identity, (base, {}))
    dates = sorted(str(o["date"]) for o in entry["occurrences"] if o.get("date"))
    contexts = sorted({str(m["source"].get("commit") or m["source"].get("revision") or "unrecorded") for m in members.values()})
    targets = [set(m["targets"]) for m in members.values() if m["targets"]]
    artifacts = {m["artifact"]["sha256"]: m["artifact"] for m in members.values()}
    member_artifacts = sorted([{"role": role, "sha256": member["artifact"]["sha256"]}
                               for role, member in members.items()], key=lambda a: a["role"])
    variants[recipe.id] = {"recipe": recipe.id, "label": "Source " + ", ".join(c[:12] for c in contexts),
      "profile": recipe.profile.id, "model_class": base["model_class"],
      "hardware": [{key: claim[key] for key in ("name", "url", "method")} for claim in hardware
                   if contexts == [claim["context"]] and member_artifacts == sorted(claim["artifacts"], key=lambda a: a["role"])],
      "targets": sorted(set.intersection(*targets)) if targets else [],
      "formats": sorted({a["format"] for a in artifacts.values()}), "size": sum(a["size"] for a in artifacts.values()),
      "available": all(catalog._data["locations"].get(a, {}).get("availability") == "available" and
                       catalog._data["locations"].get(a, {}).get("urls") for a in artifacts),
      "updated_at": dates[-1] if dates else "", "contexts": contexts}
  result = [{**base, "variants": sorted(variants.values(), key=lambda v: v["recipe"]),
             "updated_at": max(v["updated_at"] for v in variants.values())} for base, variants in groups.values()
            if (include_archive or not base["archived"]) and (kind is None or base["kind"] == kind)
            and (publisher is None or base["publisher"] == publisher)
            and (not query or query.casefold() in dumps(base).casefold())]
  # Named choices lead, but generated labels remain in the same complete directory.
  result.sort(key=lambda m: (m["updated_at"], m["name"], m["id"]), reverse=True)
  return sorted(result, key=lambda m: m.get("name_kind") == "generated")
