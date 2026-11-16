"""배포된 사이트의 '임상 분석 → 📐 간이 메타분석'을 열어 실제로 찾기·계산이 되는지 본다.

ClinicalTrials.gov·PubMed 공개 자료만 다루는 화면이라 숫자를 적어도 된다 (매출 화면은 열지 않는다).
읽고 누르기만 하므로 사이트는 바뀌지 않는다.

    QUERY="bempedoic acid;ezetimibe;LDL;12;" python .github/scripts/check_meta_live.py
"""
import os
import sys
import time

from playwright.sync_api import sync_playwright

URL = (os.environ.get("APP_URL") or "").rstrip("/")
PW = os.environ.get("APP_PASSWORD") or ""
Q = ((os.environ.get("QUERY") or "").strip() or "bempedoic acid;ezetimibe;LDL;12;").split(";")
A, B, OUT, WEEK, COND = (Q + ["", "", "", "", ""])[:5]
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


def settle(pg, gap=1.0, need=5, cap=300):
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


def fill(pg, label, value):
    i = app(pg).locator("[data-testid='stTextInput']").filter(has_text=label).locator("input").first
    i.fill(value)
    i.press("Enter")
    settle(pg, need=3)


def main():
    if not URL:
        print("APP_URL 이 없다")
        return 1
    bad = []
    with sync_playwright() as p:
        br = p.chromium.launch(executable_path=os.environ.get("CHROMIUM_PATH") or None)
        pg = br.new_context(viewport={"width": 1600, "height": 2600}, locale="ko-KR").new_page()
        pg.goto(URL + "/?page=clinical", wait_until="domcontentloaded", timeout=180_000)
        if not wait_for(pg, "임상 분석"):
            print("임상 분석 화면이 안 떴다")
            br.close()
            return 1
        settle(pg)
        if "간이 메타분석" not in body(pg):
            print("[아직] '간이 메타분석' 보기가 없다 — 새 코드가 아직 안 올라왔다")
            br.close()
            return 1
        app(pg).locator("label").filter(has_text="간이 메타분석").first.click()
        settle(pg)
        e = errors(pg)
        print("[실패] 간이 메타분석 열기\n" + e if e else "[정상] 간이 메타분석 열림")
        bad += ["열기"] if e else []
        fill(pg, "성분 A", A)
        fill(pg, "성분 B", B)
        fill(pg, "지표 이름", OUT)
        if WEEK:
            n = app(pg).locator("[data-testid='stNumberInput']").filter(has_text="시점").locator("input").first
            n.fill(WEEK)
            n.press("Enter")
            settle(pg, need=3)
        if COND:
            fill(pg, "질환", COND)
        t = time.time()
        app(pg).locator("button").filter(has_text="임상 찾기").first.click()
        time.sleep(3)
        settle(pg, need=6)
        print(f"찾기·계산 {time.time() - t:.1f}초 (기다림 포함)")
        e = errors(pg)
        print("[실패] 임상 찾기\n" + e if e else "[정상] 임상 찾기")
        bad += ["찾기"] if e else []
        for l in body(pg).splitlines():
            if any(w in l for w in ("받음", "접속하지 못했습니다", "PubMed 논문 연결")):
                print("   " + l.strip()[:300])
        mets = app(pg).evaluate("() => [...document.querySelectorAll(\"[data-testid='stMetric']\")]"
                                ".map(e => e.innerText.replace(/\\n/g, ' '))")
        print("   숫자 칸: " + " | ".join(mets))
        txt = body(pg)
        i = txt.find("결론")
        if i >= 0:
            print("   " + " ".join(txt[i:i + 400].split())[:400])
        for ex in ("하나씩 빼 보기", "깔때기 그림", "소그룹 비교"):
            ep = app(pg).locator("[data-testid='stExpander']").filter(has_text=ex).first
            if ep.count():
                ep.locator("summary").first.click()
                settle(pg, need=3)
                e = errors(pg)
                print(f"[실패] {ex}\n" + e if e else f"[정상] {ex}")
                bad += [ex] if e else []
        for v in ("검색", "분석", "생동 피험자 수", "1상 비용 어림"):
            app(pg).locator("label").filter(has_text=v).first.click() if v != "분석" else \
                app(pg).locator("label").filter(has_text="📊 분석").first.click()
            settle(pg, need=4)
            e = errors(pg)
            print(f"[실패] 임상 분석 · {v}\n" + e if e else f"[정상] 임상 분석 · {v}")
            bad += [v] if e else []
        br.close()
    print("\n" + (f"문제 {len(bad)}건: {bad}" if bad else "문제 없음"))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
