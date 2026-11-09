"""Identity changes from GFW + a hand-curated ownership table -> timeline and network.

data/ownership.csv is filled in BY HAND from public records (sanctions notices,
registries, reporting). Each row must cite a source ID from SOURCES.md.
"""

from __future__ import annotations

from pathlib import Path

import networkx as nx
import pandas as pd

from .config import OWNERSHIP_CSV

OWNERSHIP_COLUMNS = ["imo", "role", "entity", "country", "address",
                     "date_from", "date_to", "source_id", "grade", "note"]
ROLES = {"registered_owner", "beneficial_owner", "manager", "ism_manager",
         "p&i_insurer", "class_society", "flag"}


def identity_changes(identities: pd.DataFrame) -> pd.DataFrame:
    """Rows where broadcast name, flag or MMSI changed versus the previous period."""
    ais = identities[identities["source"] == "AIS self-reported"].sort_values(["imo", "from"])
    rows = []
    for imo, g in ais.groupby("imo"):
        prev = None
        for _, cur in g.iterrows():
            if prev is not None:
                for field in ("shipname", "flag", "ssvid"):
                    if cur[field] != prev[field]:
                        rows.append({"imo": imo, "date": cur["from"], "field": field,
                                     "old": prev[field], "new": cur[field]})
            prev = cur
    return pd.DataFrame(rows, columns=["imo", "date", "field", "old", "new"])


def load_ownership(path: Path = OWNERSHIP_CSV) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=OWNERSHIP_COLUMNS)
    df = pd.read_csv(path, comment="#", dtype=str).fillna("")
    bad = set(df["role"]) - ROLES
    if bad:
        raise ValueError(f"Unknown roles in {path.name}: {bad}. Allowed: {sorted(ROLES)}")
    missing = df[df["source_id"] == ""]
    if not missing.empty:
        raise ValueError(f"{len(missing)} ownership rows have no source_id — cite every claim.")
    return df


def ownership_graph(own: pd.DataFrame, names: dict[str, str] | None = None) -> nx.Graph:
    """Ship -- entity -- address graph. Shared addresses/managers show up as hubs."""
    names = names or {}
    G = nx.Graph()
    for r in own.itertuples():
        ship = f"IMO {r.imo}"
        G.add_node(ship, kind="ship", label=names.get(r.imo, ship))
        G.add_node(r.entity, kind="entity", country=r.country)
        G.add_edge(ship, r.entity, role=r.role, since=r.date_from, source=r.source_id)
        if r.address:
            G.add_node(r.address, kind="address")
            G.add_edge(r.entity, r.address, role="registered_at")
    return G


def shared_hubs(G: nx.Graph) -> pd.DataFrame:
    """Entities/addresses connected to more than one ship (directly or via an entity)."""
    rows = []
    for n, d in G.nodes(data=True):
        if d.get("kind") == "ship":
            continue
        ships = {s for s in nx.node_connected_component(G, n)
                 if G.nodes[s].get("kind") == "ship"
                 and nx.shortest_path_length(G, n, s) <= 2}
        if len(ships) > 1:
            rows.append({"node": n, "kind": d.get("kind"), "ships": sorted(ships)})
    return pd.DataFrame(rows, columns=["node", "kind", "ships"])
