# -*- coding: utf-8 -*-
"""GitHub Actions 전용: 썸네일 없는 가이드·자동화·자료실 글(예약 + 최근 공개)에 썸네일을 만들어 붙인다.
Claude가 로컬에서 post.py로 예약 발행한 글을 1시간마다 처리."""
import html
import os
import subprocess
import time
import urllib.request
from datetime import datetime, timedelta

import maeil
import thumb

KIND = {"가이드": "guide", "자동화": "auto", "자료실": "res"}


def targets(token):
    out = []
    since = (datetime.now(maeil.KST) - timedelta(days=2)).isoformat(timespec="seconds")
    for status in ("scheduled", "live"):  # 공개 글은 최근 2일만(예전 직접 쓴 글은 건드리지 않음)
        params = {"status": status, "view": "ADMIN", "fetchBodies": "true", "maxResults": "40"}
        if status == "live":
            params["startDate"] = since
        j = maeil.api("GET", "posts", token, params=params)
        for p in j.get("items", []):
            if "mi-thumb" in (p.get("content") or ""):
                continue
            kind = next((KIND[l] for l in p.get("labels", []) if l in KIND), None)
            if kind:
                out.append((p, kind))
    return out


def main():
    token = maeil.access_token()
    todo = targets(token)
    if not todo:
        maeil.log("붙일 썸네일 없음")
        return
    base = maeil.env("PAGES_BASE").rstrip("/")
    made = []
    for p, kind in todo:
        rel = f"thumbs/{datetime.now(maeil.KST):%Y%m}/c-{p['id']}.jpg"
        thumb.make(kind, p["title"], os.path.join(maeil.HERE, rel))
        made.append((p, rel, f"{base}/{rel}"))
        maeil.log(f"썸네일: {p['title']}")
    root = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=maeil.HERE, capture_output=True, text=True).stdout.strip()
    run = lambda *a: subprocess.run(list(a), cwd=root, check=True)
    run("git", "config", "user.name", "maeil-ai-bot")
    run("git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com")
    run("git", "add", *[os.path.join(maeil.HERE, r) for _, r, _ in made])
    run("git", "commit", "-m", f"thumbs x{len(made)}")
    run("git", "pull", "--rebase")
    run("git", "push")
    for i in range(30):  # Pages 반영 대기(최대 약 5분)
        try:
            urllib.request.urlopen(urllib.request.Request(made[-1][2], method="HEAD"), timeout=15)
            break
        except Exception:
            time.sleep(10)
    token = maeil.access_token()
    for p, rel, url in made:
        img = (f'<p class="mi-thumb" style="margin:0 0 20px"><img src="{html.escape(url)}" alt="{html.escape(p["title"])}" '
               f'width="1200" height="630" style="width:100%;height:auto;border-radius:12px"/></p>\n')
        maeil.api("PATCH", f"posts/{p['id']}", token, {"content": img + p["content"]})
        maeil.log(f"붙임: {p['title']}")


if __name__ == "__main__":
    main()
