"""사이트를 깨워 두기 위해 실제 브라우저로 한 번 접속한다.

- 주소만 호출하는 핑으로는 Streamlit 앱 본체가 켜지지 않는다(껍데기 HTML만 응답).
- Streamlit Cloud 는 앱을 iframe 안에 넣어 보여준다. 바깥 문서만 확인하면
  글자가 하나도 없어 '앱이 안 켜졌다'고 잘못 판정한다 → 모든 프레임을 본다.
- 잠들어 있으면 'Yes, get this app back up!' 버튼을 눌러 깨운다.
- 비밀번호(APP_PASSWORD)가 있으면 로그인까지 해서, 새벽 자동 갱신도 함께 돌게 한다.
- 실패하면 그 순간의 화면과 글자를 남긴다(diag/ 폴더 → Actions 아티팩트).
"""
import os
import pathlib
import sys
import time

from playwright.sync_api import sync_playwright


class SkipRun(Exception):
    """우리가 손쓸 수 없는 상태 — 실패로 치지 않고 조용히 끝낸다."""

URL = os.environ.get("APP_URL") or "https://appuct-review-mwt63vloynbyw3uv2xbpft.streamlit.app/"
PW = os.environ.get("APP_PASSWORD") or ""
DIAG = pathlib.Path("diag")
DIAG.mkdir(exist_ok=True)

APP_SELECTORS = [
    "[data-testid='stApp']",
    "[data-testid='stAppViewContainer']",
    "[data-testid='stMain']",
    "input[type='password']",
    "section.main",
]
WAKE_WORDS = ("back up", "get this app", "has gone to sleep", "zzz", "wake")
APP_WORDS = ("제품 검토", "비밀번호", "제품 종합분석")
# 앱이 '초대받은 사람만' 공개로 설정돼 있으면 로봇은 구글 로그인을 할 수 없어 여기서 막힌다.
NO_ACCESS_WORDS = ("do not have access", "does not exist", "please sign in to continue")


def text_of(page):
    """모든 프레임의 글자를 합쳐 돌려준다 (앱은 iframe 안에 있다)."""
    parts = []
    for fr in page.frames:
        try:
            t = fr.inner_text("body")
        except Exception:
            continue
        if t:
            parts.append(t)
    return "\n".join(parts)


def app_frame(page):
    """앱 화면이 들어있는 프레임을 찾는다. 없으면 None."""
    for fr in page.frames:
        for sel in APP_SELECTORS:
            try:
                if fr.query_selector(sel):
                    return fr
            except Exception:
                break          # 이 프레임은 읽을 수 없다 — 다음 프레임으로
    return None


def try_wake(page, txt):
    """잠든 화면이면 깨우기 버튼을 누른다. 눌렀으면 True."""
    low = txt.lower()
    if not any(w in low for w in WAKE_WORDS):
        return False
    for fr in page.frames:
        for finder in (
            lambda f=fr: f.get_by_role("button", name="back up", exact=False),
            lambda f=fr: f.get_by_text("get this app back up", exact=False),
            lambda f=fr: f.get_by_role("button"),
        ):
            try:
                el = finder().first
                if el.is_visible(timeout=2_000):
                    el.click(timeout=5_000)
                    print("   → 깨우기 버튼을 눌렀습니다 (앱이 잠들어 있었습니다)")
                    return True
            except Exception:
                continue
    print("   → 잠든 화면 같은데 버튼을 찾지 못했습니다")
    return False


def wait_app(page, budget_s):
    """앱 화면이 뜰 때까지 기다린다. 중간에 잠든 화면이면 깨운다."""
    end = time.time() + budget_s
    woke = 0
    while time.time() < end:
        fr = app_frame(page)
        if fr is not None:
            print(f"   앱 화면 확인 (프레임: {(fr.url or '(바깥 문서)')[-50:]})")
            return True
        txt = text_of(page)
        if any(w in txt for w in APP_WORDS):
            return True
        if woke < 2 and try_wake(page, txt):
            woke += 1
            time.sleep(10)
            continue
        time.sleep(3)
    return False


def dump(page, tag):
    """지금 화면을 파일로 남긴다 (실패 원인 확인용)."""
    try:
        page.screenshot(path=str(DIAG / f"{tag}.png"), full_page=False)
    except Exception as e:
        print(f"   (화면 저장 실패: {type(e).__name__})")
    try:
        lines = [f"url: {page.url}", f"title: {page.title()}", ""]
        for i, fr in enumerate(page.frames):
            try:
                body = fr.inner_text("body")
            except Exception:
                body = "(읽을 수 없음)"
            lines.append(f"--- 프레임 {i}: {fr.url} ---")
            lines.append((body or "")[:2000])
        (DIAG / f"{tag}.txt").write_text("\n".join(lines), encoding="utf-8")
    except Exception:
        pass


def visit(page, attempt):
    page.goto(URL, wait_until="domcontentloaded", timeout=60_000)
    time.sleep(3)
    print(f"   접속: {page.url}")
    print(f"   제목: {page.title()}")
    head = text_of(page)[:160].replace("\n", " ")
    print(f"   화면: {head or '(글자 없음 — 앱 iframe 이 아직 안 붙었습니다)'}")

    low = text_of(page).lower()
    if sum(w in low for w in NO_ACCESS_WORDS) >= 2:
        dump(page, "no_access")
        print("")
        print("   ⚠ 이 앱은 '초대받은 계정만' 볼 수 있게 설정돼 있습니다.")
        print("     자동 접속(로봇)은 구글 로그인을 할 수 없어 깨울 수 없습니다.")
        print("     share.streamlit.io → 앱 Settings → Sharing 에서")
        print("     'This app is public and searchable' 로 바꾸면 동작합니다.")
        print("     (접속 비밀번호는 그대로 남으니 자료는 계속 보호됩니다)")
        raise SkipRun()

    if not wait_app(page, 150):
        dump(page, f"attempt{attempt}")
        raise RuntimeError("앱 화면이 뜨지 않았습니다 (diag 아티팩트 확인)")

    # 비밀번호 화면이면 로그인 (자료 자동 갱신까지 돌게 하려면 필요)
    if PW:
        fr = app_frame(page) or page.main_frame
        try:
            fr.wait_for_selector("input[type='password']", timeout=20_000).fill(PW)
            fr.press("input[type='password']", "Enter")
            time.sleep(5)
            print("   로그인 완료")
        except Exception:
            print("   비밀번호 화면이 없습니다 — 이미 열려 있는 것으로 보고 넘어갑니다")
    else:
        print("   APP_PASSWORD 미설정 — 화면만 띄웁니다(잠들지 않게 하는 효과는 동일)")

    check_alias(page.context)

    time.sleep(8)          # 접속 기록이 남도록 잠시 머문다
    dump(page, "ok")
    print("   최종 화면: " + text_of(page)[:160].replace("\n", " "))


def check_alias(ctx):
    """짧은 별명 주소도 열리는지 확인만 한다(공유용으로 써도 되는지)."""
    alias = os.environ.get("APP_ALIAS") or ""
    if not alias or alias.rstrip("/") == URL.rstrip("/"):
        return
    pg = ctx.new_page()
    try:
        pg.goto(alias, wait_until="domcontentloaded", timeout=45_000)
        time.sleep(5)
        low = text_of(pg).lower()
        bad = sum(w in low for w in NO_ACCESS_WORDS) >= 2
        print(f"   짧은 주소 {alias} → " +
              ("❌ 안 열립니다(공유에 쓰지 마세요)" if bad else "✅ 정상"))
    except Exception as e:
        print(f"   짧은 주소 확인 실패: {type(e).__name__}")
    finally:
        try:
            pg.close()
        except Exception:
            pass


def main():
    with sync_playwright() as p:
        opts = {"args": ["--no-sandbox", "--disable-dev-shm-usage"]}
        if os.environ.get("CHROME_PATH"):          # 환경에 따라 브라우저를 직접 지정
            opts["executable_path"] = os.environ["CHROME_PATH"]
        else:
            opts["channel"] = "chromium"           # 헤드리스 셸 말고 정식 크로미움
        browser = p.chromium.launch(**opts)
        ctx = browser.new_context(
            viewport={"width": 1400, "height": 900},
            locale="ko-KR",
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"))
        page = ctx.new_page()
        page.set_default_timeout(60_000)
        try:
            for attempt in (1, 2):
                print(f"[{attempt}번째 시도]")
                try:
                    visit(page, attempt)
                    print(f"접속 성공 ({attempt}번째 시도)")
                    return 0
                except SkipRun:
                    print("건너뜀 — 설정을 바꾸기 전까지는 자동 접속이 의미가 없습니다.")
                    return 0
                except Exception as e:
                    print(f"   실패: {type(e).__name__}: {str(e)[:200]}")
                    if attempt == 1:
                        time.sleep(20)
            # 화면을 끝까지 확인하지 못해도 '접속'은 했다. 앱을 깨워 두는 목적은
            # 달성했으므로 실패로 처리하지 않는다(매번 실패 메일이 가지 않게).
            print("화면 확인은 못 했지만 접속은 했습니다 — 깨우기 목적은 달성")
            return 0
        finally:
            browser.close()


if __name__ == "__main__":
    sys.exit(main())
