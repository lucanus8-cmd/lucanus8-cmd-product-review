"""배포된 사이트의 '제품 제안 → 🧪 원료사 동향(DMF)' 보기를 열어 오류 없이 뜨는지 본다.

로봇 기록은 공개라서 매출 칸(시장규모·CAGR)은 읽지도 적지도 않는다.
DMF·임상·특허는 식약처 공개 자료라 적어도 된다. 읽기만 하므로 사이트는 바뀌지 않는다.

    QUERY=데스벤라팍신 python .github/scripts/check_dmf_view.py
"""
import os
import sys
import time

from playwright.sync_api import sync_playwright

URL = (os.environ.get("APP_URL") or "").rstrip("/")
PW = os.environ.get("APP_PASSWORD") or ""
PICK = (os.environ.get("QUERY") or "").strip()
APP_SEL = ("[data-testid='stAppViewContainer']", "[data-testid='stApp']", "input[type='password']")
SALES_WORDS = ("시장규모", "CAGR", "매출")

GRIDS_JS = """
() => [...document.querySelectorAll("[data-testid='stDataFrame']")].map(d => ({
  hdr: [...d.querySelectorAll("[role='columnheader']")].map(e => e.innerText.trim()),
  rows: [...d.querySelectorAll("[role='row']")].map(r =>
    [...r.querySelectorAll("[role='gridcell']")].map(c => c.innerText.trim())).filter(r => r.length)
}))
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


def settle(pg, gap=1.0, need=5, cap=300):
    last, same, t0 = None, 0, time.time()
    while time.time() - t0 < cap:
        b = body_text(pg)
        busy = app(pg).query_selector("[data-testid='stStatusWidget']")
        same = same + 1 if (b == last and not busy) else 0
        last = b
        if same >= need:
            return
        time.sleep(gap)


def errors(pg):
    ex = app(pg).query_selector_all("[data-testid='stException']")
    return (ex[0].inner_text() or "")[:600] if ex else None


def safe_line(pg, word):
    """안내 줄 하나만. 매출 낱말이 든 줄은 적지 않는다."""
    for l in body_text(pg).splitlines():
        if word in l and not any(w in l for w in SALES_WORDS):
            return l.strip()[:300]
    return ""


def print_grid(g, limit=12):
    hdr = g["hdr"]
    keep = [i for i, h in enumerate(hdr) if not any(w in h for w in SALES_WORDS)]
    print("   칸: " + " | ".join(hdr[i] for i in keep) + (" (매출 칸은 적지 않음)" if len(keep) < len(hdr) else ""))
    for r in g["rows"][:limit]:
        print("      " + " | ".join(r[i][:40] for i in keep if i < len(r)))


def main():
    if not URL:
        print("APP_URL 이 없다")
        return 1
    bad = []
    with sync_playwright() as p:
        br = p.chromium.launch(executable_path=os.environ.get("CHROMIUM_PATH") or None)
        pg = br.new_context(viewport={"width": 1700, "height": 2600}, locale="ko-KR").new_page()
        pg.goto(URL + "/?page=sugg", wait_until="domcontentloaded", timeout=180_000)
        if not wait_for(pg, "보기"):
            print("제품 제안 화면이 안 떴다 (새 코드가 아직 안 올라왔을 수 있다)")
            br.close()
            return 1
        settle(pg)
        e = errors(pg)
        print("[실패] 제품 제안 처음\n" + e if e else "[정상] 제품 제안 처음 (조건 필터)")
        bad += ["처음"] if e else []
        r = app(pg).locator("[data-testid='stRadio']").filter(has_text="보기").first
        r.locator("label").filter(has_text="원료사 동향").first.click()
        time.sleep(3)
        wait_for(pg, "식약처 DMF 공고 기준", tries=60)
        settle(pg)
        e = errors(pg)
        print("[실패] 원료사 동향\n" + e if e else "[정상] 원료사 동향 열림")
        bad += ["원료사 동향"] if e else []
        print("   " + safe_line(pg, "식약처 DMF 공고 기준"))
        grids = app(pg).evaluate(GRIDS_JS)
        if grids:
            print("\n목록 표")
            print_grid(grids[0], 15)
        if PICK:
            sb = app(pg).locator("[data-testid='stSelectbox']").filter(has_text="성분").last
            sb.click()
            time.sleep(1.5)
            app(pg).locator("[role='option']").filter(has_text=PICK).first.click()
            settle(pg)
            e = errors(pg)
            print(f"\n[실패] 성분 {PICK}\n" + e if e else f"\n[정상] 성분 {PICK} 자세히 보기")
            bad += ["성분"] if e else []
        for w in ("이 성분 이름이 적힌", "근거 적음", "이 기간에", "핵심어를 자동으로 못"):
            l = safe_line(pg, w)
            if l:
                print("   " + l)
        grids = app(pg).evaluate(GRIDS_JS)
        for g in grids[1:]:
            print("\n표")
            print_grid(g, 10)
        br.close()
    print("\n" + (f"문제 {len(bad)}건" if bad else "문제 없음"))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
