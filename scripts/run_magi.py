#!/usr/bin/env python3
"""マギシステム バッチランナー（claude -p ヘッドレス実行）

input/companies.csv を上から順に処理し、opener/replier/closer を並列起草 → judge で統合 →
差し戻しは最大3回 → output/results.csv に追記する。results.csv に既にある会社はスキップ（再開可能）。

使い方:
  python3 scripts/run_magi.py                 # 未処理の全社
  python3 scripts/run_magi.py --limit 5       # 未処理の先頭5社
  python3 scripts/run_magi.py --workers 6     # 同時に処理する社数（既定 4）
  python3 scripts/run_magi.py --draft-model sonnet --judge-model opus
中間生成物: output/drafts/<番号>_<会社名>.md（3案＋判決文）、output/run.log
"""
import argparse, csv, json, os, re, subprocess, sys, threading, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILL = ROOT / ".claude/skills/karitoru-form/SKILL.md"
AGENTS = ROOT / ".claude/agents"
COMPANIES = ROOT / "input/companies.csv"
RESULTS = ROOT / "output/results.csv"
DRAFTS = ROOT / "output/drafts"
LOG = ROOT / "output/run.log"
WORKDIR = Path(os.environ.get("MAGI_WORKDIR", "/tmp/magi-work"))
COLS = ["処理日時", "会社名", "部署", "担当者", "件名", "本文", "本文字数",
        "opener_score", "replier_score", "closer_score", "final_score",
        "モード", "差し戻し回数", "採用理由", "備考"]
JST = timezone(timedelta(hours=9))
lock = threading.Lock()
cost_total = [0.0]


def log(msg):
    line = f"{datetime.now(JST).strftime('%H:%M:%S')} {msg}"
    with lock:
        print(line, flush=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")


def strip_frontmatter(text):
    m = re.match(r"^---\n.*?\n---\n", text, re.S)
    return text[m.end():] if m else text


def system_prompt(agent):
    body = strip_frontmatter((AGENTS / f"{agent}.md").read_text(encoding="utf-8"))
    skill = strip_frontmatter(SKILL.read_text(encoding="utf-8"))
    return (body + "\n\n---\n\n# 添付: .claude/skills/karitoru-form/SKILL.md（全文）\n"
            "ツールは使えません。SKILL.md は以下に全文添付してあるので、これを読んだものとして扱ってください。"
            "Bash が使えない場合の文字数は、自分で慎重に数えてください。\n\n" + skill)


def claude(prompt, system, model, timeout=420):
    env = {k: v for k, v in os.environ.items()
           if k not in ("CLAUDECODE", "CLAUDE_CODE_CHILD_SESSION", "CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_ENTRYPOINT")}
    cmd = ["claude", "-p", prompt, "--output-format", "json", "--model", model,
           "--tools", "", "--system-prompt", system]
    for attempt in range(6):
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=WORKDIR, env=env)
            data = json.loads(p.stdout.strip().splitlines()[-1]) if p.stdout.strip() else {}
            if data.get("is_error") or not data.get("result"):
                err = data.get("result") or data.get("error") or p.stderr[:800] or p.stdout[:800]
                raise RuntimeError(f"claude error: {err}")
            with lock:
                cost_total[0] += float(data.get("total_cost_usd") or 0)
            return data["result"]
        except Exception as e:  # noqa
            wait = min(15 * 2 ** attempt, 300)
            log(f"  retry {attempt+1} (wait {wait}s): {str(e)[:400]}")
            time.sleep(wait)
    raise RuntimeError("claude failed 6 times")


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
    return ng


def section(text, start_pat, end_pat):
    m = re.search(start_pat + r"[^\n]*\n(.*?)(?=" + end_pat + r"|\Z)", text, re.S | re.M)
    return m.group(1).strip() if m else ""


def parse_draft(text):
    subject = section(text, r"^#{2,3}\s*件名", r"^#{2,3}\s").strip()
    body = section(text, r"^#{2,3}\s*本文", r"^#{2,3}\s")
    return subject, body


def parse_verdict(text):
    v = {}
    m = re.search(r"##\s*VERDICT(.*)", text, re.S)
    block = m.group(1) if m else ""
    for key in ("STATUS", "MODE", "TARGET"):
        mm = re.search(rf"{key}:\s*(.+)", block)
        v[key] = mm.group(1).strip() if mm else ""
    mm = re.search(r"INSTRUCTION:\s*(.+)", block, re.S)
    v["INSTRUCTION"] = mm.group(1).strip() if mm else ""
    scores = dict(re.findall(r"(opener|replier|closer|final)\s*=\s*(\d+)", block))
    v["SCORES"] = {k: int(x) for k, x in scores.items()}
    final = text.split("## VERDICT")[0]
    fm = re.search(r"##\s*最終文面(.*)", final, re.S)
    ftxt = fm.group(1) if fm else ""
    v["subject"] = section(ftxt, r"^#{2,4}\s*件名", r"^#{2,4}\s").strip()
    v["body"] = section(ftxt, r"^#{2,4}\s*本文", r"^#{2,4}\s")
    reason = section(text, r"^#{2,4}\s*採用理由", r"^#{2,4}\s")
    v["reason"] = re.sub(r"\s+", " ", reason)[:200]
    return v


def draft_prompt(c, remand=None, prev=None):
    p = ("以下の1社に対するフォーム営業文面を、あなたの役割に従って1案だけ書いてください。\n\n"
         f"会社名: {c['会社名']}\n部署: {c['部署']}\n担当者: {c['担当者']}\n"
         f"事業内容: {c['事業内容']}\n課題仮説: {c['課題仮説']}\n")
    if remand:
        p += f"\n【裁判官からの差し戻し指示】\n{remand}\n\n【あなたの前回案】\n{prev}\n"
    return p


def judge_prompt(c, drafts):
    p = ("以下の会社に対する3案を採点し、合成または選抜で最終文面を確定してください。\n\n"
         "【会社データ】\n"
         f"会社名: {c['会社名']} / 部署: {c['部署']} / 担当者: {c['担当者']} / "
         f"事業内容: {c['事業内容']} / 課題仮説: {c['課題仮説']}\n\n")
    for name in ("opener", "replier", "closer"):
        s, b = parse_draft(drafts[name])
        p += (f"【{name}案】（機械計測: 本文{count(b)}字, 絶対条件NG={check(s, b) or 'なし'}）\n"
              f"{drafts[name]}\n\n")
    p += ("注意: Bash は使えないので、上記の機械計測値をそのまま使ってください。"
          "最終文面は本文400字以内（改行・空白除く）に必ず収め、出力フォーマットと VERDICT ブロックを厳守してください。")
    return p


def process(idx, c, args):
    name = c["会社名"]
    tag = f"[{idx}] {name}"
    sys_prompts = {a: system_prompt(a) for a in ("opener", "replier", "closer", "judge")}
    drafts = {}
    with ThreadPoolExecutor(3) as ex:
        futs = {ex.submit(claude, draft_prompt(c), sys_prompts[a], args.draft_model): a
                for a in ("opener", "replier", "closer")}
        for f in as_completed(futs):
            drafts[futs[f]] = f.result()
    log(f"{tag} 3案完了")
    remands = 0
    transcript = []
    verdict = None
    noverdict_retry = 0
    while True:
        jtxt = claude(judge_prompt(c, drafts), sys_prompts["judge"], args.judge_model)
        transcript.append(jtxt)
        verdict = parse_verdict(jtxt)
        if not verdict["STATUS"] and noverdict_retry < 1:
            # ツール呼び出しを試みる等で VERDICT が出なかった → 1回だけ出し直し
            noverdict_retry += 1
            log(f"{tag} VERDICTなし → 出し直し")
            jtxt = claude(judge_prompt(c, drafts) +
                          "\n\n【重要】この環境ではツール（Bash等）は一切呼び出せません。上記の機械計測値を採用し、"
                          "判決文・最終文面・VERDICT ブロックをすべてテキストで一度に出力してください。",
                          sys_prompts["judge"], args.judge_model)
            transcript.append(jtxt)
            verdict = parse_verdict(jtxt)
        if verdict["STATUS"].startswith("ADOPT"):
            ng = check(verdict["subject"], verdict["body"])
            fix = 0
            while ng and fix < 2:
                fix += 1
                log(f"{tag} 最終案NG {ng} ({count(verdict['body'])}字) 修正依頼 {fix}")
                jtxt = claude(judge_prompt(c, drafts) +
                              f"\n\n【前回のあなたの判決】\n{jtxt}\n\n最終案が絶対条件 {','.join(ng)} に違反しています"
                              f"（本文{count(verdict['body'])}字）。修正して VERDICT を出し直してください。",
                              sys_prompts["judge"], args.judge_model)
                transcript.append(jtxt)
                verdict = parse_verdict(jtxt)
                ng = check(verdict["subject"], verdict["body"])
            note = "絶対条件NG:" + ",".join(ng) if ng else ""
            break
        if verdict["STATUS"].startswith("REMAND") and remands < 3 and verdict["TARGET"] in drafts:
            remands += 1
            t = verdict["TARGET"]
            log(f"{tag} 差し戻し{remands} → {t}")
            drafts[t] = claude(draft_prompt(c, verdict["INSTRUCTION"], drafts[t]), sys_prompts[t], args.draft_model)
            continue
        # 差し戻し上限 or 解析不能 → 最高点の案を選抜
        sc = verdict["SCORES"]
        best = max((k for k in ("opener", "replier", "closer") if k in sc), key=lambda k: sc[k], default=None)
        if best:
            s, b = parse_draft(drafts[best])
            verdict.update(subject=s, body=b, MODE="選抜")
            verdict["SCORES"]["final"] = sc.get(best, 0)
            note = "差し戻し上限到達" if remands >= 3 else "判決解析不能・最高点案を選抜"
        else:
            verdict.update(subject="", body="", MODE="")
            note = "失格のみ・要人手"
        break

    DRAFTS.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^\w一-龥ぁ-んァ-ヶー]", "_", name)[:40]
    with open(DRAFTS / f"{idx:04d}_{safe}.md", "w", encoding="utf-8") as f:
        for a in ("opener", "replier", "closer"):
            f.write(f"# {a}\n\n{drafts[a]}\n\n")
        for i, t in enumerate(transcript):
            f.write(f"# judge (round {i+1})\n\n{t}\n\n")
    sc = verdict["SCORES"]
    row = {
        "処理日時": datetime.now(JST).isoformat(timespec="seconds"),
        "会社名": name, "部署": c["部署"], "担当者": c["担当者"],
        "件名": verdict["subject"], "本文": verdict["body"], "本文字数": count(verdict["body"]),
        "opener_score": sc.get("opener", ""), "replier_score": sc.get("replier", ""),
        "closer_score": sc.get("closer", ""), "final_score": sc.get("final", ""),
        "モード": verdict["MODE"], "差し戻し回数": remands, "採用理由": verdict["reason"], "備考": note,
    }
    with lock:
        new = not RESULTS.exists() or RESULTS.stat().st_size == 0
        with open(RESULTS, "a", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=COLS, quoting=csv.QUOTE_ALL)
            if new:
                w.writeheader()
            w.writerow(row)
    log(f"{tag} 完了 final={row['final_score']} {row['モード']} 差戻{remands} {note} 累計${cost_total[0]:.2f}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--draft-model", default="sonnet")
    ap.add_argument("--judge-model", default="opus")
    args = ap.parse_args()
    WORKDIR.mkdir(parents=True, exist_ok=True)
    done = set()
    if RESULTS.exists():
        done = {r["会社名"] for r in csv.DictReader(open(RESULTS, encoding="utf-8"))}
    companies = list(csv.DictReader(open(COMPANIES, encoding="utf-8")))
    todo = [(i + 1, c) for i, c in enumerate(companies) if c["会社名"] not in done]
    if args.limit:
        todo = todo[:args.limit]
    log(f"=== start: 全{len(companies)}社 / 済{len(done)} / 今回{len(todo)}社 workers={args.workers} "
        f"draft={args.draft_model} judge={args.judge_model}")
    ok = fail = 0
    with ThreadPoolExecutor(args.workers) as ex:
        futs = {ex.submit(process, i, c, args): (i, c) for i, c in todo}
        for f in as_completed(futs):
            i, c = futs[f]
            try:
                f.result(); ok += 1
            except Exception as e:  # noqa
                fail += 1
                log(f"[{i}] {c['会社名']} 失敗: {str(e)[:300]}")
    log(f"=== end: 成功{ok} 失敗{fail} 累計コスト${cost_total[0]:.2f}")


if __name__ == "__main__":
    main()
