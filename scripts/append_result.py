#!/usr/bin/env python3
"""judge の最終文面を検証して output/results.csv に1行追記する。

使い方:
  python3 scripts/append_result.py result.json
result.json の形:
  {"会社名":..., "部署":..., "担当者":..., "件名":..., "本文":...,
   "opener_score":82, "replier_score":85, "closer_score":79, "final_score":88,
   "モード":"合成", "差し戻し回数":0, "採用理由":"...", "備考":""}
絶対条件 A1〜A3 を機械チェックし、NG なら備考に「絶対条件NG」を付けて exit 1。
"""
import csv, json, re, sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DST = ROOT / "output" / "results.csv"
COLS = ["処理日時", "会社名", "部署", "担当者", "件名", "本文", "本文字数",
        "opener_score", "replier_score", "closer_score", "final_score",
        "モード", "差し戻し回数", "採用理由", "備考"]
JST = timezone(timedelta(hours=9))


def check(subject, body):
    ng = []
    n = len(re.sub(r"\s", "", body))
    if n > 400:
        ng.append(f"A1({n}字)")
    if not ("【" in subject and "】" in subject and re.search(r"[0-9０-９]", subject)):
        ng.append("A2")
    first = body.strip().splitlines()[0] if body.strip() else ""
    if not re.match(r"^\S+\s+\S+様$", first):
        ng.append("A3")
    return n, ng


def main():
    d = json.load(open(sys.argv[1], encoding="utf-8"))
    n, ng = check(d.get("件名", ""), d.get("本文", ""))
    if ng:
        d["備考"] = (d.get("備考", "") + " 絶対条件NG:" + ",".join(ng)).strip()
    row = {c: d.get(c, "") for c in COLS}
    row["処理日時"] = datetime.now(JST).isoformat(timespec="seconds")
    row["本文字数"] = n
    new = not DST.exists() or DST.stat().st_size == 0
    with open(DST, "a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLS, quoting=csv.QUOTE_ALL)
        if new:
            w.writeheader()
        w.writerow(row)
    print(("NG " if ng else "OK ") + f"{d['会社名']} {n}字 final={d.get('final_score')} {ng}")
    sys.exit(1 if ng else 0)


if __name__ == "__main__":
    main()
