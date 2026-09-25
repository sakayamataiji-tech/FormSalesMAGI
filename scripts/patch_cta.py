#!/usr/bin/env python3
"""output/results_patched.csv の604社に、新しいCTA固定文を適用する軽量パッチ第2弾。

CTA文が「ご興味があれば[...]ご返信ください」系の定型パターンにマッチする行は
正規表現で機械的に置換（AI不要・無料）。マッチしない行だけ Sonnet 1回で個別修正する。

使い方:
  python3 scripts/patch_cta.py
出力: output/results_patched.csv を直接上書き更新する（元データは git 履歴に残る）。
"""
import csv, os, re, subprocess, json, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "output/results_patched.csv"
WORKDIR = Path(os.environ.get("MAGI_WORKDIR", "/tmp/magi-patch-work"))
WORKDIR.mkdir(parents=True, exist_ok=True)

NEW_CTA = "ご興味があれば、「興味あり」とご返信ください。"
CTA_PAT = re.compile(r"ご興味があれば[、,]?.{0,20}?ご返信ください。?")

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
    return ng


SYSTEM = f"""あなたはフォーム営業文面のCTA修正担当です。ツールは使えません。
渡された本文の「返信を促す最後の一文（署名の直前）」だけを、次の固定文にそのまま置き換えてください:

{NEW_CTA}

それ以外の文（宛名・会社紹介文・署名・パーソナライズ文・なぜ今の一文）は一切変更しないでください。
既存のCTA文（例:「◯文字だけ返信」「このフォームにご返信ください」等）を探して、その1文だけをこの固定文に差し替えてください。
出力は次の形式のみ。前置き・解説は書かない。

## 件名
（元の件名のまま）

## 本文
（CTA文だけ置き換えた本文全体）
"""


def claude(prompt, model="sonnet", timeout=180):
    env = {k: v for k, v in os.environ.items()
           if k not in ("CLAUDECODE", "CLAUDE_CODE_CHILD_SESSION", "CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_ENTRYPOINT")}
    cmd = ["claude", "-p", prompt, "--output-format", "json", "--model", model, "--tools", "", "--system-prompt", SYSTEM]
    for attempt in range(4):
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


def main():
    rows = list(csv.DictReader(open(SRC, encoding="utf-8")))
    regex_fixed = ai_fixed = skipped = failed = 0
    for r in rows:
        body = r["本文"]
        if not body:
            skipped += 1
            continue
        if NEW_CTA in body:
            continue
        new_body, n = CTA_PAT.subn(NEW_CTA, body, count=1)
        if n:
            r["本文"] = new_body
            r["本文字数"] = count(new_body)
            regex_fixed += 1
        else:
            try:
                prompt = f"件名: {r['件名']}\n\n本文:\n{body}"
                text = claude(prompt)
                subj, new_b = parse(text)
                if new_b and NEW_CTA in new_b and not check(subj or r['件名'], new_b):
                    r["本文"] = new_b
                    r["本文字数"] = count(new_b)
                    ai_fixed += 1
                else:
                    print(f"[要確認] {r['会社名']}: AI修正が不合格 -> {check(subj or r['件名'], new_b)}")
                    failed += 1
            except Exception as e:  # noqa
                print(f"[失敗] {r['会社名']}: {e}")
                failed += 1
    with open(SRC, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=rows[0].keys(), quoting=csv.QUOTE_ALL)
        w.writeheader()
        w.writerows(rows)
    print(f"正規表現で修正: {regex_fixed} / AIで修正: {ai_fixed} / 空欄スキップ: {skipped} / 失敗: {failed} / 総数: {len(rows)}")


if __name__ == "__main__":
    main()
