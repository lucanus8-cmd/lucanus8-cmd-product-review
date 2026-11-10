"""'질환 분류(TRGT_DISS_CD_NM)' 칸이 쓸 만한지 검색으로 떠본다.

그 칸은 화면에 안 나오고 CSV 에도 없어서 밖에서 셀 수 없다. 다만 임상 분석의
'적응증 키워드' 검색이 그 칸까지 뒤지므로, 후보 분류명을 하나씩 넣어보고
건수를 읽으면 대략을 알 수 있다.

주의: 그 검색은 시험 제목·목적도 같이 뒤진다. 영문 제목에 같은 단어가 있으면
함께 걸리므로 숫자는 '최대값'으로 읽어야 한다. 쓸 만한지 가늠하는 용도다.
사이트는 전혀 바꾸지 않는다 — 검색창에 입력만 한다.
"""
import os
import time

from playwright.sync_api import sync_playwright

URL = (os.environ.get("APP_URL") or "").rstrip("/")
PW = os.environ.get("APP_PASSWORD") or ""
APP_SEL = ("[data-testid='stApp']", "[data-testid='stAppViewContainer']",
           "[data-testid='stMain']", "input[type='password']", "section.main")

WORDS = ["Cardiovascular", "Oncology", "Neurology", "Psychiatry", "Endocrine",
         "Infection", "Respiratory", "Gastrointestinal", "Dermatology",
         "Immunology", "Ophthalmology", "Urology", "Musculoskeletal",
         "Hematology", "Metabolic", "Vaccine", "Others", "ZZZQQQNOPE"]


def find_all(page, sel):
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
        for sel in APP_SEL:
            try:
                if fr.query_selector(sel):
                    return fr
            except Exception:
                break
    return None


def settle(page, timeout=40):
    """스트림릿이 다시 그리는 중이면 끝날 때까지 기다린다."""
    t0 = time.time()
    time.sleep(1.2)                       # 입력이 서버에 닿을 틈
    while time.time() - t0 < timeout:
        if not find_all(page, "[data-testid='stStatusWidget']"):
            time.sleep(1.0)
            if not find_all(page, "[data-testid='stStatusWidget']"):
                return True
        time.sleep(0.6)
    return False


def count(page):
    """첫 지표 '임상 건수' 값을 숫자로."""
    els = find_all(page, "[data-testid='stMetricValue']")
    if not els:
        return None
    try:
        return int((els[0].inner_text() or "").replace(",", "").strip())
    except Exception:
        return None


def main():
    if not URL:
        print("APP_URL 없음")
        return 1
    with sync_playwright() as pw:
        br = pw.chromium.launch()
        pg = br.new_page(viewport={"width": 1500, "height": 1200}, locale="ko-KR")
        pg.goto(URL + "/?page=clinical", wait_until="domcontentloaded", timeout=120_000)

        for _ in range(60):
            if app_frame(pg):
                break
            time.sleep(2)
        if not app_frame(pg):
            print("앱 화면을 못 찾았다")
            br.close()
            return 1
        time.sleep(8)

        logged = False
        for _ in range(60):
            if not logged and PW:
                box = find_one(pg, "input[type='password']")
                if box:
                    box.fill(PW)
                    box.press("Enter")
                    logged = True
                    print("로그인했다")
                    time.sleep(12)
                    continue
            if count(pg) is not None:
                break
            time.sleep(2)

        base = count(pg)
        print(f"필터 없음(전체): {base:,}건\n" if base else "지표를 못 읽었다\n")
        if base is None:
            print((text_of(pg) or "")[:600])
            br.close()
            return 1

        kw = None
        for inp in find_all(pg, "input[type='text']"):
            try:
                ph = inp.get_attribute("placeholder") or ""
                if "비만" in ph or "쉼표" in ph:
                    kw = inp
                    break
            except Exception:
                pass
        if kw is None:
            print("적응증 키워드 칸을 못 찾았다")
            br.close()
            return 1

        print(f"{'후보 분류명':<20}{'건수':>9}{'전체 대비':>10}")
        print("-" * 40)
        for w in WORDS:
            try:
                kw.fill("")
                kw.press("Enter")
                settle(pg)
                kw.fill(w)
                kw.press("Enter")
                settle(pg)
                n = count(pg)
                pctv = f"{100*n/base:5.1f}%" if (n is not None and base) else "    -"
                print(f"{w:<20}{(f'{n:,}' if n is not None else '?'):>9}{pctv:>10}")
            except Exception as e:
                print(f"{w:<20}{'실패':>9}  {type(e).__name__}")
        br.close()
    return 0


raise SystemExit(main())
