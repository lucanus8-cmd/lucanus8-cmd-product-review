"""식약처 의약품안전나라 '임상시험정보검색'에서 최근 생동성시험(또는 다른 단계)을 받아,
제품명이 개발코드인 것 중 주제어(예: 심혈관·지질)가 제목에 들어간 것을 적는다.

공개 자료(임상시험 승인 정보)만 다룬다. 매출 자료와는 상관없다.
    STEP=생동 MONTHS=24 WORDS=고지혈,이상지질 FOCUS=벰페도,bempedo python .github/scripts/check_nedrug_clinic.py
"""
import os
import re
import sys
import time
from datetime import date, timedelta
from urllib.parse import urlencode

from playwright.sync_api import sync_playwright

BASE = "https://nedrug.mfds.go.kr"
STEP = os.environ.get("STEP") or "생동"
MONTHS = int(os.environ.get("MONTHS") or "24")
WORDS = [w.strip() for w in (os.environ.get("WORDS") or
         "고지혈,이상지질,지질,콜레스테롤,LDL,심혈관,동맥경화,관상동맥,고혈압,혈압,심부전,협심,심근,뇌졸중,혈전,"
         "스타틴,statin,에제티미브,ezetimibe").split(",") if w.strip()]
FOCUS = [w.strip() for w in (os.environ.get("FOCUS") or "벰페도,bempedo,넥스리").split(",") if w.strip()]
CODE = re.compile(r"[A-Za-z]{1,8}[-_ ]?\d{2,}")
HANGUL = re.compile(r"[가-힣]")


def is_code(name):
    n = (name or "").strip()
    if not n:
        return False
    if CODE.search(n) and not re.search(r"\([가-힣]{2,}", n):   # 'OO정(성분명)' 꼴은 성분이 있는 것
        return True
    return not HANGUL.search(n) and bool(re.search(r"\d", n))


def step_values(pg):
    """단계 체크칸의 (값, 글자) 목록."""
    pg.goto(BASE + "/PPBAB13", wait_until="domcontentloaded", timeout=120_000)
    time.sleep(5)
    out = []
    for el in pg.query_selector_all("input[name='clinicStepCode']"):
        v = el.get_attribute("value") or ""
        i = el.get_attribute("id") or ""
        lab = ""
        if i:
            l = pg.query_selector(f"label[for='{i}']")
            lab = (l.inner_text() if l else "").strip()
        out.append((v, lab))
    return out


def rows_of(pg):
    tb = pg.query_selector("table")
    if not tb:
        return [], []
    hdr = [" ".join(th.inner_text().split()) for th in tb.query_selector_all("thead th")]
    rows = []
    for tr in tb.query_selector_all("tbody tr"):
        tds = tr.query_selector_all("td")
        if len(tds) < 5:
            continue
        cells = [" ".join(td.inner_text().split()) for td in tds]
        # 좁은 화면용으로 칸 이름이 앞에 붙어 있으면 뗀다
        cells = [c[len(h):].strip() if h and c.startswith(h) else c for c, h in zip(cells, hdr + [""] * 20)]
        rows.append(cells)
    return hdr, rows


def main():
    end = date.today()
    start = end - timedelta(days=int(MONTHS * 30.5))
    with sync_playwright() as p:
        br = p.chromium.launch(executable_path=os.environ.get("CHROMIUM_PATH") or None)
        pg = br.new_context(viewport={"width": 1500, "height": 2600}, locale="ko-KR").new_page()
        steps = step_values(pg)
        print("단계 체크칸:", " · ".join(f"{l}={v}" for v, l in steps))
        want = [v for v, l in steps if STEP in l]
        if not want:
            print(f"'{STEP}' 단계를 못 찾음")
            br.close()
            return 1
        hdr, allrows, page = [], [], 1
        while page <= 80:
            q = [("page", page), ("searchYn", "true"), ("approvalDtStart", start.isoformat()),
                 ("approvalDtEnd", end.isoformat())] + [("clinicStepCode", v) for v in want]
            pg.goto(BASE + "/searchClinic?" + urlencode(q), wait_until="domcontentloaded", timeout=120_000)
            time.sleep(2.5)
            if page == 1:
                m = re.search(r"총\s*([\d,]+)\s*건", pg.inner_text("body"))
                print(f"{start} ~ {end} · 단계 {STEP} · 화면 총 {m.group(1) if m else '?'}건")
            h, r = rows_of(pg)
            if h:
                hdr = h
            r = [x for x in r if "조회 결과가 없습니다" not in " ".join(x)]
            if not r:
                break
            allrows.extend(r)
            page += 1
        print(f"받은 줄 {len(allrows)} · 칸: {' | '.join(hdr)}")
        br.close()

    def col(name):
        return next((i for i, h in enumerate(hdr) if name in h), None)
    ip, it, ia, isp = col("제품명"), col("제목"), col("승인일"), col("의뢰자")
    if ip is None or it is None:
        print("칸을 못 찾음")
        return 1
    code = [r for r in allrows if is_code(r[ip])]
    print(f"\n제품명이 개발코드인 것: {len(code)} / {len(allrows)} ({len(code) / max(1, len(allrows)) * 100:.0f}%)")

    focus = [r for r in allrows if any(w.lower() in (r[ip] + " " + r[it]).lower() for w in FOCUS)]
    print(f"\n[1] 제품명·제목에 {', '.join(FOCUS)} 가 들어간 것 — {len(focus)}건")
    for r in focus:
        print(f"   {r[ia] if ia is not None else ''} | {r[isp] if isp is not None else ''} | {r[ip]} | {r[it][:110]}")

    hit = [r for r in code if any(w.lower() in r[it].lower() for w in WORDS)]
    print(f"\n[2] 개발코드 + 제목에 심혈관·지질 주제어 — {len(hit)}건")
    for r in sorted(hit, key=lambda x: x[ia] if ia is not None else "", reverse=True):
        print(f"   {r[ia] if ia is not None else ''} | {r[isp] if isp is not None else ''} | {r[ip]} | {r[it][:110]}")

    rest = [r for r in code if r not in hit]
    print(f"\n[3] 개발코드인데 주제어가 제목에 없는 것 — {len(rest)}건 (제목 앞부분만)")
    for r in sorted(rest, key=lambda x: x[ia] if ia is not None else "", reverse=True)[:60]:
        print(f"   {r[ia] if ia is not None else ''} | {r[isp] if isp is not None else ''} | {r[ip]} | {r[it][:80]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
