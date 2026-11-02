"""사이트를 깨워 두기 위해 실제 브라우저로 한 번 접속한다.

- 주소만 호출하는 핑으로는 Streamlit 앱 본체가 켜지지 않는다(껍데기 HTML만 응답).
- 비밀번호(APP_PASSWORD)가 설정돼 있으면 로그인까지 해서, 새벽 자동 갱신도 함께 돌게 한다.
"""
import os
import sys
import time

from playwright.sync_api import sync_playwright

URL = os.environ.get("APP_URL") or "https://xbpft.streamlit.app/"
PW = os.environ.get("APP_PASSWORD") or ""


def idle(page, timeout=180):
    """Streamlit 이 계산을 끝낼 때까지 기다린다."""
    end = time.time() + timeout
    while time.time() < end:
        if page.query_selector("[data-testid='stStatusWidget']") is None:
            time.sleep(0.5)
            if page.query_selector("[data-testid='stStatusWidget']") is None:
                return True
        time.sleep(0.3)
    return False


def visit(page):
    page.goto(URL, wait_until="domcontentloaded", timeout=90_000)

    # 잠들어 있으면 깨우기 버튼이 나온다 → 눌러 준다
    for label in ("Yes, get this app back up", "get this app back up"):
        try:
            btn = page.get_by_text(label, exact=False).first
            if btn.is_visible(timeout=3_000):
                print("앱이 잠들어 있어 깨우기 버튼을 눌렀습니다")
                btn.click()
                break
        except Exception:
            pass

    page.wait_for_selector("[data-testid='stApp']", timeout=180_000)
    idle(page)

    # 비밀번호 화면이면 로그인 (자료 자동 갱신까지 돌게 하려면 필요)
    if PW:
        try:
            pwbox = page.wait_for_selector("input[type='password']", timeout=60_000)
            pwbox.fill(PW)
            page.keyboard.press("Enter")
            idle(page)
            print("로그인 완료")
        except Exception:
            print("비밀번호 화면이 보이지 않습니다 — 이미 열려 있는 것으로 보고 넘어갑니다")
    else:
        print("APP_PASSWORD 미설정 — 화면만 띄웁니다(잠들지 않게 하는 효과는 동일)")

    time.sleep(5)          # 접속 기록이 남도록 잠시 머문다
    body = page.inner_text("body")[:200].replace("\n", " ")
    print(f"화면 확인: {body}")
    return True


def main():
    with sync_playwright() as p:
        opts = {"args": ["--no-sandbox"]}
        if os.environ.get("CHROME_PATH"):      # 브라우저를 직접 지정해야 하는 환경용
            opts["executable_path"] = os.environ["CHROME_PATH"]
        browser = p.chromium.launch(**opts)
        page = browser.new_page(viewport={"width": 1400, "height": 900})
        page.set_default_timeout(60_000)
        try:
            for attempt in (1, 2):
                try:
                    visit(page)
                    print(f"접속 성공 ({attempt}번째 시도)")
                    return 0
                except Exception as e:
                    print(f"{attempt}번째 시도 실패: {type(e).__name__}: {e}")
                    if attempt == 1:
                        time.sleep(20)
            return 1
        finally:
            browser.close()


if __name__ == "__main__":
    sys.exit(main())
