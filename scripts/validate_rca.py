"""Valida o módulo RCA com cenários de causa conhecida.

Uso, a partir da raiz do repositório:

    python scripts/validate_rca.py            # heurística determinística, sem rede
    python scripts/validate_rca.py --llm      # também consulta o LLM configurado
    python scripts/validate_rca.py --json resultado.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from qualitybridge.llm import client_from_env
from qualitybridge.rca_validation import evaluate, load_scenarios, summary

DEFAULT = Path(__file__).resolve().parents[1] / "examples" / "rca-scenarios" / "scenarios.json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--scenarios", type=Path, default=DEFAULT)
    parser.add_argument("--llm", action="store_true", help="Usa o LLM das variáveis de ambiente.")
    parser.add_argument("--json", type=Path, help="Grava as métricas neste arquivo.")
    args = parser.parse_args()

    llm = client_from_env() if args.llm else None
    if args.llm and llm is None:
        print("Nenhum LLM configurado; veja docs/rca.md.", file=sys.stderr)
        return 2

    data = summary(evaluate(load_scenarios(args.scenarios), llm))
    print(f"{'cenário':<28} {'esperado':<22} {'obtido':<22} cat  commit  ms")
    for item in data["resultados"]:
        print(
            f"{item['id']:<28} {item['expected_category']:<22} {item['obtained_category']:<22} "
            f"{'ok' if item['category_ok'] else 'X':<4} {'ok' if item['sha_ok'] else 'X':<7} "
            f"{item['milliseconds']}"
        )
    print(
        f"\nAcerto de categoria: {data['acerto_categoria']:.0%} | "
        f"acerto de commit: {data['acerto_commit']:.0%} | "
        f"hipóteses fundamentadas: {data['hipoteses_fundamentadas']:.0%} | "
        f"tempo médio: {data['tempo_medio_ms']} ms"
    )
    if args.json:
        args.json.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if data["acerto_categoria"] == 1 and data["acerto_commit"] == 1 else 1


if __name__ == "__main__":
    raise SystemExit(main())
