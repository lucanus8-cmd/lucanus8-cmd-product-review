"""배포된 사이트의 '제품 제안 → 🧬 약물 재창출'을 열어 실제로 지도가 그려지는지 본다.

로봇 기록은 공개다. 지도 칸의 글자(약·단백질·질환 이름, 임상 단계, 국내 특허·품목 수 같은 공개 정보)만 적고,
오른쪽 자세히 보기 칸(매출이 나올 수 있음)과 다른 매출 화면 글자는 절대 적지 않는다. 읽고 누르기만 한다.

    QUERY="실데나필;기침" python .github/scripts/check_repurpose_live.py
"""
import os
import sys
import time

from playwright.sync_api import sync_playwright

URL = (os.environ.get("APP_URL") or "").rstrip("/")
PW = os.environ.get("APP_PASSWORD") or ""
Q = ((os.environ.get("QUERY") or "").strip() or "실데나필;기침").split(";")
DRUG, DIS = (Q + ["", ""])[:2]
APP_SEL = ("[data-testid='stAppViewContainer']", "[data-testid='stApp']", "input[type='password']")


def app(pg):
    for fr in pg.frames:
        for sel in APP_SEL:
            try:
                if fr.query_selector(sel):
                    return fr
            except Exception:
                break
    return pg.main_frame


def body(pg):
    try:
        return app(pg).inner_text("body")
    except Exception:
        return ""


def wait_for(pg, word, tries=96, gap=5):
    for _ in range(tries):
        if word in body(pg):
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


def settle(pg, gap=1.0, need=5, cap=240):
    last, same, t0 = None, 0, time.time()
    while time.time() - t0 < cap:
        b = body(pg)
        busy = app(pg).query_selector("[data-testid='stStatusWidget']")
        same = same + 1 if (b == last and not busy) else 0
        last = b
        if same >= need:
            return
        time.sleep(gap)


def errors(pg):
    ex = app(pg).query_selector_all("[data-testid='stException']")
    return (ex[0].inner_text() or "")[:800] if ex else None


def nodes(pg):
    """지도 칸 글자만 (plotly 주석). 오른쪽 자세히 보기 칸은 읽지 않는다."""
    try:
        return app(pg).evaluate("() => [...document.querySelectorAll('.js-plotly-plot .annotation-text')]"
                                ".map(e => e.textContent.trim()).filter(Boolean)")
    except Exception:
        return []


def notes(pg):
    keep = ("검색어", "찾지 못", "접속하지 못", "조건에 맞는 칸이 없", "이름을 넣고", "판)", "바꾸지 못")
    return [l.strip()[:160] for l in body(pg).splitlines() if any(k in l for k in keep)]


def run(pg, mode, text):
    fr = app(pg)
    fr.locator("[data-testid='stRadio']").filter(has_text="무엇으로").locator("label").filter(
        has_text="약으로" if mode == "drug" else "질환으로").first.click()
    settle(pg, need=3)
    lab = "약 이름" if mode == "drug" else "질환 이름"
    i = app(pg).locator("[data-testid='stTextInput']").filter(has_text=lab).locator("input").first
    i.fill(text)
    i.press("Enter")
    settle(pg, need=3)
    t = time.time()
    app(pg).locator("button").filter(has_text="지도 그리기").first.click()
    time.sleep(3)
    settle(pg, need=6)
    e = errors(pg)
    print(f"\n[{'실패' if e else '정상'}] {'약' if mode == 'drug' else '질환'}으로: {text} ({time.time() - t:.1f}초, 기다림 포함)")
    if e:
        print(e)
    for l in notes(pg):
        print("   " + l)
    ns = nodes(pg)
    print(f"   지도 칸 {len(ns)}개:")
    for n in ns[:40]:
        print("     · " + n[:120])
    pts = app(pg).locator(".js-plotly-plot .scatterlayer .trace .points path")
    if pts.count() > 2:
        pts.nth(pts.count() - 1).click(force=True)
        settle(pg, need=4)
        e2 = errors(pg)
        print(f"   [{'실패' if e2 else '정상'}] 지도 칸 누르기")
        if e2:
            print(e2)
        e = e or e2
    return 0 if not e else 1


def main():
    if not URL:
        print("APP_URL 이 없다")
        return 1
    bad = 0
    with sync_playwright() as p:
        br = p.chromium.launch(executable_path=os.environ.get("CHROMIUM_PATH") or None)
        pg = br.new_context(viewport={"width": 1600, "height": 2600}, locale="ko-KR").new_page()
        pg.goto(URL + "/?page=status", wait_until="domcontentloaded", timeout=180_000)
        if wait_for(pg, "코드 버전"):
            settle(pg, need=3)
            ln = [l for l in body(pg).splitlines() if "코드 버전" in l]
            print("사이트 " + (ln[0].strip() if ln else "코드 버전 못 읽음"))
        pg.goto(URL + "/?page=sugg", wait_until="domcontentloaded", timeout=180_000)
        if not wait_for(pg, "제품 제안"):
            print("제품 제안 화면이 안 떴다")
            br.close()
            return 1
        settle(pg)
        if "약물 재창출" not in body(pg):
            print("[아직] '약물 재창출' 보기가 없다 — 새 코드가 아직 안 올라왔다")
            br.close()
            return 1
        app(pg).locator("label").filter(has_text="약물 재창출").first.click()
        settle(pg)
        e = errors(pg)
        print("[실패] 약물 재창출 열기\n" + e if e else "[정상] 약물 재창출 열기")
        bad += 1 if e else 0
        for q in [x for x in DRUG.split(",") if x.strip()]:
            bad += run(pg, "drug", q.strip())
        for q in [x for x in DIS.split(",") if x.strip()]:
            bad += run(pg, "disease", q.strip())
        for v in ("조건 필터", "원료사 동향"):
            app(pg).locator("label").filter(has_text=v).first.click()
            settle(pg, need=4)
            e = errors(pg)
            print(f"[{'실패' if e else '정상'}] 제품 제안 · {v}")
            bad += 1 if e else 0
        br.close()
    print("\n" + (f"문제 {bad}건" if bad else "문제 없음"))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
