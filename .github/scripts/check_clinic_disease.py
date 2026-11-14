"""배포된 사이트의 '임상 분석 → 검색' 화면에서 성분마다 임상을 찾아
대상질환·대상질환(세부)·단계가 어떻게 적혀 있는지 센다.

성분 → 질환 대분류를 '그 성분 이름이 적힌 임상'에서 찾을 수 있는지 보는 점검이다.
임상 자료는 식약처 공개 자료라 적어도 된다. 이 화면에는 매출이 없다. 읽기만 한다.

    QUERY=피타바스타틴,루라시돈 python .github/scripts/check_clinic_disease.py
"""
import os
import sys
import time
from collections import Counter

from playwright.sync_api import sync_playwright

URL = (os.environ.get("APP_URL") or "").rstrip("/")
PW = os.environ.get("APP_PASSWORD") or ""
QUERIES = [q.strip() for q in (os.environ.get("QUERY") or "피타바스타틴").split(",") if q.strip()]
APP_SEL = ("[data-testid='stAppViewContainer']", "[data-testid='stApp']", "input[type='password']")

GRID_JS = """
() => {
  const ds = [...document.querySelectorAll("[data-testid='stDataFrame']")];
  const d = ds.find(x => [...x.querySelectorAll("[role='columnheader']")].some(h => h.innerText.includes('대상질환')));
  if (!d) return null;
  const hdr = [...d.querySelectorAll("[role='columnheader']")].map(e => e.innerText.trim());
  const rows = [...d.querySelectorAll("[role='row']")].map(r =>
    [...r.querySelectorAll("[role='gridcell']")].map(c => c.innerText.trim())).filter(r => r.length);
  return {hdr, rows};
}
"""
SCROLL_JS = """
(y) => {
  const ds = [...document.querySelectorAll("[data-testid='stDataFrame']")];
  const d = ds.find(x => [...x.querySelectorAll("[role='columnheader']")].some(h => h.innerText.includes('대상질환')));
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


def read_rows(pg):
    first = app(pg).evaluate(GRID_JS)
    if not first:
        return [], []
    seen, rows, y = set(), [], 0
    for _ in range(300):
        g = app(pg).evaluate(GRID_JS) or {"rows": []}
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
    return first["hdr"], rows


def main():
    if not URL:
        print("APP_URL 이 없다")
        return 1
    with sync_playwright() as p:
        br = p.chromium.launch(executable_path=os.environ.get("CHROMIUM_PATH") or None)
        pg = br.new_context(viewport={"width": 1700, "height": 2400}, locale="ko-KR").new_page()
        pg.goto(URL + "/?page=clinical", wait_until="domcontentloaded", timeout=180_000)
        if not wait_for(pg, "성분/제품 검색"):
            print("임상 검색 화면이 안 떴다")
            br.close()
            return 1
        settle(pg)
        for q in QUERIES:
            try:
                box = app(pg).locator("[data-testid='stTextInput']").filter(has_text="성분/제품 검색").locator("input").first
                box.fill(q)
                box.press("Enter")
                time.sleep(3)
                settle(pg, cap=180)
                hdr, rows = read_rows(pg)
                print(f"\n===== {q} · 표 {len(rows)}줄 · 칸: {' | '.join(hdr)}")
                if not rows:
                    continue

                def col(name, exact=False):
                    for i, h in enumerate(hdr):
                        if (h == name) if exact else (name in h):
                            return i
                    return None
                idis, idtl, istep, iing = col("대상질환", True), col("대상질환(세부)"), col("단계"), col("성분명")
                for label, i in (("대상질환", idis), ("대상질환(세부)", idtl), ("단계", istep)):
                    if i is None:
                        continue
                    c = Counter((r[i] if i < len(r) else "") or "(빈칸)" for r in rows)
                    top = " · ".join(f"{k[:40]} {n}" for k, n in c.most_common(8))
                    print(f"   {label}: {top}")
                if istep is not None and idis is not None:
                    named = [r for r in rows if "생동" not in (r[istep] if istep < len(r) else "")]
                    c = Counter((r[idis] or "(빈칸)") for r in named)
                    print(f"   생동 뺀 {len(named)}건의 대상질환: " + " · ".join(f"{k[:40]} {n}" for k, n in c.most_common(6)))
                    bio = [r for r in rows if "생동" in (r[istep] if istep < len(r) else "")]
                    c = Counter((r[idis] or "(빈칸)") for r in bio)
                    print(f"   생동 {len(bio)}건의 대상질환: " + " · ".join(f"{k[:40]} {n}" for k, n in c.most_common(6)))
                if iing is not None:
                    c = Counter((r[iing] or "(빈칸)") for r in rows)
                    print("   성분명 표기: " + " · ".join(f"{k[:30]} {n}" for k, n in c.most_common(5)))
            except Exception as e:
                print(f"'{q}' 실패: {str(e)[:200]}")
        br.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
