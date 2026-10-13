"""Аудит гео-данных теми же метриками, что в ТЗ «админка-гео» (сверка до/после каждого этапа).

    python -m tools.geo_audit                 — база из настроек (.env, DB_PATH)
    python -m tools.geo_audit путь/к/базе.db  — другая база (например, копия с сервера)
Ничего не меняет: только читает и печатает.
"""
from __future__ import annotations

import sys


def main(argv: list[str]) -> int:
    from app import config, db, geoaudit, geoquality
    if len(argv) > 1:
        config.DB_PATH = argv[1]
    conn = db.connect(config.DB_PATH)
    m = geoquality.metrics(conn)
    total = m["total"] or 1
    print("Качество гео-данных")
    for k, label in geoquality.LABELS.items():
        share = "" if k in ("total", "same_groups") else f"  ({m[k] / total * 100:.1f} %)"
        print(f"  {label:<45} {m[k]:>7}{share}")
    rep = geoaudit.report(conn)
    print("\nПравила и справочники")
    print(f"  Правил «улица/ЖК → район»: {len(rep['rules'])}, против карты: {rep['bad']}")
    for r in [r for r in rep["rules"] if r["status"] == "bad"][:20]:
        print(f"    {r['label']}: в своём районе {int((r['share'] or 0) * 100)} %, по контурам {r['dist']}")
    print(f"  ЖК-мусор: {len(rep['junk'])}; «улицы»-подписи: {len(rep['labels'])}; испорченных правил: {len(rep['broken'])}")
    print(f"  Выученных точек домов без посёлка: {rep['points_total']}, сомнительных: {len(rep['points'])}")
    print(f"  Точек ЖК из одного дома: {len(rep['cx_points'])}; районов-дублей: {len(rep['dupes'])}")
    print(f"  Контуры без объектов: {', '.join(rep['empty_contours']) or '—'}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
