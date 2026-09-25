#!/usr/bin/env python3
"""汎用（非パーソナライズ）文面を、companies.csv の「後半600社」に機械的に展開する。

マギシステムで1回だけ作った汎用テンプレート（部署=開発部で作成）の宛名部分だけを
部署名で置換し、AI呼び出しなしで output/generic_results.csv に一括出力する。

対象行の切り方（会議決定）:
  companies.csv の 1〜600行目 = 前半600社（フルパーソナライズ、run_magi.py で処理）
  companies.csv の 601〜1200行目 = 後半600社（このスクリプトで汎用文面を一括適用）

使い方:
  python3 scripts/generic_batch.py
"""
import csv
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COMPANIES = ROOT / "input" / "companies.csv"
DST = ROOT / "output" / "generic_results.csv"
JST = timezone(timedelta(hours=9))

START, END = 604, 1204  # 0-indexed: rows 605-1204 (1-indexed) — 604は前半パーソナライズが実際に到達した社数

SUBJECT = "【Web開発・SaaS運営向け】エンジニア等4職種、0→1相談からSESまで1社完結"

# {部署} 部分を差し込む（テンプレート内で「開発部」は宛名にしか出現しないため単純置換で安全）
BODY_TEMPLATE = """{部署} ご担当者様

「何を作ればいいか分からない」「人手が足りない」——そんなお悩みはありませんか。

私たち株式会社レイハウオリは、Web領域を中心に、ITリソースや専門人材が不足しているお客様を支援しています。
社内には、エンジニア・デザイナー・ディレクター・PMなど、各分野のエキスパートが在籍しています。
「何を作ればいいのかわからない」「IT周りをまとめて任せたい」といった0→1のご相談から、「社員の退職に伴うリソース不足を補いたい」といったSESのご要望まで、課題やフェーズに応じて幅広く対応しています。

ご興味をお持ちいただけましたら、「詳細希望」と一言いただくだけでも結構です。
担当より詳しいご案内とあわせて、お打ち合わせの日程候補をお送りいたします。

株式会社Lei Hau'oli
（tel）050-5497-3411
（mail）ml.account-sales@leihauoli.com"""

COLS = ["処理日時", "会社名", "部署", "担当者", "件名", "本文", "本文字数",
        "テンプレートスコア", "モード", "備考"]


def main():
    rows = list(csv.DictReader(open(COMPANIES, encoding="utf-8")))
    results_path = ROOT / "output" / "results.csv"
    already_personalized = set()
    if results_path.exists():
        already_personalized = {r["会社名"] for r in csv.DictReader(open(results_path, encoding="utf-8"))}
    target = [r for r in rows[START:END] if r["会社名"] not in already_personalized]
    now = datetime.now(JST).isoformat(timespec="seconds")
    out = []
    for r in target:
        body = BODY_TEMPLATE.format(部署=r["部署"])
        out.append({
            "処理日時": now,
            "会社名": r["会社名"],
            "部署": r["部署"],
            "担当者": r["担当者"],
            "件名": SUBJECT,
            "本文": body,
            "本文字数": len(body.replace("\n", "").replace(" ", "").replace("　", "")),
            "テンプレートスコア": 91,
            "モード": "汎用（非パーソナライズ・マギ1回生成・2026-09-25 CTA「詳細希望」版）",
            "備考": "",
        })
    with open(DST, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLS, quoting=csv.QUOTE_ALL)
        w.writeheader()
        w.writerows(out)
    print(f"対象: companies.csv[{START}:{END}] = {len(target)}社 -> {DST}")
    from collections import Counter
    print("部署内訳:", dict(Counter(r["部署"] for r in target)))


if __name__ == "__main__":
    main()
