#!/usr/bin/env python3
"""output/results_patched.csv の604社のCTAを、新しい固定2文（「詳細希望」版）に差し替える。

新CTAは旧CTAより約50字長いため、単純な文字列置換だけでは400字を超える行が大半になる。
そのため1社1回・Sonnetで「CTAを差し替え、超過分だけパーソナライズ文を削って400字に収める」
修正を行う（会社紹介文・署名・宛名は変更しない）。

使い方:
  python3 scripts/patch_cta2.py [--limit N] [--workers 6]
出力: output/results_patched.csv を直接上書き更新する。
"""
import argparse, csv, os, re, subprocess, json, sys, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "output/results_patched.csv"
WORKDIR = Path(os.environ.get("MAGI_WORKDIR", "/tmp/magi-patch-work"))
WORKDIR.mkdir(parents=True, exist_ok=True)

NEW_CTA = ("ご興味をお持ちいただけましたら、「詳細希望」と一言いただくだけでも結構です。\n"
           "担当より詳しいご案内とあわせて、お打ち合わせの日程候補をお送りいたします。")
INTRO = ("私たち株式会社レイハウオリは、Web領域を中心に、ITリソースや専門人材が不足しているお客様を支援しています。\n"
    "社内には、エンジニア・デザイナー・ディレクター・PMなど、各分野のエキスパートが在籍しています。\n"
    "「何を作ればいいのかわからない」「IT周りをまとめて任せたい」といった0→1のご相談から、"
    "「社員の退職に伴うリソース不足を補いたい」といったSESのご要望まで、課題やフェーズに応じて幅広く対応しています。")
SIG = "株式会社Lei Hau'oli\n（tel）050-5497-3411\n（mail）ml.account-sales@leihauoli.com"


def count(body):
    return len(re.sub(r"\s", "", body))


def check(subject, body):
    ng = []
    if count(body) > 400:
        ng.append("A1")
    if not ("【" in subject and "】" in subject and re.search(r"[0-9０-９]", subject)):
        ng.append("A2")
    first = body.strip().splitlines()[0] if body.strip() else ""
    if not re.match(r"^\S+\s+\S+様$", first):
        ng.append("A3")
    if NEW_CTA not in body:
        ng.append("CTA不一致")
    if INTRO not in body:
        ng.append("会社紹介文不一致")
    if SIG not in body:
        ng.append("署名不一致")
    return ng


SYSTEM = f"""あなたはフォーム営業文面のCTA差し替え担当です。ツールは使えません。

## やること
1. 本文中の既存CTA文（例:「ご興味があれば、「興味あり」とご返信ください。」等、返信を促す最後の一文）を、
   次の固定2文に**そのまま**置き換える:

{NEW_CTA}

2. 新CTAは旧CTAより長いため、本文が400字（改行・空白を除く）を超える場合がある。
   超えていたら、**パーソナライズ文（相手固有の課題に触れる文）または「なぜ今」の一文を短く圧縮**して400字以内に収める。
   優先順位: 会社紹介文（3文）・署名（3行）・新CTA（2文）は絶対に削らない。削るのはパーソナライズ文/なぜ今の一文だけ。
   1文にまとめる、修飾語を削る、などで対応する。内容の骨子（相手の課題・なぜ今）は完全には消さない。
3. 宛名行（1行目）・会社紹介文・署名は一切変更しない。

## 出力フォーマット（これ以外の文章は書かない）
## 件名
（元の件名のまま）

## 本文
（CTA差し替え後、必要なら短縮した本文全体）
"""


def claude(prompt, model="sonnet", timeout=180):
    env = {k: v for k, v in os.environ.items()
           if k not in ("CLAUDECODE", "CLAUDE_CODE_CHILD_SESSION", "CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_ENTRYPOINT")}
    cmd = ["claude", "-p", prompt, "--output-format", "json", "--model", model, "--tools", "", "--system-prompt", SYSTEM]
    for attempt in range(5):
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=WORKDIR, env=env)
            data = json.loads(p.stdout.strip().splitlines()[-1]) if p.stdout.strip() else {}
            if data.get("is_error") or not data.get("result"):
                raise RuntimeError(data.get("result") or p.stderr[:300])
            return data["result"]
        except Exception as e:  # noqa
            time.sleep(10 * (attempt + 1))
    raise RuntimeError("claude failed")


def parse(text):
    m1 = re.search(r"##\s*件名\s*\n(.+?)(?=\n##\s*本文)", text, re.S)
    m2 = re.search(r"##\s*本文\s*\n(.+?)\Z", text, re.S)
    return (m1.group(1).strip() if m1 else ""), (m2.group(1).strip() if m2 else "")


def process(row, model):
    body = row["本文"]
    if not body:
        return row, "空欄スキップ"
    prompt = f"件名: {row['件名']}\n\n本文:\n{body}"
    text = claude(prompt, model)
    subj, new_body = parse(text)
    ng = check(subj or row["件名"], new_body)
    if ng:
        text2 = claude(prompt + f"\n\n【前回出力が以下の点で不合格でした。直してください】\n{', '.join(ng)}\n"
                        f"\n【前回の出力】\n{text}\n", model)
        subj2, body2 = parse(text2)
        ng2 = check(subj2 or row["件名"], body2)
        if not ng2 or len(ng2) < len(ng):
            subj, new_body, ng = subj2, body2, ng2
    row = dict(row)
    row["件名"] = subj or row["件名"]
    row["本文"] = new_body or row["本文"]
    row["本文字数"] = count(row["本文"])
    status = "OK" if not ng else f"未解消:{','.join(ng)}"
    return row, status


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--model", default="sonnet")
    args = ap.parse_args()

    rows = list(csv.DictReader(open(SRC, encoding="utf-8")))
    targets = rows if not args.limit else rows[:args.limit]
    results = {}
    ok = fail = 0
    with ThreadPoolExecutor(args.workers) as ex:
        futs = {ex.submit(process, r, args.model): i for i, r in enumerate(targets)}
        for fut in as_completed(futs):
            i = futs[fut]
            try:
                newrow, status = fut.result()
                results[i] = newrow
                if status == "OK" or status == "空欄スキップ":
                    ok += 1
                else:
                    fail += 1
                    print(f"[{newrow['会社名']}] {status}")
            except Exception as e:  # noqa
                fail += 1
                print(f"[{targets[i]['会社名']}] 失敗: {e}")
    # 元の順序を保ちつつ、処理した分だけ差し替える
    for i, newrow in results.items():
        targets[i] = newrow
    if not args.limit:
        rows = targets
    else:
        rows[:args.limit] = targets
    with open(SRC, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=rows[0].keys(), quoting=csv.QUOTE_ALL)
        w.writeheader()
        w.writerows(rows)
    print(f"完了: OK/スキップ {ok} / 要確認 {fail} / 総数 {len(targets)}")


if __name__ == "__main__":
    main()
