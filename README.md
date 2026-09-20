# Serveur MCP opz

Donne à un assistant (Claude, ou tout client MCP) une vue **lecture seule** de
votre infrastructure supervisée par [opz](https://opz.cybtek.fr).

Un seul fichier Python, aucune dépendance exotique, rien à exposer sur
Internet : il tourne chez vous, à côté de votre assistant.

```bash
git clone https://github.com/starterclic/mcp-opz.git
cd mcp-opz
export OPZ_TOKEN=opz_xxxxxxxx      # Settings -> API, portée `read`
uv run --with mcp --with httpx python opz_mcp.py
```

## Où le faire tourner

**Chez vous, à côté de l'assistant**, en stdio. C'est lui qui détient le jeton
et appelle l'API d'opz. Ne l'exposez pas sur Internet : ce serait une seconde
porte d'entrée à sécuriser, pour aucun gain.

## Installation

1. Créez un jeton dans **Settings → API**, portée **`read`**, expiration 90 jours.
2. Déclarez le serveur dans votre client MCP :

```json
{
  "mcpServers": {
    "opz": {
      "command": "uv",
      "args": ["run", "--with", "mcp", "--with", "httpx",
               "python", "/chemin/vers/opz_mcp.py"],
      "env": { "OPZ_TOKEN": "opz_xxxxxxxx" }
    }
  }
}
```

Le jeton vit ici, dans la configuration — **jamais dans un prompt**.

## Les outils

| Outil | Ce qu'il rend |
| --- | --- |
| `etat_du_parc` | machines, coût, recommandations, économies — le résumé le plus dense |
| `plan_action` | les actions suggérées, triées par gain |
| `machines` | la liste des VPS |
| `machine(host_id)` | le détail d'une machine et ses conteneurs |
| `historique(host_id, heures)` | CPU/RAM/disque, au plus 48 points |
| `recommandations(severite, limite)` | les recommandations ouvertes |

Chaque outil est plafonné et renvoie des agrégats : un agent bien orienté
répond mieux qu'un agent noyé sous les données.

## Limites

- **Lecture seule** : le serveur n'émet que des GET, et un jeton `read` ne
  pourrait rien modifier de toute façon.
- **48 heures d'historique** : l'API sert les relevés bruts, purgés au-delà.
  Les points horaires conservés 90 jours ne sont pas encore exposés.
- **300 requêtes par minute**, au-delà l'API répond `429`.
- Le serveur ne voit que **votre tenant** — celui du créateur du jeton.

## Vérifier sans réseau

```bash
python opz_mcp.py --selftest
```
