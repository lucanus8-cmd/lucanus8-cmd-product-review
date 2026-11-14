"""배포된 사이트의 종합분석 → DMF(동일성분) 탭에서 성분 하나를 찾아 표를 적는다.

DMF(원료의약품 등록)는 식약처 공개 자료라 행을 그대로 적어도 된다.
매출 자료는 읽지도 적지도 않는다. 읽기만 하므로 사이트는 바뀌지 않는다.

    QUERY=미라베그론,아토르바스타틴 python .github/scripts/check_dmf.py
"""
import os
import re
import sys
import time

from playwright.sync_api import sync_playwright

URL = (os.environ.get("APP_URL") or "").rstrip("/")
PW = os.environ.get("APP_PASSWORD") or ""
QUERIES = [q.strip() for q in (os.environ.get("QUERY") or "미라베그론").split(",") if q.strip()]
APP_SEL = ("[data-testid='stAppViewContainer']", "[data-testid='stApp']",
           "input[type='password']")

# DMF 탭 안의 표만 읽는다(다른 탭의 표도 화면에 그려져 있다)
PANEL_JS = """
() => {
  const ps = [...document.querySelectorAll("[role='tabpanel']")];
  const p = ps.find(x => (x.innerText || '').includes('동일성분 DMF'));
  if (!p) return null;
  const cap = ((p.innerText || '').split('\\n').find(l => l.includes('동일성분 DMF')) || '').trim();
  const d = p.querySelector("[data-testid='stDataFrame']");
  if (!d) return {cap, hdr: [], rows: []};
  const hdr = [...d.querySelectorAll("[role='columnheader']")].map(e => e.innerText.trim());
  const rows = [...d.querySelectorAll("[role='row']")].map(r =>
    [...r.querySelectorAll("[role='gridcell']")].map(c => c.innerText.trim())).filter(r => r.length);
  return {cap, hdr, rows};
}
"""
SCROLL_JS = """
(y) => {
  const ps = [...document.querySelectorAll("[role='tabpanel']")];
  const p = ps.find(x => (x.innerText || '').includes('동일성분 DMF'));
  const d = p && p.querySelector("[data-testid='stDataFrame']");
  const s = d && (d.querySelector('.dvn-scroller') || d.querySelector('[class*="scroller"]'));
  if (!s) return -1;
  s.scrollTop = y;
  return s.scrollHeight;
}
"""


def app(pg):
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
    for _ in range(tries):
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


def settle(pg, gap=1.0, need=4, cap=240):
    last, same, t0 = None, 0, time.time()
    while time.time() - t0 < cap:
        b = body_text(pg)
        busy = app(pg).query_selector("[data-testid='stStatusWidget']")
        same = same + 1 if (b == last and not busy) else 0
        last = b
        if same >= need:
            return
        time.sleep(gap)


def one(pg, q):
    pg.goto(URL + "/?page=search", wait_until="domcontentloaded", timeout=180_000)
    if not wait_for(pg, "검색 기준"):
        print("종합분석 화면이 안 떴다")
        return
    settle(pg)
    app(pg).locator("[data-testid='stRadio'] label").filter(has_text="주성분").first.click()
    settle(pg)
    box = app(pg).locator("[data-testid='stTextInput']").filter(has_text="검색어").locator("input").first
    box.fill(q)
    box.press("Enter")
    settle(pg)
    sb = app(pg).locator("[data-testid='stSelectbox']").filter(has_text="선택").first
    sb.click()
    time.sleep(1.5)
    picked = None
    opts = app(pg).query_selector_all("[role='option']")
    names = [(o.inner_text() or "").strip() for o in opts]
    # 정확히 같은 이름 → 단일 성분 → 첫 번째 순서로 고른다
    order = ([i for i, n in enumerate(names) if n == q]
             + [i for i, n in enumerate(names) if n and "선택하세요" not in n and "|" not in n and "/" not in n]
             + [i for i, n in enumerate(names) if n and "선택하세요" not in n])
    if order:
        picked = names[order[0]]
        opts[order[0]].click()
    else:
        pg.keyboard.press("Escape")
    print(f"\n===== '{q}' → 고른 성분: {picked}")
    if not picked:
        return
    settle(pg, cap=300)
    tab = app(pg).locator("[role='tab']").filter(has_text="DMF").first
    if not tab.count():
        print("DMF 탭이 없다")
        return
    tab.click()
    settle(pg)
    first = app(pg).evaluate(PANEL_JS)
    if not first:
        # 화면 글을 그대로 적으면 안 된다 — 종합분석 화면 위쪽에 매출(유료 자료)
        # 숫자가 있고, GitHub 로봇 기록은 공개다. 안내 문구가 있는지만 적는다.
        b = body_text(pg)
        why = next((w for w in ("DMF 데이터 미연결", "동일성분 DMF 매칭 없음") if w in b), "알 수 없음")
        print(f"DMF 탭에 표가 없다 — {why}")
        return
    print(first["cap"])
    seen, rows, y = set(), [], 0
    for _ in range(300):
        g = app(pg).evaluate(PANEL_JS) or {"rows": []}
        for r in g["rows"]:
            t = tuple(r)
            if t not in seen:
                seen.add(t)
                rows.append(r)
        h = app(pg).evaluate(SCROLL_JS, y + 200)
        if h == -1 or y > (h or 0) + 400:
            break
        y += 200
        time.sleep(0.3)
    print(f"표 {len(rows)}줄 · 칸: {' | '.join(first['hdr'])}")
    for r in rows[:80]:
        print("   " + " | ".join(x[:40] for x in r))
    if len(rows) > 80:
        print(f"   … 외 {len(rows) - 80}줄")


def main():
    if not URL:
        print("APP_URL 이 없다")
        return 1
    with sync_playwright() as p:
        br = p.chromium.launch(executable_path=os.environ.get("CHROMIUM_PATH") or None)
        pg = br.new_context(viewport={"width": 1600, "height": 2200}, locale="ko-KR").new_page()
        for q in QUERIES:
            try:
                one(pg, q)
            except Exception as e:
                print(f"'{q}' 실패: {str(e)[:200]}")
        br.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
