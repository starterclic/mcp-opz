#!/usr/bin/env python3
"""Serveur MCP opz — expose l'infrastructure d'un tenant à un assistant.

Tourne CHEZ le client, en stdio, à côté de son assistant : c'est lui qui
détient le jeton et appelle l'API. Aucune surface réseau ajoutée côté opz.

    export OPZ_TOKEN=opz_xxxxxxxx
    uv run --with mcp --with httpx python opz_mcp.py

Lecture seule par construction : seules des requêtes GET sont émises.

Les outils renvoient des AGRÉGATS, pas des vidages. Un relevé brut pèse ~11 Ko ;
en sortir mille sature la fenêtre de contexte sans rien apprendre au modèle.
Chaque outil est donc plafonné, et un outil de détail existe pour creuser.
"""
from __future__ import annotations

import os
import sys
from typing import Any

BASE = os.environ.get("OPZ_BASE", "https://opz.cybtek.fr").rstrip("/")
TOKEN = os.environ.get("OPZ_TOKEN", "")
TIMEOUT = 20.0

MAX_HOSTS = 50
MAX_RECOS = 10
MAX_APPS = 40
MAX_POINTS = 48  # points d'historique renvoyés au modèle


# --- mise en forme (pur, testable sans réseau) -------------------------------

def compact_reco(r: dict[str, Any]) -> dict[str, Any]:
    """Garde ce qui sert à décider, jette le reste.

    `target_ref` contient le détail des CVE par image et la liste des
    emplacements : utile à l'UI, illisible pour un modèle.
    """
    ref = r.get("target_ref") or {}
    if isinstance(ref, str):
        ref = {}
    cve = ref.get("cve_summary") or {}
    out = {
        "id": r.get("id"),
        "severite": r.get("severity"),
        "type": r.get("kind"),
        "titre": r.get("title"),
        "host_id": r.get("host_id"),
    }
    if ref.get("image"):
        out["image"] = ref["image"]
    if cve:
        out["cve"] = {"critique": cve.get("critical"), "haute": cve.get("high")}
    if r.get("estimated_savings"):
        out["economie_eur_mois"] = r["estimated_savings"]
    return out


def downsample(points: list[dict[str, Any]], maxi: int = MAX_POINTS) -> list[dict[str, Any]]:
    """Garde au plus `maxi` points, régulièrement espacés, extrémités comprises.

    Une journée de collecte = 1440 relevés. Les donner tous à un modèle coûte
    cher et n'apprend rien de plus qu'une cinquantaine bien répartis.
    """
    n = len(points)
    if n <= maxi or maxi < 2:
        return points
    step = (n - 1) / (maxi - 1)
    return [points[round(i * step)] for i in range(maxi)]


def compact_point(s: dict[str, Any]) -> dict[str, Any]:
    return {
        "ts": s.get("ts"),
        "cpu": s.get("cpu_percent"),
        "ram": s.get("mem_percent"),
        "disque": s.get("disk_percent"),
        "conteneurs": s.get("containers_running"),
    }


def _selftest() -> None:
    assert downsample([{"i": i} for i in range(5)], 10) == [{"i": i} for i in range(5)]
    d = downsample([{"i": i} for i in range(1000)], 48)
    assert len(d) == 48 and d[0] == {"i": 0} and d[-1] == {"i": 999}
    c = compact_reco({
        "id": 1, "severity": "critical", "kind": "cve_critical", "title": "x",
        "host_id": 3, "estimated_savings": None,
        "target_ref": {"image": "redis:7", "cve_summary": {"critical": 4, "high": 17},
                       "locations": [{"host_id": 3}] * 50},
    })
    assert c["image"] == "redis:7" and c["cve"]["critique"] == 4
    assert "locations" not in str(c) and "economie_eur_mois" not in c
    print("selftest ok")


# --- serveur MCP -------------------------------------------------------------

def main() -> None:
    import httpx
    from mcp.server.fastmcp import FastMCP

    if not TOKEN:
        sys.exit("OPZ_TOKEN manquant — créez un jeton en portée `read` dans Settings → API.")
    if not TOKEN.startswith("opz_"):
        sys.exit("OPZ_TOKEN ne ressemble pas à un jeton opz (préfixe attendu : opz_).")

    client = httpx.Client(
        base_url=BASE + "/api/v1",
        headers={"Authorization": f"Bearer {TOKEN}"},
        timeout=TIMEOUT,
    )

    def get(path: str, **params: Any) -> Any:
        r = client.get(path, params=params or None)
        if r.status_code == 401:
            raise RuntimeError("jeton refusé (401) : expiré ou révoqué.")
        if r.status_code == 403:
            raise RuntimeError("accès refusé (403) : portée insuffisante pour cette ressource.")
        if r.status_code == 429:
            raise RuntimeError(f"quota atteint (429), réessayer dans {r.headers.get('Retry-After', '60')} s.")
        r.raise_for_status()
        return r.json()

    mcp = FastMCP("opz")

    @mcp.tool()
    def etat_du_parc() -> dict[str, Any]:
        """Vue d'ensemble : machines, coût mensuel, recommandations, économies
        possibles. À appeler en premier — c'est le résumé le plus dense."""
        d = get("/insights/context")
        return {
            "genere_le": d.get("generated_at"),
            "tenant": d.get("tenant"),
            "resume": d.get("summary"),
            "machines": (d.get("hosts") or [])[:MAX_HOSTS],
            "top_recommandations": [compact_reco(r) for r in (d.get("top_recommendations") or [])[:MAX_RECOS]],
        }

    @mcp.tool()
    def plan_action() -> dict[str, Any]:
        """Les actions suggérées, triées par gain mensuel."""
        d = get("/insights/action-plan")
        return {
            "economie_totale_eur_mois": d.get("total_potential_savings_eur_month"),
            "actions": (d.get("actions") or [])[:MAX_RECOS],
        }

    @mcp.tool()
    def machines() -> list[dict[str, Any]]:
        """Liste des VPS surveillés : état, dernière collecte, coût."""
        return [
            {"id": h.get("id"), "nom": h.get("label"), "etat": h.get("status"),
             "hote": h.get("hostname"), "cout_eur_mois": h.get("monthly_cost_eur")}
            for h in get("/hosts")[:MAX_HOSTS]
        ]

    @mcp.tool()
    def machine(host_id: int) -> dict[str, Any]:
        """Détail d'une machine : son état et les conteneurs qui y tournent."""
        detail = get(f"/hosts/{host_id}")
        try:
            apps = get(f"/hosts/{host_id}/applications")[:MAX_APPS]
        except Exception:
            apps = []
        return {"machine": detail, "applications": apps}

    @mcp.tool()
    def historique(host_id: int, heures: int = 24) -> dict[str, Any]:
        """Historique CPU/RAM/disque d'une machine.

        Limite connue : l'API sert les relevés bruts, purgés au bout de
        48 heures. Demander davantage ne renverra pas plus de points.
        """
        heures = max(1, min(int(heures), 48))
        snaps = get(f"/hosts/{host_id}/history", hours=heures)
        pts = [compact_point(s) for s in snaps]
        return {"host_id": host_id, "heures": heures, "points": downsample(pts)}

    @mcp.tool()
    def recommandations(severite: str | None = None, limite: int = MAX_RECOS) -> list[dict[str, Any]]:
        """Recommandations ouvertes, les mieux notées d'abord.

        `severite` filtre sur critical / high / medium / low / info.
        Un compteur de CVE n'est pas une évaluation de risque : vérifier si le
        conteneur est exposé avant de conclure à l'urgence.
        """
        rows = get("/recommendations")
        if severite:
            rows = [r for r in rows if str(r.get("severity")) == severite]
        return [compact_reco(r) for r in rows[: max(1, min(int(limite), 50))]]

    @mcp.prompt()
    def revue_securite() -> str:
        """Revue de sécurité du parc (surface exposée, CVE exploitables,
        durcissement), avec les garde-fous d'un agent autonome."""
        t = get("/insights/prompt-template", focus="security")
        return t["system"] + "\n\n" + t["user_template"].replace(
            "```json\n{context}\n```",
            "Appelle d'abord l'outil `etat_du_parc`, puis `recommandations` "
            "si besoin : ce sont tes seules données.",
        )

    mcp.run()


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        main()
