#!/usr/bin/env python3
"""既存の output/results.csv（フルパーソナライズ604社）を、修正済み会社プロフィール
（SKILL.md 2026-09-18版）に合わせて安く直す「修正パス」。

opener/replier/closer/judgeのフル4エージェントを再実行すると1社あたり約$0.5かかり
604社で$300超になる。ここでは1社1回・Sonnetのみの軽量な修正呼び出しにして、
コストを1社あたり約$0.01〜0.02（604社で$6〜12程度）に抑える。

直す内容:
  - 会社紹介文・USP・実績まわりを、SKILL.md §4-1（クライアント指定の固定パラグラフ）に
    一字一句そのまま差し替える。
  - 署名を SKILL.md §4-3（固定・本物の連絡先）に一字一句そのまま差し替える。
  - 「唯一」「業界No.1」等の禁止表現、"◯文字だけ返信" のCTA小技を除去・自然な表現に言い換える。
  - パーソナライズ（相手固有の一文）・なぜ今・CTAの主旨・宛名行はできるだけ残す。
  - 本文400字以内、件名【】+数字、宛名行の絶対条件は再検証する。

使い方:
  python3 scripts/patch_results.py                # 未処理分すべて
  python3 scripts/patch_results.py --limit 20      # 先頭20件だけ
  python3 scripts/patch_results.py --workers 6
出力: output/results_patched.csv（既存 output/results.csv とは別ファイル。
      results_patched.csv に既にある会社はスキップ＝再開可能）
"""
import argparse, csv, os, re, subprocess, sys, threading, time, json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILL = ROOT / ".claude/skills/karitoru-form/SKILL.md"
SRC = ROOT / "output/results.csv"
DST = ROOT / "output/results_patched.csv"
LOG = ROOT / "output/patch.log"
WORKDIR = Path(os.environ.get("MAGI_WORKDIR", "/tmp/magi-patch-work"))
JST = timezone(timedelta(hours=9))
lock = threading.Lock()
cost_total = [0.0]

INTRO = (
    "私たち株式会社レイハウオリは、Web領域を中心に、ITリソースや専門人材が不足しているお客様を支援しています。\n"
    "社内には、エンジニア・デザイナー・ディレクター・PMなど、各分野のエキスパートが在籍しています。\n"
    "「何を作ればいいのかわからない」「IT周りをまとめて任せたい」といった0→1のご相談から、"
    "「社員の退職に伴うリソース不足を補いたい」といったSESのご要望まで、課題やフェーズに応じて幅広く対応しています。"
)
SIGNATURE = "株式会社Lei Hau'oli\n（tel）050-5497-3411\n（mail）ml.account-sales@leihauoli.com"
BANNED = [r"唯一", r"業界No\.?1", r"日本最大", r"の\d文字だけ", r"要確認", r"〇〇",
          r"2008年", r"エンジニア約?40名", r"美容予約", r"大手BtoC"]

COLS = ["処理日時", "会社名", "部署", "担当者", "件名", "本文", "本文字数",
        "opener_score", "replier_score", "closer_score", "final_score",
        "モード", "差し戻し回数", "採用理由", "備考"]


def log(msg):
    line = f"{datetime.now(JST).strftime('%H:%M:%S')} {msg}"
    with lock:
        print(line, flush=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")


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
    for pat in BANNED:
        if re.search(pat, subject) or re.search(pat, body):
            ng.append(f"禁止語:{pat}")
    if INTRO not in body:
        ng.append("会社紹介文が一字一句一致していない")
    if SIGNATURE not in body:
        ng.append("署名が一字一句一致していない")
    return ng


def claude(prompt, system, model="sonnet", timeout=300):
    env = {k: v for k, v in os.environ.items()
           if k not in ("CLAUDECODE", "CLAUDE_CODE_CHILD_SESSION", "CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_ENTRYPOINT")}
    cmd = ["claude", "-p", prompt, "--output-format", "json", "--model", model,
           "--tools", "", "--system-prompt", system]
    for attempt in range(5):
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=WORKDIR, env=env)
            data = json.loads(p.stdout.strip().splitlines()[-1]) if p.stdout.strip() else {}
            if data.get("is_error") or not data.get("result"):
                err = data.get("result") or data.get("error") or p.stderr[:500] or p.stdout[:500]
                raise RuntimeError(f"claude error: {err}")
            with lock:
                cost_total[0] += float(data.get("total_cost_usd") or 0)
            return data["result"]
        except Exception as e:  # noqa
            wait = min(15 * 2 ** attempt, 240)
            log(f"  retry {attempt+1} (wait {wait}s): {str(e)[:300]}")
            time.sleep(wait)
    raise RuntimeError("claude failed 5 times")


SYSTEM = f"""あなたはフォーム営業文面の「修正担当」です。ツールは使えません。
既存の件名・本文を渡すので、以下のルールで**最小限の書き換え**をして出力してください。
新しい主張・数字・事例を作ってはいけません。

## 直すこと（必須）
1. 会社紹介文（自社が何者かを説明している部分。USP・実績・"エンジニア約40名"などの数字を含む文一式）を、
   次の3文に**一字一句そのまま**置き換える。改変・要約・言い換え禁止:

{INTRO}

2. 署名を、次の3行に**一字一句そのまま**置き換える（プレースホルダーや個人名は使わない）:

{SIGNATURE}

3. 「唯一」「業界No.1」「日本最大」などの排他的な言い切りは削除するか、事実に即した表現に言い換える。
4. 「◯文字だけ返信してください」のような返信内容を文字数指定するCTAは、自然な一文
   （例:「ご興味があれば、このフォームにご返信ください」）に言い換える。
5. 2008年設立・エンジニア約40名・美容予約サービス・大手BtoC直請け、など§4-1にない実績・数字・固有名詞は
   本文から削除する（代替の数字は作らない）。

## 残すこと（削らない）
- 1行目の宛名（「{{部署}} {{担当者名}}様」形式）はそのまま。
- 相手固有のパーソナライズ文（会社名・事業内容・課題仮説に触れている文）は、内容が事実と反しない限りそのまま残す。
- 「なぜ今動くべきか」の一文、CTAの主旨（返信を促す1アクション）は残す。

## 字数調整（最重要・必ず守る）
会社紹介文（3文）は約200字、署名（3行）は約60字で、合計すると**それだけで約260字**を使う。
本文は改行・空白を除いて**400字以内が絶対条件**なので、パーソナライズ文＋なぜ今＋CTAは**残り140字程度**に収めなければならない。
- パーソナライズ文は**1文だけ**（60字以内）に圧縮する。元の本文に複数文あれば、最も相手固有性の強い1文だけ残し、他は削除する。
- 「なぜ今」は短い1節（30字以内）にする。数字を使った長い説明は削る。
- 旧プロフィール由来の具体的な提供形態の約束（「フロントエンド専門」「1名からチーム単位」「React/Vue専門エンジニア」など、今の§4-1にない表現）は削除するか、「専門人材」「必要な体制」のような一般化した言葉に置き換える。
- CTAの一文（「ご興味があれば、このフォームにご返信ください」等）は10〜20字程度に短く保つ。
- 出力する前に自分で文字数を数え、400字を超えていたらパーソナライズ文をさらに削って調整してから出力すること。字数超過での提出は不合格。

## 件名の数字（絶対条件・必ず守る）
件名には【】の組と算用数字が最低1つ必要（絶対条件）。旧プロフィール由来の数字（「1名から」「エンジニア約40名」など）を件名から削除する場合は、
**必ず代わりの数字を件名に残す**こと。代替案: 「0→1」という言葉自体に含まれる数字（0と1）を使う、CTAの所要時間、期限・締切の数字、
本文中の他の正当な数字を使う。件名から数字が消えた状態で出力してはならない。

## 出力フォーマット（これ以外の文章は書かない）
## 件名
（修正後の件名。【】と数字は維持する）

## 本文
（修正後の本文。宛名行から署名まで）
"""


def build_prompt(row):
    return (
        f"以下のフォーム営業文面を、上記ルールに従って修正してください。\n\n"
        f"【現在の件名】\n{row['件名']}\n\n【現在の本文】\n{row['本文']}\n"
    )


def parse(text):
    m1 = re.search(r"##\s*件名\s*\n(.+?)(?=\n##\s*本文)", text, re.S)
    m2 = re.search(r"##\s*本文\s*\n(.+?)\Z", text, re.S)
    subject = m1.group(1).strip() if m1 else ""
    body = m2.group(1).strip() if m2 else ""
    return subject, body


def process(row, model):
    prompt = build_prompt(row)
    text = claude(prompt, SYSTEM, model)
    subject, body = parse(text)
    ng = check(subject, body)
    if ng:
        text2 = claude(
            prompt + f"\n\n【前回出力が以下の点で不合格でした。直して出力し直してください】\n{', '.join(ng)}\n"
            f"\n【前回のあなたの出力】\n{text}\n",
            SYSTEM, model)
        subject2, body2 = parse(text2)
        ng2 = check(subject2, body2)
        if not ng2 or len(ng2) < len(ng):
            subject, body, ng = subject2, body2, ng2
    note = "修正パス(2026-09-18)"
    if ng:
        note += f" / 未解消:{','.join(ng)}"
    row = dict(row)
    row["件名"] = subject or row["件名"]
    row["本文"] = body or row["本文"]
    row["本文字数"] = count(row["本文"])
    row["備考"] = (row.get("備考", "") + " " + note).strip()
    return row


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=5)
    ap.add_argument("--model", default="sonnet")
    args = ap.parse_args()
    WORKDIR.mkdir(parents=True, exist_ok=True)

    src_rows = list(csv.DictReader(open(SRC, encoding="utf-8")))
    done = set()
    if DST.exists():
        done = {r["会社名"] for r in csv.DictReader(open(DST, encoding="utf-8"))}
    todo = [r for r in src_rows if r["会社名"] not in done]
    if args.limit:
        todo = todo[:args.limit]
    log(f"=== start: 全{len(src_rows)}社 / 済{len(done)} / 今回{len(todo)}社 workers={args.workers} model={args.model}")

    ok = fail = 0
    new = not DST.exists() or DST.stat().st_size == 0
    with open(DST, "a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLS, quoting=csv.QUOTE_ALL)
        if new:
            w.writeheader()
        with ThreadPoolExecutor(args.workers) as ex:
            futs = {ex.submit(process, r, args.model): r for r in todo}
            for fut in as_completed(futs):
                r = futs[fut]
                try:
                    out = fut.result()
                    with lock:
                        w.writerow(out)
                        f.flush()
                    ok += 1
                    unresolved = "未解消" in out["備考"]
                    log(f"[{out['会社名']}] 完了{'（要目視確認）' if unresolved else ''} 累計${cost_total[0]:.2f}")
                except Exception as e:  # noqa
                    fail += 1
                    log(f"[{r['会社名']}] 失敗: {str(e)[:300]}")
    log(f"=== end: 成功{ok} 失敗{fail} 累計コスト${cost_total[0]:.2f}")


if __name__ == "__main__":
    main()
