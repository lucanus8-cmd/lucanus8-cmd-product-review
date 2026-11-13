"""배포된 사이트의 특허 화면에서 성분 하나를 찾아 그대로 적는다.

'특허 분석' 화면에서 상태 기본값('등록')을 지워 모든 상태를 보이게 하고,
품목/성분 검색에 성분명을 넣어 나온 표를 전부 읽는다(표는 보이는 줄만
그려지므로 스크롤하며 모은다). 읽기만 하므로 사이트는 바뀌지 않는다.

특허 자료는 식약처 공개 자료라 행을 그대로 적어도 된다.

    QUERY=미라베그론 python .github/scripts/check_patent.py
"""
import os
import re
import sys
import time

from playwright.sync_api import sync_playwright

URL = (os.environ.get("APP_URL") or "").rstrip("/")
PW = os.environ.get("APP_PASSWORD") or ""
QUERY = (os.environ.get("QUERY") or "미라베그론").strip()

GRID_JS = """
() => {
  const d = document.querySelector("[data-testid='stDataFrame']");
  if (!d) return null;
  const hdr = [...d.querySelectorAll("[role='columnheader']")].map(e => e.innerText.trim());
  const rows = [...d.querySelectorAll("[role='row']")].map(r =>
    [...r.querySelectorAll("[role='gridcell']")].map(c => c.innerText.trim()))
    .filter(r => r.length);
  return {hdr, rows};
}
"""
SCROLL_JS = """
(y) => {
  const d = document.querySelector("[data-testid='stDataFrame']");
  const s = d && (d.querySelector('.dvn-scroller') || d.querySelector('[class*="scroller"]'));
  if (!s) return -1;
  s.scrollTop = y;
  return s.scrollHeight;
}
"""


APP_SEL = ("[data-testid='stAppViewContainer']", "[data-testid='stApp']",
           "input[type='password']")


def app(pg):
    """앱이 그려진 틀. 실제 사이트는 앱을 iframe 안에 띄우므로 바깥 페이지만
    읽으면 빈 화면으로 보인다. 틀은 다시 그려질 수 있어 매번 새로 찾는다."""
    for fr in pg.frames:
        for sel in APP_SEL:
            try:
                if fr.query_selector(sel):
                    return fr
            except Exception:
                break
    return pg.main_frame


def body_text(pg):
    try:
        return app(pg).inner_text("body")
    except Exception:
        return ""


def wait_for(pg, word, tries=96, gap=5):
    for i in range(tries):
        if word in body_text(pg):
            return True
        if PW:
            box = app(pg).query_selector("input[type='password']")
            if box:
                box.fill(PW)
                box.press("Enter")
                time.sleep(14)
                continue
        time.sleep(gap)
    return False


def read_all_rows(pg):
    """보이는 줄만 그려지는 표를 스크롤하며 전부 모은다."""
    first = app(pg).evaluate(GRID_JS)
    if not first:
        return [], []
    hdr = first["hdr"]
    seen, rows = set(), []
    y, last_h = 0, None
    for _ in range(400):
        g = app(pg).evaluate(GRID_JS) or {"rows": []}
        for r in g["rows"]:
            t = tuple(r)
            if t not in seen:
                seen.add(t)
                rows.append(r)
        h = app(pg).evaluate(SCROLL_JS, y + 200)
        if h == -1:
            break
        time.sleep(0.4)
        if y > (h or 0) + 400:
            break
        y += 200
        last_h = h
    return hdr, rows


def main():
    if not URL:
        print("APP_URL 이 없다 — 판정 불가")
        return 1
    with sync_playwright() as p:
        # 로컬에서 시험할 때만 CHROMIUM_PATH 로 브라우저 위치를 준다
        br = p.chromium.launch(executable_path=os.environ.get("CHROMIUM_PATH") or None)
        pg = br.new_context(viewport={"width": 1600, "height": 2000},
                            locale="ko-KR").new_page()
        pg.goto(URL + "/?page=patent", wait_until="domcontentloaded", timeout=180_000)
        if not wait_for(pg, "특허 분석"):
            print("특허 분석 화면이 안 떴다")
            print("   화면 앞부분:", body_text(pg)[:400].replace("\n", " | "))
            br.close()
            return 1
        time.sleep(8)

        # 상태 칸 기본값('등록')을 지운다
        stat = app(pg).locator("[data-testid='stMultiSelect']").filter(has_text="상태").first
        clr = stat.locator("[aria-label*='Clear'], [title*='Clear']")
        if clr.count():
            clr.first.click()
        else:
            x = stat.locator("[data-baseweb='tag'] svg")
            if x.count():
                x.first.click()
        time.sleep(6)
        st_txt = stat.inner_text().replace("\n", " | ")
        print(f"상태 칸: {st_txt}")

        box = app(pg).locator("[data-testid='stTextInput']").filter(
            has_text="품목/성분 검색").locator("input").first
        box.fill(QUERY)
        box.press("Enter")
        time.sleep(4)
        for _ in range(36):
            if "특허 건수" in body_text(pg) or "조건에 맞는 특허 없음" in body_text(pg):
                break
            time.sleep(5)
        time.sleep(4)

        ex = app(pg).query_selector_all("[data-testid='stException']")
        if ex:
            print("화면에 예외:", (ex[0].inner_text() or "")[:400])
            br.close()
            return 1
        body = body_text(pg)
        if "조건에 맞는 특허 없음" in body:
            print(f"'{QUERY}' — 조건에 맞는 특허 없음 (모든 상태로 검색)")
            br.close()
            return 0

        mets = [e.inner_text().replace("\n", " ") for e in
                app(pg).query_selector_all("[data-testid='stMetric']")]
        print(f"'{QUERY}' 검색 · 모든 상태 · " + " // ".join(mets))

        # 만료 연도 범위 슬라이더는 기본이 전체 구간이라 손대지 않는다
        hdr, rows = read_all_rows(pg)
        print(f"\n표 {len(rows)}줄 · 칸: {' | '.join(hdr)}")
        for r in rows:
            print("   " + " | ".join(r))

        br.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
