"""배포된 사이트의 '생동 피험자 수' 화면을 실제로 눌러 확인한다.

화면이 뜨는 것만 보지 않는다 — 기본값으로 계산해 **숫자가 맞는지**까지 본다.
읽기만 하므로 사이트는 바뀌지 않는다.

기대값: 2×2 교차 · CV 30% · GMR 0.95 · 검정력 80% · 0.8~1.25 · α 5%
        → 40명 · 81.58%  (PowerTOST 문서 예제와 같은 값)
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

WANT_N = "40"
WANT_POWER = 81.58
WANT_DESIGNS = 12


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


def wait_text(page, word, tries=48, gap=5):
    """화면에 그 글자가 나올 때까지 기다린다."""
    for _ in range(tries):
        if word in (text_of(page) or ""):
            return True
        time.sleep(gap)
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
    fail = []
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

        # 비밀번호 화면이면 넘어간다
        if PW:
            got = find_all(pg, "input[type='password']")
            if got:
                got[0].fill(PW)
                got[0].press("Enter")
                print("로그인했다")
                time.sleep(14)

        # 1) 어느 코드가 돌고 있나
        build = None
        for _ in range(24):
            b = text_of(pg) or ""
            m = re.search(r"코드 버전[:\s]*([0-9A-Za-z.\-]+)", b)
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

        # 2) 임상 분석 → 생동 피험자 수
        pg.goto(URL + "/?page=clinical", wait_until="domcontentloaded", timeout=120_000)
        for _ in range(60):
            if app_frame(pg):
                break
            time.sleep(2)
        if not wait_text(pg, "임상 분석", tries=60):
            print("임상 분석 화면이 안 떴다 — 판정 불가")
            br.close()
            return 1
        time.sleep(5)
        if not click_text(pg, "label", "생동 피험자 수", wait_for="피험자 수 계산"):
            print("보기 칸에 '생동 피험자 수'가 없다 — 옛 코드다")
            print("   화면 앞부분:", (text_of(pg) or "")[:400].replace("\n", " | "))
            br.close()
            return 1
        body = text_of(pg) or ""
        if "피험자 수 계산" not in body:
            print("계산 버튼이 안 보인다")
            fail.append("계산 버튼")

        ex = find_all(pg, "[data-testid='stException'], .stException")
        if ex:
            print("화면을 여는 데 예외가 떴다:")
            print((ex[0].inner_text() or "")[:400])
            br.close()
            return 1
        print("[정상] 화면 열림")

        # 3) 눌러서 계산
        if not click_text(pg, "button", "피험자 수 계산",
                          wait_for="필요 피험자 수", tries=36):
            print("계산 버튼을 못 눌렀다")
            br.close()
            return 1
        time.sleep(4)
        body = text_of(pg) or ""
        ex = find_all(pg, "[data-testid='stException'], .stException")
        if ex:
            print("계산에서 예외가 떴다:")
            print((ex[0].inner_text() or "")[:400])
            br.close()
            return 1

        mn = re.search(r"필요 피험자 수[^\d]*([\d,]+)", body)
        mp = re.search(r"실제 검정력[^\d]*([\d.]+)", body)
        n = mn.group(1).replace(",", "") if mn else None
        p = float(mp.group(1)) if mp else None
        ok = (n == WANT_N) and (p is not None) and abs(p - WANT_POWER) < 0.02
        print(f"[{'정상' if ok else '확인'}] 2×2 · CV 30 기본값 → "
              f"{n or '?'}명 · {p if p is not None else '?'}%  "
              f"(기대 {WANT_N}명 · {WANT_POWER}%)")
        if not ok:
            fail.append("기본 계산값")
            print("   화면 앞부분:", body[:400].replace("\n", " | "))

        # 4) 설계 목록이 12가지인지
        for word in ("2×2 교차", "부분반복", "Williams", "Balaam",
                     "Liu 반복교차", "대응표본", "병렬"):
            if word not in body:
                # 드롭다운을 열어야 보이는 것도 있다 — 열어서 다시 본다
                break
        else:
            word = None
        boxes = find_all(pg, "[data-testid='stSelectbox']")
        if boxes:
            try:
                boxes[0].click()
                time.sleep(2)
                opts = [o.inner_text() or "" for o in find_all(pg, "[role='option']")]
                print(f"[{'정상' if len(opts) == WANT_DESIGNS else '확인'}] "
                      f"설계 {len(opts)}가지 (기대 {WANT_DESIGNS})")
                if len(opts) != WANT_DESIGNS:
                    fail.append("설계 가짓수")
                print("   " + " / ".join(o.strip() for o in opts)[:400])
                pg.keyboard.press("Escape")
                time.sleep(1)
            except Exception as e:
                print(f"설계 목록을 못 읽었다: {str(e)[:120]}")
        else:
            print("설계 고르는 칸을 못 찾았다")
            fail.append("설계 칸")

        br.close()

    print()
    if fail:
        print("확인이 필요하다: " + ", ".join(fail))
        return 1
    print("새 코드가 올라갔고, 계산도 맞다")
    return 0


raise SystemExit(main())
