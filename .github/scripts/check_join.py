"""배포된 사이트에서 '허가 자료 ↔ 약가' 연결이 살아 있는지 확인한다.

왜: 약가 변동 그래프의 초록 세모(동일성분 등재/허가)가 안 나온다는 제보가 있다.
세모가 그려지려면 약가 행의 성분키가 허가 행의 성분키와 맞아야 하는데,
로컬 자료에서는 약가 쪽 _key 가 100% 비어 있었다. 실제도 그런지 본다.

읽는 것만 한다. 입력하고 탭을 누를 뿐, 사이트는 바꾸지 않는다.
집계와 범례 이름만 로그에 남기고 원자료는 남기지 않는다. 작업 브랜치 전용.
"""
import os
import re
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


def any_plot(page):
    for fr in page.frames:
        try:
            if fr.query_selector(".js-plotly-plot"):
                return True
        except Exception:
            pass
    return False


def trace_names(page):
    """그래프에 실제로 들어간 선·표식 이름을 Plotly 객체에서 직접 읽는다.
    범례 글자를 DOM 에서 긁는 방식은 이 화면에서 아무것도 못 잡았다."""
    js = """() => {
      const out = [];
      document.querySelectorAll('.js-plotly-plot').forEach(p => {
        (p.data || []).forEach(t => out.push(t.name || '(이름없음)'));
      });
      return out;
    }"""
    for fr in page.frames:
        try:
            got = fr.evaluate(js)
        except Exception:
            continue
        if got:
            return got
    return []


def probe(pg, term):
    print("\n" + "=" * 68)
    print(f"성분: {term}")
    print("=" * 68)

    if not click_label(pg, "주성분"):
        print("  '주성분' 기준 칸을 못 찾았다")
        return
    time.sleep(6)

    box = widget_with(pg, "stTextInput", "검색어 입력")
    if box is None:
        print("  검색어 칸을 못 찾았다")
        return
    inp = box.query_selector("input")
    inp.fill(term)
    inp.press("Enter")
    time.sleep(12)

    sb = widget_with(pg, "stSelectbox", "허가 주성분 선택")
    if sb is None:
        print("  성분 드롭다운을 못 찾았다")
        return
    try:
        print("  드롭다운 머리글:", (sb.inner_text() or "").splitlines()[0][:70])
    except Exception:
        pass

    # 드롭다운을 열고 아래 화살표로 첫 후보를 고른다(타자 + Enter 가 안 먹었다).
    # 이 화면의 드롭다운은 react-aria ComboBox 라 항목이 [role='option'] 이다.
    # ('li[role=option]' 은 이 구조에 없어서 앞서 아무것도 못 읽었다.)
    sb.click()
    time.sleep(3)
    cands = []
    for o in find_all(pg, "[role='option']"):
        try:
            t = (o.inner_text() or "").strip()
        except Exception:
            continue
        if t and "선택하세요" not in t:
            cands.append((t, o))
    if not cands:
        print(f"  '{term}' 로 후보가 안 나온다 — 목록은 한글 주성분 기준이다")
        pg.keyboard.press("Escape")
        return
    # 복합제('메트포르민염산염|아토르바스타틴')가 아니라 단일 성분을 고른다.
    pick = (next((c for c in cands if c[0] == term), None)
            or next((c for c in cands if "|" not in c[0]), None)
            or cands[0])
    val, el = pick
    el.click()
    print(f"  고른 성분: {val[:48]}  (후보 {len(cands)}개)")

    # 성분을 고르면 탭이 생긴다. 실제 사이트는 느리니 떠오를 때까지 기다린다.
    for _ in range(15):
        if len(find_all(pg, "[role='tab']")) >= 5:
            break
        time.sleep(10)
    else:
        print("  성분을 골라도 탭이 안 생겼다 (2분 반 기다림)")
        return
    time.sleep(6)

    body = text_of(pg) or ""
    mm = re.search(r"허가 [\d,]+건[^\n]*", body)
    print(f"  허가 탭 줄: {mm.group(0)[:70] if mm else '(없음)'}")
    if "선택하세요" in body:
        print("  아직 '(선택하세요)' 상태 — 성분이 안 골라졌다")
        return

    moved = False
    for t in find_all(pg, "[role='tab'], [data-testid='stTab']"):
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
    # 이 그래프는 동일성분 품목마다 회색 선을 하나씩 그려서 아주 느리다.
    # 로컬(품목 250개 남짓)에서 80초가 걸렸다. 넉넉히 기다린다.
    t0 = time.time()
    drew = False
    for _ in range(18):
        if any_plot(pg):
            drew = True
            break
        time.sleep(10)
    print(f"  그래프 그리기: {'끝남' if drew else '3분 안에 안 끝남'} "
          f"({time.time() - t0:.0f}초)")

    sb2 = widget_with(pg, "stSelectbox", "약가 추이를 볼 품목")
    if sb2:
        try:
            i3 = sb2.query_selector("input")
            print("  자동 선택된 품목:", (i3.get_attribute("value") or "(빈칸)")[:44] if i3 else "(못 읽음)")
        except Exception:
            pass
    else:
        print("  품목 드롭다운이 없다")

    names = trace_names(pg)
    named = [n for n in names if n and n != "(이름없음)"]
    print(f"  그래프 선 {len(names)}개 (이름 있는 것 {len(named)}개): {named[:6]}")
    hit = any("등재" in n or "허가" in n for n in names)
    print(f"  >>> 초록 세모(동일성분 등재·허가): "
          f"{'있다 — 연결 살아 있음' if hit else '없다 — 연결 끊김 또는 그래프 자체가 없음'}")

    b2 = text_of(pg) or ""
    for mark in ("최초 급여가", "물질특허만료", "용도특허만료", "급여삭제", "가격 인하 시점 전후 사실"):
        print(f"      {mark}: {'보임' if mark in b2 else '안 보임'}")


def main():
    with sync_playwright() as pw:
        _exe = os.environ.get("PW_CHROME")   # 로컬에서 미리 깔린 브라우저를 쓸 때
        br = pw.chromium.launch(executable_path=_exe, args=["--no-sandbox"]) if _exe \
            else pw.chromium.launch()
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
