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

START, END = 600, 1200  # 0-indexed: rows 601-1200 (1-indexed)

SUBJECT = "【1名から常駐】フロントエンド専任エンジニア40名、採用に頼らず体制強化"

# {部署} 部分を差し込む（テンプレート内で「開発部」は宛名にしか出現しないため単純置換で安全）
BODY_TEMPLATE = """{部署} ご担当者様

UI改修や新機能開発を進めたくても、フロントエンド専任エンジニアの採用が進まず、着手が遅れていないでしょうか。

株式会社Lei Hau'oliは、フロントエンド特化で国内大手BtoC直請け水準のエンジニアを1名から供給できるSES会社です。React/Vue.js/Next.js/TypeScriptを専門とするエンジニア約40名が在籍し、国内最大級の美容予約サービスの開発にも参画しています。

中途採用に数か月かけずとも、大規模BtoCで培った開発フローと品質基準をそのまま持ち込めるため、リリースを止めずにUI品質を底上げできます。採用が長引く間も他社は機能改善を進めており、今動くかで次四半期の速度が変わります。

現状の開発体制について、「相談したい」の5文字だけご返信ください。折り返しご連絡します。

株式会社Lei Hau'oli 開発支援事業部"""

COLS = ["処理日時", "会社名", "部署", "担当者", "件名", "本文", "本文字数",
        "テンプレートスコア", "モード", "備考"]


def main():
    rows = list(csv.DictReader(open(COMPANIES, encoding="utf-8")))
    target = rows[START:END]
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
            "テンプレートスコア": 92,
            "モード": "汎用（非パーソナライズ・マギ1回生成）",
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
