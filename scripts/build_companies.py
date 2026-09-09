#!/usr/bin/env python3
"""ターゲットリスト（営業リストCSV）を マギシステム用 input/companies.csv に変換する。

入力: input/target_list_raw.csv
  1行目: 空行 / 2行目: ヘッダー / 3行目以降: データ
  A=企業名 B=中業界(メイン) C=中業界(サブ) D=都道府県 E=所在地 F=従業員数 G=資本金 H=売上
  I=企業HP J=問い合わせフォーム K=電話 L=メール ... O=送信可否 P=日付 Q=送信不可理由
出力: input/companies.csv  (会社名,部署,担当者,事業内容,課題仮説)  + 参考列 HP,フォームURL

使い方:
  python3 scripts/build_companies.py            # フォームあり & 営業禁止なし の全社
  python3 scripts/build_companies.py --limit 20 # 先頭20社
  python3 scripts/build_companies.py --all      # 送信不可も含めて全社
"""
import argparse, csv, re, sys, unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "input" / "target_list_raw.csv"
DST = ROOT / "input" / "companies.csv"

# 業界 → 想定部署（SESの決裁・窓口になりやすい部署）
DEPT = {
    "ゲーム開発": "開発部",
    "医療機器": "情報システム部",
    "情報セキュリティ": "技術部",
    "クラウド系/SaaS/ASPサービス": "開発部",
    "Webアプリ・サービス運営": "開発部",
}

# 業界 × 規模 → 課題仮説（SES＝フロントエンド開発支援を売る側の視点）
def hypothesis(main, subs, size):
    small = size in ("1", "2")     # 5〜20人未満
    mid = size == "3"              # 20〜50人未満
    large = size in ("4", "5")     # 50人以上

    if main == "ゲーム開発":
        base = "ゲーム本体の開発にエンジニアを集中させたい一方、公式サイト・運営管理ツール・Web版の改修が後回しになりやすい。"
    elif main == "医療機器":
        base = "製品情報サイトや顧客向けポータル、社内システムのWeb化を進めたいが、社内にフロントエンド開発の専任者がおらず外注先の品質もばらつく。"
    elif main == "情報セキュリティ":
        base = "管理コンソールやダッシュボードのUIが製品評価に直結するのに、セキュリティ専門のエンジニアがUI実装まで抱えており開発が詰まりやすい。"
    elif main == "クラウド系/SaaS/ASPサービス":
        base = "顧客要望による管理画面・ダッシュボードの機能追加が続き、フロントエンドの改修がバックログに積み上がっている。"
    else:
        base = "サービスのUI改修や機能追加の要望に対し、フロントエンドの開発リソースが慢性的に不足している。"

    if small:
        stage = "少人数でフロントとバックを兼任しており、正社員のフロントエンド採用は難しく、即戦力を短期で足したい局面。"
    elif mid:
        stage = "プロダクトの成長期で、React/Vueへの刷新やSPA化を控えているが、中途採用市場でフロントエンド経験者が採れていない。"
    else:
        stage = "複数プロダクト・大規模UIを内製チームで抱えており、レガシー刷新と新機能開発の並走で負荷が平準化できていない。"

    extra = ""
    if "受託開発" in subs or "Web制作" in subs:
        extra = "受託案件の波で稼働が上下し、繁忙期だけ経験者を確保する手段がない。"
    elif "eコマース" in subs:
        extra = "セール時期や表示速度の改善など、フロントエンドの品質が売上に直結する。"
    elif "人材派遣" in subs or "ITコンサル" in subs:
        extra = "クライアント案件でフロントエンド要員を求められても、自社で供給しきれないケースがある。"
    return base + stage + extra


def business(main, subs, pref, size_label, capital, sales):
    parts = [main]
    if subs:
        parts.append("（" + "・".join(subs[:3]) + "）")
    desc = "".join(parts) + "を手がける企業。"
    if pref:
        desc += f"本社{pref}。"
    if size_label:
        desc += f"従業員{size_label}。"
    if sales:
        desc += f"売上規模{sales}。"
    return desc


def clean_size(v):
    # "3: 20人以上~50人未満" → ("3", "20人以上50人未満")
    m = re.match(r"(\d):\s*(.+)", v or "")
    if not m:
        return "", ""
    return m.group(1), m.group(2).replace("~", "").replace(" ", "")


def clean_sales(v):
    m = re.match(r"\d:\s*(.+)", v or "")
    return m.group(1).replace("~", "").replace(" ", "") if m else ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--all", action="store_true", help="送信不可の企業も含める")
    args = ap.parse_args()

    rows = list(csv.reader(open(SRC, encoding="utf-8-sig")))[2:]
    out = []
    skipped = {"名前なし": 0, "フォームなし": 0, "営業禁止": 0}
    for r in rows:
        r = (r + [""] * 18)[:18]
        name = unicodedata.normalize("NFKC", r[0]).strip()
        if not name:
            skipped["名前なし"] += 1
            continue
        form = r[9].strip()
        reason = r[16].strip()
        if not args.all:
            if "営業禁止" in reason:
                skipped["営業禁止"] += 1
                continue
            if not form:
                skipped["フォームなし"] += 1
                continue
        main_ind = r[1].strip()
        subs = [s.strip() for s in r[2].split(",") if s.strip()]
        size_code, size_label = clean_size(r[5])
        out.append({
            "会社名": name,
            "部署": DEPT.get(main_ind, "開発部"),
            "担当者": "ご担当者",
            "事業内容": business(main_ind, subs, r[3].strip(), size_label, r[6], clean_sales(r[7])),
            "課題仮説": hypothesis(main_ind, subs, size_code),
            "HP": r[8].strip() if r[8].strip().startswith("http") else "",
            "フォームURL": form,
        })
        if args.limit and len(out) >= args.limit:
            break

    with open(DST, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["会社名", "部署", "担当者", "事業内容", "課題仮説", "HP", "フォームURL"])
        w.writeheader()
        w.writerows(out)
    print(f"wrote {len(out)} rows -> {DST}", file=sys.stderr)
    print("skipped:", skipped, file=sys.stderr)


if __name__ == "__main__":
    main()
