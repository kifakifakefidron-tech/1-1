"""Сравнение двух запросов к DeepSeek на одних и тех же решениях администратора."""
from __future__ import annotations

import argparse
import importlib.util
import json
import sqlite3
from collections import Counter
from contextlib import closing
from pathlib import Path

from app import config, dupreview, llm


LABELS = ("repeat", "market", "diff")
JOINS = {"repeat", "market"}


def sample(conn, per_verdict):
    chosen = []
    for label in LABELS:
        rows = conn.execute("""SELECT id,a,b,status,decided_at FROM dup_pairs
            WHERE status=? AND user_id>0 AND decided_at IS NOT NULL
            ORDER BY decided_at DESC,id DESC LIMIT ?""", (label, per_verdict * 10)).fetchall()
        count = 0
        for row in rows:
            cards = [dupreview._card(conn, row[key]) for key in ("a", "b")]
            if not all(card and card["text"] and (card["version_ts"] or 0) <= row["decided_at"] for card in cards):
                continue
            chosen.append(dict(row))
            count += 1
            if count == per_verdict:
                break
    return chosen


def score(results, key, requests):
    predicted = Counter(row[key] for row in results)
    correct = Counter(row["admin"] for row in results if row["admin"] == row[key])
    joins = sum(correct[verdict] for verdict in JOINS)
    return {"requests": requests, "correct": correct.total(), "correct_joins": joins,
            "missed_same_properties": sum(row["admin"] in JOINS and row[key] in ("diff", "unsure") for row in results),
            "requests_per_correct_join": round(requests / joins, 2) if joins else None,
            "by_verdict": {verdict: {"correct": correct[verdict], "predicted": predicted[verdict],
                                     "precision": round(correct[verdict] / predicted[verdict], 4) if predicted[verdict] else None}
                           for verdict in LABELS}}


def compare(conn, baseline, per_verdict):
    """Считает рекомендации на актуальных текстах исторических пар, порог общий."""
    reviewers = {"before": baseline._model_review, "after": dupreview._model_review}
    requests, errors, results = Counter(), Counter(), []
    candidates = sample(conn, per_verdict)
    for i, row in enumerate(candidates):
        findings = {"id": row["id"], "admin": row["status"]}
        conn.execute("BEGIN")
        try:
            status = conn.execute("SELECT status,decided_at,user_id FROM dup_pairs WHERE id=?", (row["id"],)).fetchone()
            cards = [dupreview._card(conn, row[key]) for key in ("a", "b")]
            if (not status or not status["user_id"] or status["status"] != row["status"]
                    or status["decided_at"] != row["decided_at"]
                    or not all(card and card["text"] and (card["version_ts"] or 0) <= row["decided_at"] for card in cards)):
                continue
            signature = dupreview._ai_signature(cards)
            order = ("after", "before") if i % 2 else ("before", "after")
            for key in order:
                requests[key] += 1
                try:
                    ai = reviewers[key](conn, cards, signature)
                except llm.LLMError:
                    errors[key] += 1
                    break
                findings[key] = ai["verdict"] if (ai["confidence"] or 0) >= dupreview.AI_AUTO_CONFIDENCE else "unsure"
        finally:
            conn.rollback()
        if errors:
            break
        results.append(findings)
    return {"sample": len(candidates), "compared": len(results), "admin_labels": dict(Counter(row["admin"] for row in results)),
            "model_errors": dict(errors), **{key: score(results, key, requests[key]) for key in reviewers}, "pairs": results}


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--baseline", type=Path, required=True, help="файл app/dupreview.py из предыдущего коммита")
    cli.add_argument("--per-verdict", type=int, default=8, help="число ручных решений каждого типа")
    args = cli.parse_args()
    if args.per_verdict <= 0:
        cli.error("--per-verdict должен быть больше 0")
    if not config.DEEPSEEK_API_KEY:
        cli.exit(1, "Не задан DEEPSEEK_API_KEY.\n")
    spec = importlib.util.spec_from_file_location("app.dup_prompt_baseline", args.baseline)
    baseline = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(baseline)
    uri = Path(config.DB_PATH).resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True, timeout=30)) as conn:
        conn.row_factory = sqlite3.Row
        report = compare(conn, baseline, args.per_verdict)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return int(bool(report["model_errors"] or not report["compared"]))


if __name__ == "__main__":
    raise SystemExit(main())
