"""Заново проверяет сохранённые решения по повторам через DeepSeek. База сайта открывается только для чтения."""
from __future__ import annotations

import argparse
import json
import sqlite3
import time
from collections import Counter
from contextlib import closing
from pathlib import Path

from app import config, dupreview, llm, offers


def _append(report, row):
    report.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    report.flush()


def _load_run(conn, report, hours):
    report.seek(0)
    header = None
    results = {}
    while True:
        position = report.tell()
        line = report.readline()
        if not line:
            break
        if not line.endswith("\n"):
            report.seek(position)
            report.truncate()
            break
        entry = json.loads(line)
        if header is None:
            header = entry
        else:
            results[entry["id"]] = entry
    if header is None:
        until = int(time.time())
        since = until - hours * 3600
        pairs = conn.execute("""SELECT id, a, b, status AS previous, decided_at, COALESCE(user_id,0)>0 AS manual
            FROM dup_pairs WHERE decided_at BETWEEN ? AND ? AND status!='open'
            ORDER BY decided_at DESC,id DESC""", (since, until)).fetchall()
        header = {"hours": hours, "since_ts": since, "until_ts": until, "pairs": [dict(row) for row in pairs]}
        _append(report, header)
    if header["hours"] != hours:
        raise ValueError("В этом отчёте сохранён другой период проверки.")
    return header, results


def _read_pair(conn, entry):
    ids = (entry["a"], entry["b"])
    has_merge = conn.execute("""SELECT 1 FROM dup_pairs WHERE keep_id IN (?,?)
        AND status IN ('repeat','same','auto') AND drop_id IS NOT NULL AND undo IS NOT NULL LIMIT 1""", ids).fetchone() is not None
    cards = [dupreview._card(conn, lid, include_history=not has_merge) for lid in ids]
    if has_merge:
        # Фото, история отправителей и владелец могли попасть в карточку из предыдущих объединений.
        for card in (card for card in cards if card):
            card.update(photos=[], owner_user_id=None)
    return cards, has_merge


def _model_decision(conn, cards):
    signature = dupreview._ai_signature(cards)
    for attempt in range(3):
        try:
            return dupreview._model_review(conn, cards, signature)
        except llm.LLMError as error:
            provider_error = error.__cause__ or error.__context__
            if attempt == 2 or getattr(provider_error, "code", None) in (400, 401, 402, 403, 404):
                raise
            time.sleep(5 * (attempt + 1))


def _audit_pair(conn, entry):
    cards, has_merge = _read_pair(conn, entry)
    if not all(cards):
        return {"state": "unavailable"}
    try:
        ai = _model_decision(conn, cards)
    except llm.LLMError as error:
        return {"state": "api_error", "error": str(error)[:250]}
    verdict = ai["verdict"]
    enough = (ai["confidence"] or 0) >= dupreview.AI_AUTO_CONFIDENCE
    return {"state": "reviewed", "recommendation": verdict,
            "confident_recommendation": verdict if enough else "unsure",
            "confidence": ai["confidence"], "reason": ai["why"], "evidence": ai["evidence"],
            "without_transferred_details": has_merge,
            "missing_description": any(not card["text"].strip() for card in cards),
            "newer_card_version": any((card["version_ts"] or 0) > entry["decided_at"] for card in cards)}


def _summary(header, results):
    reviewed = [row for row in results.values() if row["state"] == "reviewed"]
    states = Counter(row["state"] for row in results.values())
    matrix = {}
    for row in reviewed:
        matrix.setdefault(row["previous"], Counter())[row["confident_recommendation"]] += 1
    total = len(header["pairs"])
    return {"hours": header["hours"], "since_ts": header["since_ts"], "until_ts": header["until_ts"],
            "total": total, "reviewed": len(reviewed), "unavailable": states["unavailable"],
            "api_errors": states["api_error"], "remaining": total - len(reviewed) - states["unavailable"],
            "previous": dict(Counter(row["previous"] for row in header["pairs"])),
            "new_recommendations": dict(Counter(row["recommendation"] for row in reviewed)),
            "confidence_threshold": dupreview.AI_AUTO_CONFIDENCE, "prior_status_by_new_recommendation": matrix}


def audit(conn, report, hours):
    header, results = _load_run(conn, report, hours)
    pairs = header["pairs"]
    done = sum(row["state"] != "api_error" for row in results.values())
    print(f"К проверке {len(pairs)} пар за {hours} часов. Уже обработано {done}.", flush=True)
    consecutive_errors = 0
    for entry in pairs:
        pid = entry["id"]
        if pid in results and results[pid]["state"] != "api_error":
            continue
        row = results[pid] = {**entry, **_audit_pair(conn, entry)}
        _append(report, row)
        if row["state"] == "api_error":
            consecutive_errors += 1
            print(f"Пара № {pid}. Не удалось получить ответ DeepSeek.", flush=True)
            if consecutive_errors == 3:
                print("Три ошибки подряд. Повторный запуск продолжит этот отчёт.", flush=True)
                break
        else:
            consecutive_errors = 0
            done += 1
            if done % 50 == 0:
                print(f"Обработано {done} из {len(pairs)} пар.", flush=True)
    return _summary(header, results)


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--hours", type=int, default=48)
    cli.add_argument("--report", type=Path, required=True, help="путь к поэтапному отчёту JSONL")
    args = cli.parse_args()
    if args.hours < 1:
        cli.error("--hours должен быть больше нуля")
    if not config.DEEPSEEK_API_KEY:
        cli.exit(1, "Не задан DEEPSEEK_API_KEY.\n")
    database = Path(config.DB_PATH).resolve()
    if args.report.exists() and args.report.samefile(database):
        cli.error("Отчёт должен быть отдельным файлом.")
    uri = database.as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True, timeout=30)) as conn, args.report.open("a+", encoding="utf-8") as out:
        conn.row_factory = sqlite3.Row
        summary = audit(conn, out, args.hours)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"Отчёт находится в {args.report}")
    return int(bool(summary["remaining"]))


if __name__ == "__main__":
    raise SystemExit(main())
