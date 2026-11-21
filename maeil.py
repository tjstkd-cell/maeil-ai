# -*- coding: utf-8 -*-
"""매일 AI - 속보·툴 글 자동 작성·발행

흐름
  속보: 공식 블로그·IT매체·구글뉴스 RSS 수집 → 원문 일부 읽기 → Gemini가 4~5개 골라 길게 해설 → Blogger 발행
  툴  : GitHub 최근 급상승 AI 저장소 → README 읽기 → Gemini가 1개를 깊게 소개 → Blogger 발행

사용법
  python maeil.py news --slot am --preview   (아침: 해외 위주 / pm: 국내 위주, 발행 안 하고 미리보기)
  python maeil.py tool --preview
  python maeil.py news --slot am             (바로 발행)
  python maeil.py auth                       (처음 한 번, Blogger 연결)

필요한 값(.env 또는 환경변수)
  GEMINI_API_KEY, BLOGGER_CLIENT_ID, BLOGGER_CLIENT_SECRET, BLOG_ID,
  BLOGGER_REFRESH_TOKEN(없으면 .blogger_token.json), GITHUB_TOKEN(선택)
"""
import argparse
import html
import http.server
import json
import os
import re
import secrets
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
STATE = os.path.join(DATA, "published.json")
LOG = os.path.join(DATA, "publish_log.csv")
TOKEN = os.path.join(HERE, ".blogger_token.json")
KST = timezone(timedelta(hours=9))
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
MODELS = ["gemini-3-flash-preview", "gemini-2.5-flash", "gemini-flash-latest"]
MIN_CHARS = 1800  # 본문 글자 수(태그 제외)가 이보다 짧으면 한 번 더 쓰게 함

GN = "https://news.google.com/rss/search?"
FEEDS = {
    "am": [  # 밤사이 해외
        ("OpenAI", "https://openai.com/news/rss.xml"),
        ("Google AI", "https://blog.google/technology/ai/rss/"),
        ("Hugging Face", "https://huggingface.co/blog/feed.xml"),
        ("TechCrunch", "https://techcrunch.com/category/artificial-intelligence/feed/"),
        ("The Verge", "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml"),
        ("Google News", GN + urllib.parse.urlencode({"q": "OpenAI OR Anthropic OR Gemini OR \"AI model\" when:1d", "hl": "en-US", "gl": "US", "ceid": "US:en"})),
    ],
    "pm": [  # 낮 국내 + 해외 보강
        ("AI타임스", "https://www.aitimes.com/rss/allArticle.xml"),
        ("Google 뉴스", GN + urllib.parse.urlencode({"q": "생성형 AI OR 챗GPT OR 클로드 OR 제미나이 when:1d", "hl": "ko", "gl": "KR", "ceid": "KR:ko"})),
        ("Google 뉴스", GN + urllib.parse.urlencode({"q": "인공지능 서비스 출시 when:1d", "hl": "ko", "gl": "KR", "ceid": "KR:ko"})),
        ("TechCrunch", "https://techcrunch.com/category/artificial-intelligence/feed/"),
        ("The Verge", "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml"),
    ],
}
GH_TOPICS = ["llm", "ai-agents", "mcp", "generative-ai", "claude", "ai"]


# ------------------------------------------------------------ 설정
def env(name):
    v = os.environ.get(name, "").strip()
    p = os.path.join(HERE, ".env")
    if not v and os.path.exists(p):
        with open(p, encoding="utf-8-sig") as f:
            for line in f:
                if line.startswith(name + "="):
                    v = line.split("=", 1)[1].strip().strip('"').strip("'")
    return v


def need(name):
    v = env(name)
    if not v:
        sys.exit(f"{name} 값이 없어요. .env 또는 환경변수에 넣어주세요.")
    return v


def log(*a):
    print(datetime.now(KST).strftime("%H:%M:%S"), *a, flush=True)


def get(url, headers=None, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode(r.headers.get_content_charset() or "utf-8", errors="replace")


def strip_tags(s):
    s = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", s or "")
    s = re.sub(r"(?s)<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", html.unescape(s)).strip()


# ------------------------------------------------------------ 기록
def load_done():
    if os.path.exists(STATE):
        with open(STATE, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_done(done):
    os.makedirs(DATA, exist_ok=True)
    cutoff = (datetime.now(KST) - timedelta(days=60)).strftime("%Y-%m-%d")
    done = {k: v for k, v in done.items() if v >= cutoff}
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(done, f, ensure_ascii=False, indent=0)


def norm_title(t):
    return re.sub(r"[^0-9a-z가-힣]", "", t.lower())[:40]


# ------------------------------------------------------------ 뉴스 수집
def parse_feed(src, xml_text):
    out = []
    try:
        root = ET.fromstring(xml_text.encode("utf-8"))
    except ET.ParseError:
        return out
    ns = {"a": "http://www.w3.org/2005/Atom"}
    items = root.findall(".//item")
    if items:
        for it in items:
            title = strip_tags(it.findtext("title") or "")
            link = (it.findtext("link") or "").strip()
            desc = strip_tags(it.findtext("description") or "")
            pub = it.findtext("pubDate") or ""
            s = it.find("source")
            source = strip_tags(s.text) if s is not None and s.text else src
            if src.startswith("Google") and " - " in title:  # 구글뉴스 제목 끝의 " - 언론사" 제거
                title, source = title.rsplit(" - ", 1)[0], title.rsplit(" - ", 1)[1]
            try:
                dt = parsedate_to_datetime(pub)
            except Exception:
                dt = None
            out.append({"title": title, "link": link, "summary": desc[:600], "source": source, "date": dt})
    else:
        for e in root.findall("a:entry", ns):
            title = strip_tags(e.findtext("a:title", "", ns))
            lk = e.find("a:link[@rel='alternate']", ns) or e.find("a:link", ns)
            link = lk.get("href") if lk is not None else ""
            desc = strip_tags(e.findtext("a:summary", "", ns) or e.findtext("a:content", "", ns))
            pub = e.findtext("a:published", "", ns) or e.findtext("a:updated", "", ns)
            try:
                dt = datetime.fromisoformat(pub.replace("Z", "+00:00"))
            except Exception:
                dt = None
            out.append({"title": title, "link": link, "summary": desc[:600], "source": src, "date": dt})
    return out


def article_text(url):
    """원문 본문 일부(사실 확인용). 실패하면 빈 문자열."""
    if not url or "news.google.com" in url:
        return ""
    try:
        page = get(url, timeout=15)
    except Exception:
        return ""
    paras = re.findall(r"(?is)<p[^>]*>(.*?)</p>", page)
    text = " ".join(t for t in (strip_tags(p) for p in paras) if len(t) > 40)
    return text[:2500]


def collect_news(slot, done, hours=30):
    now = datetime.now(timezone.utc)
    items, seen = [], set()
    for src, url in FEEDS[slot]:
        try:
            got = parse_feed(src, get(url))
        except Exception as e:
            log(f"  {src} 수집 실패: {e}")
            continue
        n = 0
        for it in got:
            if not it["title"] or not it["link"]:
                continue
            if it["date"] and (now - it["date"].astimezone(timezone.utc)) > timedelta(hours=hours):
                continue
            key = norm_title(it["title"])
            if key in seen or f"url:{it['link']}" in done or f"t:{key}" in done:
                continue
            seen.add(key)
            items.append(it)
            n += 1
            if n >= 8:
                break
        log(f"  {src}: {n}건")
    items.sort(key=lambda x: x["date"] or now, reverse=True)
    return items[:24]


# ------------------------------------------------------------ Gemini
def gemini(prompt, temperature=0.7):
    key = need("GEMINI_API_KEY")
    body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": temperature, "responseMimeType": "application/json"}}
    last = ""
    for model in MODELS:
        for attempt in range(3):
            req = urllib.request.Request(
                f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                data=json.dumps(body).encode(), method="POST",
                headers={"x-goog-api-key": key, "content-type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=240) as r:
                    j = json.loads(r.read().decode())
                text = "".join(p.get("text", "") for p in j["candidates"][0]["content"]["parts"])
                return json.loads(text)
            except urllib.error.HTTPError as e:
                last = f"{model} {e.code}"
                if e.code in (403, 404):
                    break
                time.sleep(8 * (attempt + 1))
            except Exception as e:
                last = f"{model} {e}"
                time.sleep(5)
        log(f"  {last} → 다음 모델")
    sys.exit(f"Gemini 실패: {last}")


def review(d, sources):
    """2차 검수: 원문과 대조해 과장·지어낸 내용을 고친 같은 형식의 JSON을 돌려받는다."""
    prompt = f"""너는 꼼꼼한 팩트체커야. 아래 [초안 JSON]을 [자료]와 한 문장씩 대조해서 고쳐줘.
- 자료에 없는 숫자·날짜·이름·기능·인용은 지우거나 '자세한 내용은 아직 공개되지 않았어요'로 바꾼다.
- 자료가 '주장/검증 전/전망'인데 초안이 '성공/해결/확정'처럼 단정하면 '~라고 주장했어요', '검증 중이에요'로 고친다. title, heading, summary3, oneline도 똑같이 점검한다.
- 자료에 반론·경쟁 주장이 있는데 빠졌으면 한 문장 넣는다.
- 문체(매일이 말투), 길이, 구조, 키 이름은 그대로 유지한다. 원문 문장을 그대로 옮기지 않는다.
JSON으로만, 초안과 같은 키 구조로 답해.

[초안 JSON]
{json.dumps(d, ensure_ascii=False)}

[자료]
{sources}"""
    try:
        r = gemini(prompt, temperature=0.2)
        return r if isinstance(r, dict) and r.get("title") else d
    except SystemExit:
        return d


ALLOWED = {"p", "h2", "h3", "ul", "ol", "li", "strong", "b", "em", "code", "pre", "blockquote", "br", "table", "tr", "th", "td", "thead", "tbody"}


def clean_html(s):
    s = re.sub(r"(?is)<(script|style|iframe|object)[^>]*>.*?</\1>", "", s or "")
    s = re.sub(r"(?is)</?a\b[^>]*>", "", s)  # 링크는 우리가 직접 붙임(지어낸 주소 방지)

    def fix(m):
        tag = m.group(2).lower()
        return f"<{m.group(1)}{tag}>" if tag in ALLOWED else ""
    return re.sub(r"<(/?)([a-zA-Z0-9]+)[^>]*>", fix, s)


def text_len(s):
    return len(strip_tags(s).replace(" ", ""))


STYLE = """말투: AI 소식 배달 로봇 '매일이'. 친근한 존댓말(~해요, ~예요), 쉬운 우리말, 전문용어는 처음 나올 때 괄호로 풀이.
절대 규칙:
- 아래 [자료]에 있는 사실만 쓴다. 자료에 없는 숫자·날짜·인용·기능은 지어내지 않는다. 모르면 '아직 자세한 내용은 공개되지 않았어요'라고 쓴다.
- 자료가 '주장(claims)', '검증되면(if verified)', '전망', '루머'인 내용은 확정된 사실처럼 쓰지 않는다. 반드시 '~라고 주장했어요', '아직 검증 중이에요'처럼 자료의 확신 정도를 그대로 지킨다. 반론·경쟁 주장이 자료에 있으면 함께 쓴다.
- 제목·소제목·요약도 자료보다 과장하지 않는다('성공', '해결', '최초' 같은 단정은 자료가 단정할 때만).
- 원문 문장을 그대로 옮기거나 직역하지 말고, 내용을 이해한 뒤 완전히 새 문장으로 쓴다.
- 링크·URL은 본문에 쓰지 않는다(따로 붙인다).
- HTML은 <p>, <h3>, <ul>, <li>, <strong>, <code>, <pre>만 사용한다."""


def write_news(items, slot, now):
    md = f"{now.month}월 {now.day}일"
    when = "아침" if slot == "am" else "오후"
    src = []
    for i, it in enumerate(items):
        src.append(f"[{i}] 제목: {it['title']}\n출처: {it['source']}\n요약: {it['summary']}\n본문 일부: {it.get('text', '')}")
    sources = "\n\n".join(src)
    prompt = f"""{STYLE}

너는 한국 AI 뉴스 블로그 '매일 AI'의 편집자야. 아래 [자료] 중에서 일반인·직장인·부업하는 사람에게 의미 있는 소식 4~5개를 골라
'{md} {when} AI 속보' 글을 써줘. 비슷한 소식은 하나로 합쳐. 광고성·주식 시황·연예 기사는 빼.

각 소식마다:
- heading: 한눈에 들어오는 소제목(25자 안팎, 핵심 키워드 포함)
- idx: 근거로 쓴 자료 번호 배열(예: [0, 3])
- html: <p> 3~4개, 합쳐서 500자 이상. 순서대로 ① 무슨 일인지 ② 왜 중요한지(배경) ③ 우리에게 어떤 의미인지(쓸 수 있는 곳, 주의할 점)

전체 글자 수(태그 제외) 2,200~3,200자.

JSON으로만 답해:
{{"title": "검색에 잘 걸리는 제목 (예: '{md} AI 속보: ○○ 외 4가지' 형식, 45자 이내)",
 "slug": "영어 소문자 단어 4~7개를 공백으로 (주소용, 예: openai new model ai news)",
 "summary3": ["오늘 3줄 요약 1", "2", "3"],
 "items": [{{"heading": "...", "idx": [0], "html": "<p>...</p>"}}],
 "comment": "매일이의 한마디 (1~2문장)",
 "tags": ["핵심 키워드 3~5개 (예: 오픈AI, 클로드)"]}}

[자료]
""" + sources
    return review(gemini(prompt), sources)


def build_news_html(d, items, now):
    parts = ['<div style="background:#fff3a6;border:1px dashed #c9a800;border-radius:12px;padding:16px 18px;margin:0 0 24px">'
             '<b>오늘 3줄 요약</b><ul style="margin:8px 0 0;padding-left:20px">' +
             "".join(f"<li>{html.escape(s)}</li>" for s in d.get("summary3", [])[:3]) + "</ul></div>"]
    used = []
    for n, x in enumerate(d.get("items", []), 1):
        idx = [i for i in x.get("idx", []) if isinstance(i, int) and 0 <= i < len(items)]
        parts.append(f"<h2>{n}. {html.escape(x.get('heading', ''))}</h2>")
        parts.append(clean_html(x.get("html", "")))
        if idx:
            links = " · ".join(f'<a href="{html.escape(items[i]["link"])}" rel="nofollow noopener" target="_blank">{html.escape(items[i]["source"])}</a>' for i in idx)
            parts.append(f'<p style="font-size:14px;color:#6b6b6b">원문: {links}</p>')
            used += idx
    if d.get("comment"):
        parts.append(f"<blockquote><b>매일이의 한마디</b><br/>{html.escape(d['comment'])}</blockquote>")
    parts.append('<p style="font-size:13px;color:#6b6b6b">이 글은 각 언론사·기업 공식 발표를 바탕으로 매일 AI가 요약·해설한 글이에요. '
                 f'{now:%Y.%m.%d %H:%M} 기준이며, 자세한 내용은 원문을 확인해 주세요.</p>')
    return "\n".join(parts), sorted(set(used))


# ------------------------------------------------------------ 툴 수집
def gh(url, raw=False):
    h = {"Accept": "application/vnd.github.raw" if raw else "application/vnd.github+json"}
    if env("GITHUB_TOKEN"):
        h["Authorization"] = "Bearer " + env("GITHUB_TOKEN")
    return get(url, h, timeout=30)


def collect_tools(done):
    since = (datetime.now(KST) - timedelta(days=30)).strftime("%Y-%m-%d")
    seen_base, seen_owner, out = set(), set(), []
    for topic in GH_TOPICS:
        q = f"created:>{since} stars:>200 fork:false archived:false topic:{topic}"
        try:
            j = json.loads(gh("https://api.github.com/search/repositories?" + urllib.parse.urlencode(
                {"q": q, "sort": "stars", "order": "desc", "per_page": 20})))
        except Exception as e:
            log(f"  GitHub {topic} 실패: {e}")
            continue
        for x in j.get("items", []):
            base = re.sub(r"[-_.]?[a-z0-9]{0,8}$", "", x["name"].lower())[:14]
            if f"repo:{x['full_name']}" in done or base in seen_base or x["owner"]["login"] in seen_owner:
                continue
            if not x.get("description") or len(x["description"]) < 20:
                continue
            seen_base.add(base)
            seen_owner.add(x["owner"]["login"])
            out.append(x)
    out.sort(key=lambda x: -x["stargazers_count"])
    return out


def readme(full):
    try:
        t = gh(f"https://api.github.com/repos/{full}/readme", raw=True)
    except Exception:
        return ""
    t = re.sub(r"(?s)<!--.*?-->", "", t)
    t = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", t)  # 이미지 제거
    t = re.sub(r"(?is)<img[^>]*>", "", t)
    return t[:9000]


def write_tool(repo, rd):
    info = (f"이름: {repo['full_name']}\n설명: {repo.get('description')}\n별: {repo['stargazers_count']}\n"
            f"언어: {repo.get('language')}\n라이선스: {(repo.get('license') or {}).get('spdx_id')}\n"
            f"주제: {', '.join(repo.get('topics', []))}\n만든 날: {repo['created_at'][:10]}")
    prompt = f"""{STYLE}

너는 한국 AI 블로그 '매일 AI'의 툴 리뷰어야. 아래 [자료]의 GitHub 프로젝트 하나를 개발자가 아닌 사람도 이해하게 깊이 소개하는 글을 써줘.
설치 명령어는 README에 실제로 있는 것만 <pre><code>로 옮기고, 없으면 '설치 방법은 README를 확인하세요'라고 써.

sections는 아래 순서로 (heading은 자연스럽게 바꿔도 됨):
1. 이 툴, 한마디로 뭐예요? (무슨 문제를 푸는지)
2. 이런 분께 추천해요 (구체적인 사용 장면 3~4개, <ul>)
3. 주요 기능 살펴보기 (기능별 <h3> + 설명)
4. 설치·사용 방법 (단계별)
5. 써보기 전에 알아둘 점 (라이선스, 아직 초기 프로젝트인지, 필요한 환경, 비용 등 README 근거로)
각 섹션 html은 300자 이상. 전체 글자 수(태그 제외) 2,200~3,500자.

JSON으로만 답해:
{{"title": "검색에 잘 걸리는 제목 (툴 이름 + 무엇을 해주는지, 45자 이내)",
 "slug": "영어 소문자 단어 4~7개 공백 구분 (예: graphify knowledge graph tool review)",
 "oneline": "카드용 한 줄 소개 (40자 이내)",
 "sections": [{{"heading": "...", "html": "<p>...</p>"}}],
 "comment": "매일이의 한마디 (1~2문장)"}}

[자료]
{info}

README:
{rd}"""
    return review(gemini(prompt), info + "\n\nREADME:\n" + rd)


def build_tool_html(d, repo, now):
    lic = (repo.get("license") or {}).get("spdx_id") or "표기 없음"
    row = lambda k, v: f'<tr><th style="text-align:left;padding:7px 10px;background:#fff3a6;border:1px solid #e6e3d8;width:90px">{k}</th><td style="padding:7px 10px;border:1px solid #e6e3d8">{v}</td></tr>'
    parts = [f'<p><strong>{html.escape(d.get("oneline", ""))}</strong></p>',
             '<table style="width:100%;border-collapse:collapse;font-size:15px;margin:12px 0 24px">' +
             row("저장소", f'<a href="{html.escape(repo["html_url"])}" rel="nofollow noopener" target="_blank">{html.escape(repo["full_name"])}</a>') +
             row("GitHub 별", f'★ {repo["stargazers_count"]:,}개 ({now:%m.%d} 기준)') +
             row("언어", html.escape(repo.get("language") or "-")) +
             row("라이선스", html.escape(lic)) +
             row("공개일", repo["created_at"][:10]) + "</table>"]
    for s in d.get("sections", []):
        parts.append(f"<h2>{html.escape(s.get('heading', ''))}</h2>")
        parts.append(clean_html(s.get("html", "")))
    if d.get("comment"):
        parts.append(f"<blockquote><b>매일이의 한마디</b><br/>{html.escape(d['comment'])}</blockquote>")
    parts.append('<p style="font-size:13px;color:#6b6b6b">이 글은 프로젝트의 공개 README를 바탕으로 매일 AI가 정리한 소개 글이에요. '
                 '오픈소스는 업데이트가 잦으니 설치 전 저장소의 최신 안내를 꼭 확인해 주세요.</p>')
    return "\n".join(parts)


# ------------------------------------------------------------ Blogger
def post_form(url, data):
    req = urllib.request.Request(url, data=urllib.parse.urlencode(data).encode(), method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def auth():
    cid, sec = need("BLOGGER_CLIENT_ID"), need("BLOGGER_CLIENT_SECRET")
    state, got = secrets.token_urlsafe(16), {}

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if qs.get("state", [""])[0] == state:
                got["code"] = qs.get("code", [""])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write("<h2>연결 완료! 창을 닫아도 돼요.</h2>".encode())

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    redirect = f"http://127.0.0.1:{srv.server_port}/"
    url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode({
        "client_id": cid, "redirect_uri": redirect, "response_type": "code",
        "scope": "https://www.googleapis.com/auth/blogger", "access_type": "offline", "prompt": "consent", "state": state})
    print("브라우저에서 블로그 주인 구글 계정으로 '허용'을 눌러주세요.\n" + url, flush=True)
    webbrowser.open(url)
    t = threading.Thread(target=srv.handle_request)
    t.start()
    t.join(timeout=600)
    srv.server_close()
    if not got.get("code"):
        sys.exit("인증 실패")
    tok = post_form("https://oauth2.googleapis.com/token", {
        "code": got["code"], "client_id": cid, "client_secret": sec, "redirect_uri": redirect, "grant_type": "authorization_code"})
    with open(TOKEN, "w", encoding="utf-8") as f:
        json.dump({"refresh_token": tok["refresh_token"]}, f)
    print("인증 완료.")


def access_token():
    rt = env("BLOGGER_REFRESH_TOKEN")
    if not rt:
        if not os.path.exists(TOKEN):
            sys.exit("먼저 python maeil.py auth 를 실행해 주세요.")
        with open(TOKEN, encoding="utf-8") as f:
            rt = json.load(f)["refresh_token"]
    return post_form("https://oauth2.googleapis.com/token", {
        "refresh_token": rt, "client_id": need("BLOGGER_CLIENT_ID"),
        "client_secret": need("BLOGGER_CLIENT_SECRET"), "grant_type": "refresh_token"})["access_token"]


def api(method, path, token, body=None, params=None):
    url = f"https://www.googleapis.com/blogger/v3/blogs/{need('BLOG_ID')}/{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None, method=method,
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=utf-8"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Blogger API {e.code}: {e.read().decode(errors='replace')[:300]}")


def slugify(s):
    s = re.sub(r"[^a-z0-9 ]", " ", (s or "").lower())
    return " ".join(s.split()[:7]) or "maeil ai"


def add_thumb(post, kind, now, slot="", repo=None):
    """썸네일 만들어 thumbs/에 저장하고 본문 맨 위에 넣는다. 이미지 주소는 PAGES_BASE 기준."""
    import thumb
    rel = f"thumbs/{now:%Y%m}/{slugify(post['slug']).replace(' ', '-')}-{now:%d%H%M}.jpg"
    date = f"{now.month}.{now.day} " + ("아침" if slot == "am" else "오후") if kind == "news" else ""
    thumb.make(kind, post["title"], os.path.join(HERE, rel), date=date,
               repo=repo["full_name"] if repo else "", stars=repo["stargazers_count"] if repo else None)
    base = env("PAGES_BASE").rstrip("/")
    url = f"{base}/{rel}" if base else "file:///" + os.path.join(HERE, rel).replace("\\", "/")
    post["content"] = (f'<p class="mi-thumb" style="margin:0 0 20px"><img src="{html.escape(url)}" alt="{html.escape(post["title"])}" '
                       f'width="1200" height="630" style="width:100%;height:auto;border-radius:12px"/></p>\n' + post["content"])
    post["thumb_rel"], post["thumb_url"] = rel, url


def push_thumb(post):
    """GitHub Actions에서만: 썸네일을 커밋·푸시하고 Pages 주소가 열릴 때까지 기다린다."""
    import subprocess
    if not os.environ.get("GITHUB_ACTIONS") or not post.get("thumb_rel"):
        return
    root = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=HERE, capture_output=True, text=True).stdout.strip()
    run = lambda *a: subprocess.run(list(a), cwd=root, check=True)
    run("git", "config", "user.name", "maeil-ai-bot")
    run("git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com")
    run("git", "add", os.path.join(HERE, post["thumb_rel"]))
    run("git", "commit", "-m", "thumb " + post["thumb_rel"])
    run("git", "pull", "--rebase")
    run("git", "push")
    for i in range(30):  # 최대 약 5분
        try:
            urllib.request.urlopen(urllib.request.Request(post["thumb_url"], method="HEAD"), timeout=15)
            log("썸네일 주소 열림")
            return
        except Exception:
            time.sleep(10)
    log("썸네일 주소가 아직 안 열렸지만 발행은 진행해요(곧 열림)")


def publish(post):
    push_thumb(post)
    token = access_token()
    slug = slugify(post["slug"])
    draft = api("POST", "posts/", token, {"kind": "blogger#post", "title": slug, "content": post["content"], "labels": post["labels"]},
                {"isDraft": "true"})
    api("POST", f"posts/{draft['id']}/publish", token)
    res = api("PATCH", f"posts/{draft['id']}", token, {"title": post["title"]})
    return res.get("url", "")


# ------------------------------------------------------------ 미리보기
def preview(post, name):
    os.makedirs(DATA, exist_ok=True)
    p = os.path.join(DATA, f"preview-{name}.html")
    page = ("<!doctype html><html lang=ko><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
            "<title>매일 AI 미리보기</title><style>body{margin:0;background:#f6f5f0;font-family:'Malgun Gothic',sans-serif;color:#111;line-height:1.85}"
            ".w{max-width:760px;margin:0 auto;padding:16px}.c{background:#fff;border:1px solid #e6e3d8;border-radius:14px;padding:28px}"
            "h2{font-size:22px;margin:34px 0 12px;padding:6px 12px;border-left:6px solid #ffd400;background:#fffbe0}"
            "blockquote{margin:16px 0;padding:12px 16px;background:#fff3a6;border-radius:8px}pre{background:#f3f2ec;padding:12px;overflow-x:auto}"
            "a{color:#2d6cdf}</style></head><body><div class=w><div class=c>"
            f"<small>{html.escape(' · '.join(post['labels']))} · 본문 {text_len(post['content']):,}자</small>"
            f"<h1>{html.escape(post['title'])}</h1>{post['content']}</div></div></body></html>")
    with open(p, "w", encoding="utf-8") as f:
        f.write(page)
    return p


def log_row(row):
    new = not os.path.exists(LOG)
    with open(LOG, "a", encoding="utf-8-sig") as f:
        if new:
            f.write("발행시각,종류,제목,URL\n")
        f.write(",".join('"' + str(x).replace('"', "'") + '"' for x in row) + "\n")


# ------------------------------------------------------------ 실행
def make_news(slot, done, now):
    log("속보 자료 수집")
    items = collect_news(slot, done)
    if len(items) < 4:
        sys.exit(f"새 소식이 {len(items)}건뿐이라 이번엔 건너뛰어요.")
    for it in items[:16]:
        it["text"] = article_text(it["link"])
    items = items[:16]
    log(f"Gemini 작성 중 (후보 {len(items)}건)")
    for attempt in range(2):
        d = write_news(items, slot, now)
        content, used = build_news_html(d, items, now)
        if text_len(content) >= MIN_CHARS:
            break
        log(f"  {text_len(content)}자 → 짧아서 다시")
    tags = [t for t in d.get("tags", []) if isinstance(t, str)][:4]
    post = {"title": d["title"], "slug": d.get("slug", "ai news"), "content": content,
            "labels": list(dict.fromkeys(["속보", "AI뉴스"] + tags))}
    add_thumb(post, "news", now, slot=slot)
    marks = [f"url:{items[i]['link']}" for i in used] + [f"t:{norm_title(items[i]['title'])}" for i in used]
    return post, marks


def make_tool(done, now):
    log("GitHub 급상승 툴 수집")
    cands = collect_tools(done)
    if not cands:
        sys.exit("새 툴 후보가 없어요.")
    for repo in cands[:5]:
        rd = readme(repo["full_name"])
        if len(rd) < 800:
            log(f"  {repo['full_name']} README 부족 → 다음")
            done[f"repo:{repo['full_name']}"] = now.strftime("%Y-%m-%d")
            continue
        log(f"Gemini 작성 중: {repo['full_name']} ★{repo['stargazers_count']}")
        for attempt in range(2):
            d = write_tool(repo, rd)
            content = build_tool_html(d, repo, now)
            if text_len(content) >= MIN_CHARS:
                break
            log(f"  {text_len(content)}자 → 짧아서 다시")
        topic = next((t for t in repo.get("topics", []) if t in GH_TOPICS and t != "ai"), "")
        labels = ["툴", "GitHub", "오픈소스"] + ([{"llm": "LLM", "ai-agents": "AI에이전트", "mcp": "MCP", "claude": "클로드", "generative-ai": "생성형AI"}[topic]] if topic else [])
        post = {"title": d["title"], "slug": d.get("slug", repo["name"] + " ai tool"), "content": content, "labels": labels}
        add_thumb(post, "tool", now, repo=repo)
        return post, [f"repo:{repo['full_name']}"]
    sys.exit("README가 충분한 툴이 없어요.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("kind", choices=["news", "tool", "auth"])
    ap.add_argument("--slot", choices=["am", "pm"], default="am")
    ap.add_argument("--preview", action="store_true")
    a = ap.parse_args()
    if a.kind == "auth":
        return auth()
    now = datetime.now(KST)
    done = load_done()
    post, marks = make_news(a.slot, done, now) if a.kind == "news" else make_tool(done, now)
    log(f"제목: {post['title']}  ({text_len(post['content']):,}자, 라벨 {post['labels']})")
    if a.preview:
        p = preview(post, a.kind + ("-" + a.slot if a.kind == "news" else ""))
        log("미리보기: " + p)
        return
    url = publish(post)
    for m in marks:
        done[m] = now.strftime("%Y-%m-%d")
    save_done(done)
    log_row([f"{now:%Y-%m-%d %H:%M}", a.kind, post["title"], url])
    log("발행 완료: " + url)


if __name__ == "__main__":
    main()
