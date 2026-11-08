"""배포된 사이트의 '약가 분석 → 약가 산정' 화면이 멀쩡히 열리는지 확인한다.

작업 브랜치 전용 점검 도구다(사이트 동작과는 무관).
Streamlit Cloud 는 앱을 iframe(/~/+/) 안에 그리므로, 바깥 문서만 보면 안 되고
모든 프레임을 훑어야 한다.
결과는 전부 로그로 찍는다 — 아티팩트를 받지 않아도 보이도록.
"""
import os
import time

from playwright.sync_api import sync_playwright

URL = (os.environ.get("APP_URL") or "").rstrip("/")
PW = os.environ.get("APP_PASSWORD") or ""
# '#root' 같은 느슨한 선택자는 Streamlit Cloud 바깥 껍데기에도 걸린다.
# 그러면 엉뚱한 프레임을 붙잡아, 눈앞에 있는 비밀번호 칸도 못 본다.
APP_SELECTORS = ("[data-testid='stApp']", "[data-testid='stAppViewContainer']",
                 "[data-testid='stMain']", "input[type='password']", "section.main")


def find_all(page, sel):
    """모든 프레임에서 찾는다 — 앱이 어느 프레임에 있든 놓치지 않게."""
    out = []
    for fr in page.frames:
        try:
            out += fr.query_selector_all(sel)
        except Exception:
            pass
    return out


def find_one(page, sel):
    got = find_all(page, sel)
    return got[0] if got else None


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
        for sel in APP_SELECTORS:
            try:
                if fr.query_selector(sel):
                    return fr
            except Exception:
                break
    return None


def main():
    if not URL:
        print("APP_URL 이 비어 있다 — 확인 못 함")
        return 0
    target = URL + "/?page=price"
    print("대상:", target)
    with sync_playwright() as pw:
        br = pw.chromium.launch()
        pg = br.new_page(viewport={"width": 1440, "height": 1500}, locale="ko-KR")
        pg.goto(target, wait_until="domcontentloaded", timeout=120_000)

        fr = None
        for _ in range(60):                 # 잠든 앱은 깨어나는 데 1~3분 걸린다
            fr = app_frame(pg)
            if fr:
                break
            time.sleep(2)
        if not fr:
            print("앱 화면을 못 찾았다(아직 깨는 중이거나 비공개).")
            print((text_of(pg) or "")[:500])
            br.close()
            return 0
        time.sleep(8)

        # 앱이 깨어나는 중이면 비밀번호 화면이 늦게 뜬다. 로그인과 버튼 찾기를
        # 한 바퀴에서 같이 돌려야, 둘 중 뭐가 먼저 나와도 놓치지 않는다.
        logged = False
        hit = False
        for _ in range(75):                 # 최대 약 2분 30초
            if not logged and PW:
                try:
                    box = find_one(pg, "input[type='password']")
                    if box:
                        box.fill(PW)
                        box.press("Enter")
                        logged = True
                        print("로그인: 비밀번호 넣었다")
                        time.sleep(10)
                        continue
                except Exception as e:
                    print("로그인 시도 중:", e)
            for lab in find_all(pg, "[data-testid='stRadio'] label"):
                try:
                    if "약가 산정" in (lab.inner_text() or ""):
                        lab.click()
                        hit = True
                        break
                except Exception:
                    pass
            if hit:
                break
            time.sleep(2)

        if not hit:
            # 못 찾았으면 '정상'이 아니다 — 무엇이 보였는지 남기고 실패로 끝낸다
            print("❌ 약가 산정 버튼을 못 찾았다 — 아래가 그때 화면이다")
            print("로그인 했나:", logged)
            labs = [(l.inner_text() or "").replace("\n", " / ")
                    for l in find_all(pg, "[data-testid='stRadio']")]
            print("라디오:", labs or "(없음)")
            print("본문:", (text_of(pg) or "")[:1200])
            pg.screenshot(path="calc.png", full_page=True)
            br.close()
            return 1
        print("약가 산정 버튼: 눌렀다")
        time.sleep(15)

        body = text_of(pg)
        exc = find_all(pg, "[data-testid='stException'], .stException")
        charts = find_all(pg, "[data-testid='stPlotlyChart']")
        mets = find_all(pg, "[data-testid='stMetric']")
        bad = [w for w in ("Traceback", "TypeError", "KeyError", "NameError") if w in body]
        print(f"예외 {len(exc)}건 · 그래프 {len(charts)}개 · 지표 {len(mets)}개"
              + (f" · 오류흔적 {bad}" if bad else ""))
        for e in exc[:2]:
            try:
                print("---- 예외 본문 ----")
                print((e.inner_text() or "")[:800])
            except Exception:
                pass
        pg.screenshot(path="calc.png", full_page=True)
        br.close()
        if exc or bad:
            print("❌ 약가 산정 화면이 아직 깨져 있다")
            return 1
        if not charts or not mets:
            # 그래프도 지표도 없으면 화면이 안 그려진 것이다 — '정상'으로 치면 안 된다
            print("❌ 화면이 비어 있다(그래프·지표 없음) — 아래가 그때 본문이다")
            print((body or "")[:1200])
            return 1
        print("✅ 약가 산정 화면 정상")
        return 0


raise SystemExit(main())
