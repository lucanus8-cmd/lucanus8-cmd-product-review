"""배포된 사이트의 '1상 비용 어림' 화면을 실제로 열어 확인한다.

읽기만 하므로 사이트는 바뀌지 않는다. 단가는 사이트 설정(Secrets)에 있어서
두 경우가 다 정상이다.
  · 단가가 들어 있으면 → 총 비용이 숫자로 나온다
  · 아직 없으면        → '단가가 아직 없습니다' 안내가 나온다
어느 쪽인지 알려주고, 화면에 예외가 있으면 실패로 본다. 금액은 적지 않는다.
"""
import os
import re
import time

from playwright.sync_api import sync_playwright

URL = (os.environ.get("APP_URL") or "").rstrip("/")
PW = os.environ.get("APP_PASSWORD") or ""
WANT_BUILD = os.environ.get("WANT_BUILD") or ""
APP_SEL = ("[data-testid='stApp']", "[data-testid='stAppViewContainer']",
           "[data-testid='stMain']", "input[type='password']", "section.main")



def find_all(page, sel):
    out = []
    for fr in page.frames:
        try:
            out += fr.query_selector_all(sel)
        except Exception:
            pass
    return out


def text_of(page):
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
    for fr in page.frames:
        for sel in APP_SEL:
            try:
                if fr.query_selector(sel):
                    return fr
            except Exception:
                break
    return None


def relogin(page):
    """화면을 옮기면 비밀번호 칸이 다시 나올 수 있다 — 나오면 다시 넣는다."""
    if not PW:
        return False
    got = find_all(page, "input[type='password']")
    if not got:
        return False
    try:
        got[0].fill(PW)
        got[0].press("Enter")
        print("   (비밀번호 다시 넣음)")
        time.sleep(14)
        return True
    except Exception:
        return False


def wait_text(page, word, tries=48, gap=5, label=""):
    """화면에 그 글자가 나올 때까지 기다린다.

    안 나오면 그때 화면이 어떤 상태였는지 남긴다 — 추측하지 않으려고.
    """
    for i in range(tries):
        b = text_of(page) or ""
        if word in b:
            return True
        if i and i % 12 == 0:            # 1분마다 상태를 남긴다
            print(f"   [{label or word}] {i * gap}초째 · 글자 {len(b)}자 · "
                  f"{b[:120].replace(chr(10), ' | ')}")
            relogin(page)
        time.sleep(gap)
    b = text_of(page) or ""
    print(f"   [{label or word}] 못 찾음. 그때 화면({len(b)}자):")
    print("   " + b[:800].replace("\n", " | "))
    ex = find_all(page, "[data-testid='stException'], .stException")
    if ex:
        print("   화면의 예외:", (ex[0].inner_text() or "")[:400])
    return False


def click_text(page, sel, word, wait_for=None, tries=48, gap=5):
    """글자가 든 요소를 눌러 본다.

    실제 사이트는 자료를 읽느라 화면이 늦게 그려진다. 고정 시간만 기다리면
    아직 그리는 중인 화면을 보고 '없다'고 잘못 판정한다 — 그래서 요소가
    나올 때까지 기다렸다가 누른다.
    """
    el = None
    for _ in range(tries):
        for e in find_all(page, sel):
            try:
                if word in (e.inner_text() or ""):
                    el = e
                    break
            except Exception:
                pass
        if el is not None:
            break
        time.sleep(gap)
    if el is None:
        return False
    try:
        el.click()
    except Exception as e:
        print(f"'{word}' 를 못 눌렀다: {str(e)[:120]}")
        return False
    if wait_for:
        wait_text(page, wait_for, tries=tries, gap=gap)
    else:
        time.sleep(8)
    return True


def main():
    if not URL:
        print("APP_URL 이 없다 — 판정 불가")
        return 1
    with sync_playwright() as pw:
        br = pw.chromium.launch()
        pg = br.new_context(viewport={"width": 1500, "height": 1800},
                            locale="ko-KR").new_page()

        pg.goto(URL + "/?page=status", wait_until="domcontentloaded", timeout=120_000)
        for _ in range(60):
            if app_frame(pg):
                break
            time.sleep(2)
        if not app_frame(pg):
            print("앱 화면을 못 찾았다 — 판정 불가")
            br.close()
            return 1
        time.sleep(8)
        if relogin(pg):
            print("로그인했다")

        build = None
        for _ in range(24):
            m = re.search(r"코드 버전[:\s]*([0-9A-Za-z.\-]+)", text_of(pg) or "")
            if m:
                build = m.group(1)
                break
            time.sleep(5)
        print(f"사이트가 돌리는 코드 버전: {build or '(못 읽음)'}"
              + (f"   (기대 {WANT_BUILD})" if WANT_BUILD else ""))
        if WANT_BUILD and build != WANT_BUILD:
            print("아직 새 코드가 아니다 — 배포가 끝나기 전이거나 옛 코드다")
            br.close()
            return 1

        pg.goto(URL + "/?page=clinical", wait_until="domcontentloaded", timeout=120_000)
        for _ in range(60):
            if app_frame(pg):
                break
            time.sleep(2)
        if not wait_text(pg, "임상 분석", tries=96, label="임상 분석 화면"):
            print("임상 분석 화면이 안 떴다 — 판정 불가")
            br.close()
            return 1
        time.sleep(5)
        if not click_text(pg, "label", "1상 비용 어림", wait_for="대상자 수"):
            print("보기 칸에 '1상 비용 어림'이 없다 — 옛 코드다")
            br.close()
            return 1
        time.sleep(6)

        ex = find_all(pg, "[data-testid='stException'], .stException")
        if ex:
            print("화면에 예외가 떴다:")
            print((ex[0].inner_text() or "")[:400])
            br.close()
            return 1
        body = text_of(pg) or ""
        fail = []
        for word in ("대상자 수", "기 수", "기당 채혈 포인트", "분석 성분 수",
                     "마지막 채혈 시점", "단가 고치기"):
            if word not in body:
                fail.append(word)
        print("[정상] 화면 열림 · 입력 칸 다섯 개" if not fail
              else "[확인] 안 보이는 칸: " + ", ".join(fail))

        if "단가가 아직 없습니다" in body:
            print("[안내] 단가가 아직 없다 — 사이트 설정(Secrets)에 [phase1_cost] 를 "
                  "넣으면 총 비용이 나온다")
        elif re.search(r"총 비용[^\d]*[\d.]+억", body):
            print("[정상] 단가가 들어 있고 총 비용이 나온다")
        else:
            print("[확인] 안내도 총 비용도 안 보인다")
            print("   화면 앞부분:", body[:400].replace("\n", " | "))
            fail.append("결과")
        br.close()

    print()
    if fail:
        print("확인이 필요하다: " + ", ".join(fail))
        return 1
    print("새 코드가 올라갔고, 화면에 예외가 없다")
    return 0


raise SystemExit(main())
