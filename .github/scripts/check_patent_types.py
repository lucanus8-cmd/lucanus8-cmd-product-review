"""배포된 사이트의 특허 자료에 어떤 '특허유형' 값이 몇 건씩 있는지 센다.

특허 분석 화면의 '특허유형' 선택지는 실제 자료의 유형 값을 그대로 보여준다.
그 목록을 읽고, 값마다 하나씩 골라 '특허 건수'를 적는다. 상태는 두 번 센다.
  · 모든 상태
  · 등록(존속)만
유형이 빈 특허는 선택지에 없으므로, 전체 건수에서 유형별 합을 빼서 구한다.
읽기만 하므로 사이트는 바뀌지 않는다. 건수만 적는다.
"""
import os
import re
import sys
import time

from playwright.sync_api import sync_playwright

URL = (os.environ.get("APP_URL") or "").rstrip("/")
PW = os.environ.get("APP_PASSWORD") or ""
APP_SEL = ("[data-testid='stAppViewContainer']", "[data-testid='stApp']",
           "input[type='password']")


def app(pg):
    for fr in pg.frames:
        for sel in APP_SEL:
            try:
                if fr.query_selector(sel):
                    return fr
            except Exception:
                break
    return pg.main_frame


def body_text(pg):
    try:
        return app(pg).inner_text("body")
    except Exception:
        return ""


def wait_for(pg, word, tries=96, gap=5):
    for _ in range(tries):
        if word in body_text(pg):
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


def msel(pg, label):
    return app(pg).locator("[data-testid='stMultiSelect']").filter(has_text=label).first


def clear(pg, label):
    m = msel(pg, label)
    c = m.locator("[aria-label*='Clear'], [title*='Clear']")
    if c.count():
        c.first.click()
        settle(pg)


def settle(pg, gap=1.0, need=4, cap=180):
    """화면 글자가 더 이상 바뀌지 않을 때까지 기다린다."""
    last, same, t0 = None, 0, time.time()
    while time.time() - t0 < cap:
        b = body_text(pg)
        busy = app(pg).query_selector("[data-testid='stStatusWidget']")
        same = same + 1 if (b == last and not busy) else 0
        last = b
        if same >= need:
            return
        time.sleep(gap)


OPTS_JS = """
() => {
  const o = [...document.querySelectorAll("[role='option']")];
  if (!o.length) return {opts: [], h: -1};
  // 목록은 보이는 칸만 그린다 → 스크롤되는 상자를 찾아 한 화면씩 내린다
  let s = o[0].parentElement;
  while (s && !(s.scrollHeight > s.clientHeight + 2)) s = s.parentElement;
  const opts = o.map(e => e.innerText.trim());
  if (!s) return {opts, h: -1};
  const before = s.scrollTop;
  s.scrollTop = before + Math.max(s.clientHeight - 20, 40);
  return {opts, h: s.scrollTop === before ? -1 : s.scrollTop};
}
"""


def options(pg, label):
    """선택칸의 값을 끝까지 다 읽는다(목록이 10개만 보이므로 스크롤하며 모은다)."""
    m = msel(pg, label)
    m.click()
    time.sleep(2)
    seen, out = set(), []
    for _ in range(300):
        r = app(pg).evaluate(OPTS_JS)
        for o in r["opts"]:
            if o and o not in seen:
                seen.add(o)
                out.append(o)
        if r["h"] == -1:
            break
        time.sleep(0.3)
    pg.keyboard.press("Escape")
    time.sleep(1)
    # 이 버전의 선택칸은 맨 위에 '모두 고르기'를 붙인다 — 값이 아니므로 뺀다
    return [o for o in out if o.lower() not in ("select all", "모두 선택")]


def pick(pg, label, value):
    """값을 검색칸에 쳐서 고른다(목록에서 안 보이는 값도 고를 수 있게)."""
    m = msel(pg, label)
    m.click()
    time.sleep(1)
    pg.keyboard.type(value, delay=20)
    time.sleep(1.5)
    hit = None
    for o in app(pg).query_selector_all("[role='option']"):
        if (o.inner_text() or "").strip() == value:
            hit = o
            break
    if hit is None:
        pg.keyboard.press("Escape")
        return False
    hit.click()
    pg.keyboard.press("Escape")
    settle(pg)
    return True


def count(pg):
    b = body_text(pg)
    if "조건에 맞는 특허 없음" in b:
        return 0
    m = re.search(r"특허 건수\s*([\d,]+)", b)
    return int(m.group(1).replace(",", "")) if m else None


def main():
    if not URL:
        print("APP_URL 이 없다")
        return 1
    with sync_playwright() as p:
        br = p.chromium.launch(executable_path=os.environ.get("CHROMIUM_PATH") or None)
        pg = br.new_context(viewport={"width": 1600, "height": 2000},
                            locale="ko-KR").new_page()
        pg.goto(URL + "/?page=patent", wait_until="domcontentloaded", timeout=180_000)
        if not wait_for(pg, "특허 분석"):
            print("특허 분석 화면이 안 떴다")
            br.close()
            return 1
        settle(pg)

        clear(pg, "상태")              # 기본 선택('등록')이 있으면 목록에서 빠진다
        stats = options(pg, "상태")
        types = options(pg, "특허유형")
        print(f"상태 값 {len(stats)}가지: {' / '.join(stats)}")
        print(f"특허유형 값 {len(types)}가지: {' / '.join(types)}\n")

        res = {}
        for mode in ("모든 상태", "등록만"):
            clear(pg, "상태")
            if mode == "등록만":
                pick(pg, "상태", "등록")
            clear(pg, "특허유형")
            total = count(pg)
            rows = []
            for t in types:
                if not pick(pg, "특허유형", t):
                    rows.append((t, None))
                    continue
                rows.append((t, count(pg)))
                clear(pg, "특허유형")
            res[mode] = (total, rows)

        ta, ra = res["모든 상태"]
        tr, rr = res["등록만"]
        print(f"{'특허유형':<22}{'모든 상태':>10}{'등록만':>10}")
        print("-" * 42)
        for (t, a), (_, r) in zip(ra, rr):
            print(f"{t:<22}{(a if a is not None else '?'):>10}{(r if r is not None else '?'):>10}")
        sa = sum(a for _, a in ra if a)
        sr = sum(r for _, r in rr if r)
        print("-" * 42)
        print(f"{'유형 값 있는 것 합':<22}{sa:>10}{sr:>10}")
        print(f"{'전체':<22}{(ta if ta is not None else '?'):>10}{(tr if tr is not None else '?'):>10}")
        if ta is not None and tr is not None:
            print(f"{'유형 빈 것 (전체-합)':<22}{ta - sa:>10}{tr - sr:>10}")
        ex = app(pg).query_selector_all("[data-testid='stException']")
        if ex:
            print("\n화면에 예외:", (ex[0].inner_text() or "")[:300])
        br.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
