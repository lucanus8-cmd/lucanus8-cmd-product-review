"""사이트를 깨우고, 자료 파일(허가·특허·임상·약가·공단 협상·매출)을 미리 만들어 둔다.

매일 오전·저녁 7시에 GitHub 가 돌린다(.github/workflows/wake-site.yml).
- 잠들어 있으면(12시간 동안 방문 없음) 깨우기 단추를 누른다.
- 비밀번호를 넣고 '?page=status&prep=1' 을 연다. 사이트는 화면 대신 자료 파일만 차례로 만들고
  (이미 최신이면 건너뜀) '모두 준비됨'을 띄운다. 메모리에는 올리지 않는다.
- 그 주소가 없는 예전 사이트면, 예전처럼 제품 제안 화면의 매출 '불러오기' 단추를 누른다.

로봇 기록은 공개다. 화면 글은 적지 않고 '깨움/불러옴/이미 있음/실패' 같은 결과만 적는다.
읽고 누르기만 하므로 사이트 코드는 바뀌지 않는다.
"""
import os
import sys
import time

from playwright.sync_api import sync_playwright

URL = (os.environ.get("APP_URL") or "").rstrip("/")
PW = os.environ.get("APP_PASSWORD") or ""
APP_SEL = ("[data-testid='stAppViewContainer']", "[data-testid='stApp']", "input[type='password']")
LOAD_BTN = "매출 자료 지금 불러오기"
READY = "필터 조건"                  # 매출이 있어야만 제품 제안 화면에 그려지는 글자
FAIL = "매출 자료를 불러오지 못했습니다"
PREP_FAIL = "자료를 받아오지 못했습니다"


def app(pg):
    for fr in pg.frames:
        for sel in APP_SEL:
            try:
                if fr.query_selector(sel):
                    return fr
            except Exception:
                break
    return pg.main_frame


def text_of(fr):
    try:
        return fr.inner_text("body")
    except Exception:
        return ""


def wake_if_sleeping(pg):
    """Streamlit 이 재운 사이트면 바깥 화면에 깨우기 단추가 있다."""
    for _ in range(6):
        for fr in pg.frames:
            t = text_of(fr)
            if "gone to sleep" in t or "get this app back up" in t:
                b = fr.locator("button").filter(has_text="back up").first
                if b.count():
                    b.click()
                    print("잠들어 있어서 깨웠다")
                    return True
        time.sleep(5)
    return False


def press(fr, label):
    b = fr.locator("button").filter(has_text=label).first
    if b.count():
        b.click()
        return True
    return False


def prep_files(pg):
    """새 방식: 파일만 미리 만들기. 끝나면 0, 실패면 1, 그 주소가 없는 예전 사이트면 None."""
    pg.goto(URL + "/?page=status&prep=1", wait_until="domcontentloaded", timeout=180_000)
    time.sleep(8)
    wake_if_sleeping(pg)
    t0, seen = time.time(), False
    while time.time() - t0 < 25 * 60:
        fr = app(pg)
        t = text_of(fr)
        if PW and fr.query_selector("input[type='password']"):
            box = fr.query_selector("input[type='password']")
            box.fill(PW)
            box.press("Enter")
            print("비밀번호를 넣었다")
            time.sleep(12)
            continue
        if "자료 미리 만들기" in t:
            seen = True
        lines = [l.strip() for l in t.splitlines() if " — " in l and any(
            w in l for w in ("준비됨", "만드는 중", "기다림"))]
        if "모두 준비됨" in t:
            for l in lines:
                print("   " + l[:60])
            print(f"모두 준비됨 · 걸린 시간 {time.time() - t0:.0f}초")
            return 0
        if seen and "실패 —" in t:
            for l in lines:
                print("   " + l[:60])
            print("[실패] 자료를 만들지 못했다 — " + next((l for l in t.splitlines() if "실패 —" in l), "")[:150])
            return 1
        if not seen and time.time() - t0 > 4 * 60:
            print("'자료 미리 만들기' 주소가 없는 예전 사이트 — 예전 방식으로 매출만 불러온다")
            return None
        time.sleep(6)
    print("[실패] 25분 안에 끝나지 않았다")
    return 1


def main():
    if not URL:
        print("APP_URL 이 없다")
        return 1
    with sync_playwright() as p:
        br = p.chromium.launch(executable_path=os.environ.get("CHROMIUM_PATH") or None)
        pg = br.new_context(viewport={"width": 1500, "height": 1800}, locale="ko-KR").new_page()
        r = prep_files(pg)
        br.close()
    if r is not None:
        return r
    return old_main()


def old_main():
    with sync_playwright() as p:
        br = p.chromium.launch(executable_path=os.environ.get("CHROMIUM_PATH") or None)
        pg = br.new_context(viewport={"width": 1500, "height": 1800}, locale="ko-KR").new_page()
        pg.goto(URL + "/?page=sugg", wait_until="domcontentloaded", timeout=180_000)
        time.sleep(8)
        wake_if_sleeping(pg)

        retried, pressed, t0 = 0, False, time.time()
        while time.time() - t0 < 20 * 60:
            fr = app(pg)
            t = text_of(fr)
            if PW and fr.query_selector("input[type='password']"):
                box = fr.query_selector("input[type='password']")
                box.fill(PW)
                box.press("Enter")
                print("비밀번호를 넣었다")
                time.sleep(12)
                continue
            if PREP_FAIL in t and retried < 2:              # 구글 쪽 일시 오류(503 등)
                if press(fr, "다시 시도"):
                    retried += 1
                    print(f"자료 준비가 잠깐 실패해서 다시 시도를 눌렀다 ({retried})")
                    time.sleep(20)
                    continue
            if FAIL in t:
                print("[실패] 매출 자료를 불러오지 못했다 — 사이트에서 직접 눌러 보세요")
                br.close()
                return 1
            if LOAD_BTN in t and not pressed:
                if press(fr, LOAD_BTN):
                    pressed = True
                    print("매출 자료가 없어서 '불러오기'를 눌렀다")
                    time.sleep(15)
                    continue
            if READY in t and LOAD_BTN not in t:
                print("매출 자료를 새로 불러왔다" if pressed else "매출 자료가 이미 있다 — 할 일 없음")
                print(f"걸린 시간 {time.time() - t0:.0f}초")
                br.close()
                return 0
            time.sleep(6)
        print("[실패] 20분 안에 끝나지 않았다")
        br.close()
        return 1


if __name__ == "__main__":
    sys.exit(main())
