# -*- coding: utf-8 -*-
"""매일 AI 썸네일 (1200x630) - 속보: 노랑 / 툴: 검정. 글꼴은 Pretendard(OFL)."""
import os
import re
from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
FONT = {w: os.path.join(HERE, "fonts", f"Pretendard-{w}.otf") for w in ("Black", "Bold", "SemiBold")}
W, H = 1200, 630
Y, K, WH, GR = (255, 212, 0), (17, 17, 17), (255, 255, 255), (150, 150, 150)
THEME = {
    "news": {"bg": Y, "fg": K, "pill_bg": K, "pill_fg": Y, "label": "오늘의 AI 속보", "sub": K, "robot": K, "robot_face": Y},
    "tool": {"bg": K, "fg": Y, "pill_bg": Y, "pill_fg": K, "label": "급상승 AI 툴", "sub": GR, "robot": Y, "robot_face": K},
    "guide": {"bg": WH, "fg": K, "pill_bg": Y, "pill_fg": K, "label": "AI 가이드", "sub": (90, 90, 90), "robot": K, "robot_face": Y},
    "auto": {"bg": (34, 34, 34), "fg": WH, "pill_bg": WH, "pill_fg": K, "label": "자동화 실전", "sub": GR, "robot": WH, "robot_face": Y},
    "res": {"bg": (246, 241, 222), "fg": K, "pill_bg": K, "pill_fg": WH, "label": "자료실", "sub": (110, 100, 70), "robot": K, "robot_face": WH},
}


def book(d, x, y, s, color):
    d.rounded_rectangle([x, y + s * .12, x + s * .47, y + s * .9], radius=3, fill=color)
    d.rounded_rectangle([x + s * .53, y + s * .12, x + s, y + s * .9], radius=3, fill=color)


def flow(d, x, y, s, color):
    r = s * .16
    pts = [(x + r, y + s * .2), (x + s - r, y + s * .5), (x + r, y + s * .8)]
    d.line(pts, fill=color, width=max(3, int(s * .1)))
    for px, py in pts:
        d.ellipse([px - r, py - r, px + r, py + r], fill=color)


def folder(d, x, y, s, color):
    d.polygon([(x, y + s * .15), (x + s * .4, y + s * .15), (x + s * .5, y + s * .28), (x + s, y + s * .28),
               (x + s, y + s * .88), (x, y + s * .88)], fill=color)


def font(w, size):
    return ImageFont.truetype(FONT[w], size)


def wrap(draw, text, f, max_w):
    """한글은 글자 단위, 공백 우선으로 줄바꿈."""
    lines, cur = [], ""
    for word in text.split(" "):
        test = (cur + " " + word).strip()
        if draw.textlength(test, font=f) <= max_w:
            cur = test
            continue
        if cur:
            lines.append(cur)
        cur = ""
        for ch in word:  # 한 단어가 너무 길면 글자 단위로
            if draw.textlength(cur + ch, font=f) <= max_w:
                cur += ch
            else:
                lines.append(cur)
                cur = ch
    if cur:
        lines.append(cur)
    return lines


def fit_title(draw, text, max_w, max_h, max_lines=3):
    for size in range(88, 40, -4):
        f = font("Black", size)
        lines = wrap(draw, text, f, max_w)
        lh = int(size * 1.22)
        if len(lines) <= max_lines and lh * len(lines) <= max_h:
            return f, lines, lh
    f = font("Black", 44)
    lines = wrap(draw, text, f, max_w)[:max_lines]
    lines[-1] = lines[-1][:-1] + "…"
    return f, lines, int(44 * 1.22)


def bolt(d, x, y, s, color):
    d.polygon([(x + s * .55, y), (x + s * .1, y + s * .58), (x + s * .45, y + s * .58), (x + s * .3, y + s),
               (x + s * .9, y + s * .38), (x + s * .52, y + s * .38), (x + s * .75, y)], fill=color)


def star(d, cx, cy, r, color):
    import math
    pts = []
    for i in range(10):
        rr = r if i % 2 == 0 else r * .45
        a = math.pi / 2 + i * math.pi / 5
        pts.append((cx + rr * math.cos(a), cy - rr * math.sin(a)))
    d.polygon(pts, fill=color)


def robot(d, x, y, s, body, face):
    """매일이: 네모 머리 + 안테나 + 눈 + 웃는 입"""
    d.line([(x + s * .5, y), (x + s * .5, y + s * .18)], fill=body, width=max(4, int(s * .05)))
    d.ellipse([x + s * .42, y - s * .08, x + s * .58, y + s * .08], fill=body)
    d.rounded_rectangle([x, y + s * .16, x + s, y + s * .96], radius=int(s * .2), fill=body)
    d.rounded_rectangle([x - s * .08, y + s * .42, x + s * .04, y + s * .7], radius=int(s * .05), fill=body)
    d.rounded_rectangle([x + s * .96, y + s * .42, x + s * 1.08, y + s * .7], radius=int(s * .05), fill=body)
    d.rounded_rectangle([x + s * .12, y + s * .3, x + s * .88, y + s * .82], radius=int(s * .14), fill=face)
    for ex in (.32, .68):
        d.ellipse([x + s * (ex - .08), y + s * .42, x + s * (ex + .08), y + s * .58], fill=body)
    d.arc([x + s * .34, y + s * .5, x + s * .66, y + s * .74], 20, 160, fill=body, width=max(4, int(s * .05)))


def make(kind, title, out, date="", repo="", stars=None):
    t = THEME[kind]
    im = Image.new("RGB", (W, H), t["bg"])
    d = ImageDraw.Draw(im)
    pad = 64

    # 왼쪽 위 카테고리 알약
    pf = font("Bold", 30)
    label_w = d.textlength(t["label"], font=pf)
    icon = 34
    pw = int(label_w + icon + 70)
    d.rounded_rectangle([pad, pad, pad + pw, pad + 62], radius=31, fill=t["pill_bg"])
    if kind == "news":
        bolt(d, pad + 22, pad + 14, icon, t["pill_fg"])
    elif kind == "tool":
        star(d, pad + 22 + icon / 2, pad + 31, icon / 2, t["pill_fg"])
    else:
        {"guide": book, "auto": flow, "res": folder}[kind](d, pad + 22, pad + 14, icon, t["pill_fg"])
    d.text((pad + 22 + icon + 12, pad + 31), t["label"], font=pf, fill=t["pill_fg"], anchor="lm")
    if date:
        d.text((pad + pw + 20, pad + 31), date, font=font("SemiBold", 28), fill=t["sub"], anchor="lm")

    # 제목 (속보는 앞의 'N월 N일 AI 속보:'를 떼고 핵심만)
    if kind == "news":
        title = re.sub(r"^\s*\d+월\s*\d+일\s*(아침|오후)?\s*AI\s*속보\s*[:：|\-]?\s*", "", title) or title
    max_w = W - pad * 2 - 230
    box_h = 240 if kind == "tool" else 300
    f, lines, lh = fit_title(d, title, max_w, box_h)
    top = pad + 62 + 40 + (box_h - lh * len(lines)) // 2
    for i, line in enumerate(lines):
        d.text((pad, top + i * lh), line, font=f, fill=t["fg"])

    # 툴: 저장소 + 별
    if kind == "tool" and repo:
        y0 = H - pad - 110
        d.text((pad, y0), repo, font=font("SemiBold", 28), fill=GR)
        if stars is not None:
            s_txt = f"{stars:,}"
            sx = pad + d.textlength(repo, font=font("SemiBold", 28)) + 24
            sw = d.textlength(s_txt, font=font("Bold", 28)) + 66
            d.rounded_rectangle([sx, y0 - 6, sx + sw, y0 + 38], radius=22, outline=Y, width=3)
            star(d, sx + 24, y0 + 16, 13, Y)
            d.text((sx + 44, y0 + 16), s_txt, font=font("Bold", 28), fill=Y, anchor="lm")

    # 하단 브랜드
    d.line([(pad, H - pad - 52), (W - pad, H - pad - 52)], fill=(60, 60, 60) if kind in ("tool", "auto") else t["fg"], width=3)
    d.text((pad, H - pad - 4), "매일 AI", font=font("Black", 34), fill=t["fg"], anchor="lb")
    d.text((pad + 150, H - pad - 4), "속보 · 툴 · 자동화   ai.free-m.club", font=font("SemiBold", 26), fill=t["sub"], anchor="lb")

    # 오른쪽 매일이
    robot(d, W - pad - 190, 150, 180, t["robot"], t["robot_face"])

    os.makedirs(os.path.dirname(out), exist_ok=True)
    im.save(out, "JPEG", quality=88, optimize=True)
    return out


if __name__ == "__main__":
    o = os.path.join(HERE, "data")
    make("news", "11월 21일 AI 속보: 167년 수학 난제 해결 주장과 앤스로픽의 불안한 상장", os.path.join(o, "thumb-news.jpg"), date="11.21 아침")
    make("tool", "dsh-Go: DeepSeek를 내 주머니 속에! 모바일 AI 에이전트 리뷰", os.path.join(o, "thumb-tool.jpg"), repo="ccch1mneyyy/dsh-Go", stars=29110)
    make("guide", "챗GPT로 회의록 5분 만에 정리하는 법", os.path.join(o, "thumb-guide.jpg"))
    make("auto", "새 메일 오면 AI가 요약해서 슬랙으로 보내기 (n8n)", os.path.join(o, "thumb-auto.jpg"))
    make("res", "바로 복사해 쓰는 업무 이메일 프롬프트 12개", os.path.join(o, "thumb-res.jpg"))
    print("ok")
