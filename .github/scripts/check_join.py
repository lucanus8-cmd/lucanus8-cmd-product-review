"""배포된 사이트에서 '허가 자료 ↔ 약가' 연결이 살아 있는지 확인한다.

왜: 약가 변동 그래프의 초록 세모(동일성분 등재/허가)가 안 나온다는 제보가 있다.
세모가 그려지려면 약가 행의 성분키가 허가 행의 성분키와 맞아야 하는데,
로컬 자료에서는 약가 쪽 _key 가 100% 비어 있었다. 실제도 그런지 본다.

읽는 것만 한다. 입력하고 탭을 누를 뿐, 사이트는 바꾸지 않는다.
집계와 범례 이름만 로그에 남기고 원자료는 남기지 않는다. 작업 브랜치 전용.
"""
import os
import time

from playwright.sync_api import sync_playwright

URL = (os.environ.get("APP_URL") or "").rstrip("/")
PW = os.environ.get("APP_PASSWORD") or ""
TERMS = [t.strip() for t in (os.environ.get("DRUGS") or "아토르바스타틴,로수바스타틴").split(",") if t.strip()]
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


def widget_with(page, testid, label):
    """라벨 글자가 들어 있는 위젯을 고른다."""
    for w in find_all(page, f"[data-testid='{testid}']"):
        try:
            if label in (w.inner_text() or ""):
                return w
        except Exception:
            pass
    return None


def click_label(page, text):
    for lb in find_all(page, "label"):
        try:
            if text in (lb.inner_text() or ""):
                lb.click()
                return True
        except Exception:
            pass
    return False


def legend_names(page):
    """Plotly 범례에 적힌 이름들."""
    out = []
    for e in find_all(page, "g.legend text, .legendtext"):
        try:
            t = (e.inner_text() or "").strip()
        except Exception:
            continue
        if t:
            out.append(t)
    return out


def probe(pg, term):
    print("\n" + "=" * 68)
    print(f"성분: {term}")
    print("=" * 68)

    # 검색 기준을 '주성분'으로
    if not click_label(pg, "주성분"):
        print("  '주성분' 기준 칸을 못 찾았다 — 그때 화면 앞부분:")
        print("    " + (text_of(pg) or "(빈 화면)")[:500].replace("\n", " | "))
        print("    라디오 칸들:", [
            (w.inner_text() or "").replace("\n", "/")[:60]
            for w in find_all(pg, "[data-testid='stRadio']")][:4])
        return
    time.sleep(6)

    box = widget_with(pg, "stTextInput", "검색어 입력")
    if box is None:
        print("  검색어 칸을 못 찾았다")
        return
    inp = box.query_selector("input")
    inp.fill(term)
    inp.press("Enter")
    time.sleep(10)

    # 드롭다운에서 첫 후보를 고른다
    sb = widget_with(pg, "stSelectbox", "허가 주성분 선택")
    if sb is None:
        print("  성분 드롭다운을 못 찾았다")
        return
    sb.click()
    time.sleep(3)
    opts = find_all(pg, "li[role='option'], [data-baseweb='menu'] li")
    picked = None
    for o in opts:
        try:
            t = (o.inner_text() or "").strip()
        except Exception:
            continue
        if t and "선택하세요" not in t:
            picked = t
            o.click()
            break
    if picked is None:
        print("  드롭다운에 후보가 없다 — 허가 자료에 이 성분이 없다는 뜻")
        pg.keyboard.press("Escape")
        return
    print(f"  고른 성분: {picked[:48]}")
    time.sleep(14)

    body = text_of(pg)
    i = body.find("허가 ")
    if i >= 0:
        print(f"  허가 탭 요약: {body[i:i+60].splitlines()[0]}")
    else:
        print("  허가 탭 요약을 못 읽었다")

    # 약가·이벤트 탭으로
    moved = False
    for t in find_all(pg, "[data-baseweb='tab'], button[role='tab']"):
        try:
            if "약가" in (t.inner_text() or ""):
                t.click()
                moved = True
                break
        except Exception:
            pass
    if not moved:
        print("  약가·이벤트 탭을 못 찾았다")
        return
    time.sleep(16)

    # 오리지널이 자동으로 골라졌나
    sb2 = widget_with(pg, "stSelectbox", "약가 추이를 볼 품목")
    if sb2:
        try:
            cur = (sb2.inner_text() or "").replace("약가 추이를 볼 품목", "").strip()
            print(f"  자동 선택된 품목: {cur.splitlines()[0][:44] if cur else '(빈칸)'}")
        except Exception:
            pass
    else:
        print("  품목 드롭다운이 없다 (제품 기준으로 열렸거나 자료 없음)")

    names = legend_names(pg)
    print(f"  그래프 범례: {names if names else '(없음)'}")
    hit = any("등재" in n or "허가" in n for n in names)
    print(f"  >>> 초록 세모(동일성분 등재·허가): {'있다  — 연결 살아 있음' if hit else '없다  — 연결 끊김'}")

    b2 = text_of(pg)
    for mark in ("물질특허만료", "용도특허만료", "공단 약가협상", "급여삭제"):
        print(f"      {mark}: {'보임' if mark in b2 else '안 보임'}")
    if "가격 인하 시점 전후 사실" in b2:
        print("      인하 시점 표: 보임")


def main():
    with sync_playwright() as pw:
        br = pw.chromium.launch()
        ctx = br.new_context(viewport={"width": 1600, "height": 1500}, locale="ko-KR")
        pg = ctx.new_page()
        pg.goto(URL + "/?page=search", wait_until="domcontentloaded", timeout=120_000)
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
                b = find_one(pg, "input[type='password']")
                if b:
                    b.fill(PW)
                    b.press("Enter")
                    logged = True
                    print("로그인했다")
                    time.sleep(14)
                    continue
            if any("검색 기준" in (w.inner_text() or "")
                   for w in find_all(pg, "[data-testid='stRadio']")):
                break
            time.sleep(2)
        else:
            print("검색 화면이 끝내 안 떴다 — 홈에 머물러 있을 수 있다")
            print("  지금 화면:", (text_of(pg) or "")[:300].replace("\n", " | "))

        for term in TERMS:
            try:
                probe(pg, term)
            except Exception as e:
                print(f"  {term}: 확인 중 오류 — {str(e)[:160]}")
        br.close()
    return 0


raise SystemExit(main())
