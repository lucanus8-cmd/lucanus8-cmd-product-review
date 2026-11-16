"""약학정보원(health.kr) 질환정보 화면을 로봇이 받아도 되는지, 어떻게 생겼는지 본다.

1) robots.txt 에서 /drug-info 쪽을 막아 두었는지
2) 이용약관에서 자동 수집·복제에 관한 문장(앞부분만)
3) 질환정보 화면의 구조: 목록(질환 수), 링크 모양, 로그인 요구 여부, 내려받기 단추
본문은 적지 않고 구조와 건수만 적는다. 읽기만 한다.
"""
import os
import re
import sys
import time

import requests
from playwright.sync_api import sync_playwright

BASE = "https://www.health.kr"
START = os.environ.get("URLS") or BASE + "/drug-info/disease"
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"}


def robots():
    try:
        r = requests.get(BASE + "/robots.txt", headers=UA, timeout=30)
        print(f"robots.txt: HTTP {r.status_code}")
        for l in r.text.splitlines()[:200]:
            if l.strip():
                print("   " + l.strip()[:150])
    except Exception as e:
        print("robots.txt 못 받음:", str(e)[:150])


def terms(pg):
    words = ("수집", "크롤", "복제", "무단", "저작권", "자동", "로봇", "전재", "배포")
    for a in pg.query_selector_all("a"):
        t = (a.inner_text() or "").strip()
        if "이용약관" in t or "저작권" in t:
            h = a.get_attribute("href") or ""
            print(f"약관 링크: {t} → {h[:120]}")
    for u in (BASE + "/notice/terms",):
        try:
            pg.goto(u, wait_until="domcontentloaded", timeout=60_000)
            time.sleep(3)
            lines = [l.strip() for l in pg.inner_text("body").splitlines() if l.strip()]
            hits = [l for l in lines if any(w in l for w in words)]
            print(f"\n{u}: {pg.title()} · 관련 문장 {len(hits)}개")
            for l in hits[:25]:
                print("   " + l[:200])
        except Exception as e:
            print(f"{u}: 실패 {str(e)[:100]}")


def structure(pg):
    pg.goto(START, wait_until="domcontentloaded", timeout=90_000)
    time.sleep(6)
    txt = pg.inner_text("body")
    print(f"\n===== {START}\n제목: {pg.title()} · 글자 {len(txt):,}자 · 최종 주소 {pg.url[:120]}")
    print("로그인 요구 문구:", [w for w in ("로그인", "회원", "약사 인증") if w in txt][:3])
    m = re.findall(r"총\s*([\d,]+)\s*(?:건|개)", txt)
    print("건수 표시:", m[:5])
    links = {}
    for a in pg.query_selector_all("a"):
        h = a.get_attribute("href") or ""
        t = " ".join((a.inner_text() or "").split())
        if "disease" in h or "Disease" in h:
            links.setdefault(re.sub(r"\d+", "N", h)[:80], []).append((t[:30], h[:120]))
    print("질환 관련 링크 모양:")
    for k, v in sorted(links.items(), key=lambda x: -len(x[1]))[:10]:
        print(f"   {k}  ×{len(v)}  예: {v[0]}")
    btn = sorted(set(re.findall(r"(PDF|다운로드|내려받기|인쇄|첨부)", txt)))
    print("단추 글자:", btn)
    # 첫 질환 하나 열어서 모양 보기
    for first in [BASE + "/drug-info/disease/I10", BASE + "/drug-info/disease/E11"]:
        u = first
        pg.goto(u, wait_until="domcontentloaded", timeout=90_000)
        time.sleep(5)
        t2 = pg.inner_text("body")
        print(f"\n----- 질환 하나: {pg.title()} · 글자 {len(t2):,}자")
        heads = [h.inner_text().strip()[:40] for h in pg.query_selector_all("h1,h2,h3,h4,h5,dt,th,strong")][:40]
        print("   제목·항목:", " | ".join(heads))
        body = t2
        for w in ("치료", "약물", "1차", "병용", "권고", "가이드라인", "처방", "성분"):
            print(f"   '{w}' {body.count(w)}번")
        # 본문 길이만(내용은 적지 않는다): 메뉴를 뺀 가운데 부분
        main = pg.query_selector("main, #content, .content, .contents, article")
        print("   본문 글자 수:", len(main.inner_text()) if main else "본문 영역 못 찾음")
        print("   로그인 요구 문구:", [w for w in ("로그인", "회원", "약사 인증") if w in t2][:3])
        files = [(a.inner_text() or "").strip()[:40] + " → " + (a.get_attribute("href") or "")[:80]
                 for a in pg.query_selector_all("a") if re.search(r"\.pdf|download|fileDown", a.get_attribute("href") or "", re.I)]
        print("   파일 링크:", files[:5])


def main():
    robots()
    with sync_playwright() as p:
        br = p.chromium.launch(executable_path=os.environ.get("CHROMIUM_PATH") or None)
        pg = br.new_context(viewport={"width": 1400, "height": 2400}, locale="ko-KR", user_agent=UA["User-Agent"]).new_page()
        try:
            structure(pg)
        except Exception as e:
            print("구조 보기 실패:", str(e)[:200])
        try:
            terms(pg)
        except Exception as e:
            print("약관 보기 실패:", str(e)[:200])
        br.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
