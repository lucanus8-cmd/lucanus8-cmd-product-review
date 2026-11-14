"""식약처 의약품안전나라에서 DMF(원료의약품 등록) 목록 화면을 찾아 읽어 본다.

공개 화면을 읽기만 한다. 제품 검토 사이트와는 상관없다.
1) 첫 화면·메뉴에서 'DMF'·'원료의약품' 이 들어간 링크를 모은다.
2) 그 링크를 열어 표가 있는지, 몇 줄인지, 맨 위 몇 줄이 무엇인지 적는다.
3) 표가 있으면 검색 칸·쪽 넘김·내려받기 단추가 있는지도 적는다.
    python .github/scripts/check_nedrug_dmf.py
"""
import os
import re
import sys
import time

from playwright.sync_api import sync_playwright

BASE = "https://nedrug.mfds.go.kr"
START = [BASE + "/index", BASE + "/pbp/CCBAA01", BASE + "/sitemap"]
WORDS = ("DMF", "원료의약품", "원료약")
EXTRA = [u.strip() for u in (os.environ.get("URLS") or "").split(",") if u.strip()]


def links(pg):
    out = []
    for a in pg.query_selector_all("a"):
        try:
            t = (a.inner_text() or "").strip().replace("\n", " ")
            h = a.get_attribute("href") or ""
        except Exception:
            continue
        if any(w in t for w in WORDS) or "dmf" in h.lower():
            if h.startswith("/"):
                h = BASE + h
            if h.startswith("http") and (t, h) not in out:
                out.append((t[:60], h))
    return out


def look(pg, url):
    print(f"\n===== {url}")
    try:
        pg.goto(url, wait_until="domcontentloaded", timeout=90_000)
    except Exception as e:
        print(f"   열기 실패: {str(e)[:160]}")
        return
    time.sleep(6)
    txt = pg.inner_text("body")
    title = (pg.title() or "").strip()
    print(f"   제목: {title} · 글자 {len(txt):,}자")
    m = re.search(r"(총|전체)\s*[:：]?\s*([\d,]+)\s*건", txt)
    if m:
        print(f"   건수 표시: {m.group(0)}")
    tables = pg.query_selector_all("table")
    for i, tb in enumerate(tables[:4]):
        hdr = [h.inner_text().strip() for h in tb.query_selector_all("th")][:14]
        rows = tb.query_selector_all("tbody tr")
        print(f"   표{i + 1}: {len(rows)}줄 · 머리: {' | '.join(hdr)}")
        for r in rows[:5]:
            cells = [c.inner_text().strip().replace("\n", " ")[:30] for c in r.query_selector_all("td")]
            print("      " + " | ".join(cells))
    btn = [b.strip() for b in re.findall(r"(엑셀[^\n]{0,10}|다운로드[^\n]{0,10}|검색)", txt)][:8]
    print(f"   단추 글자: {sorted(set(btn))}")
    pages = pg.query_selector_all("[class*='paging'] a, .pagination a")
    print(f"   쪽 넘김 링크 {len(pages)}개")
    forms = pg.query_selector_all("form")
    for f in forms[:3]:
        act = f.get_attribute("action") or ""
        names = [e.get_attribute("name") for e in f.query_selector_all("input, select")]
        names = [n for n in names if n][:20]
        if act or names:
            print(f"   form → {act} · 칸: {names}")


def main():
    with sync_playwright() as p:
        br = p.chromium.launch(executable_path=os.environ.get("CHROMIUM_PATH") or None)
        pg = br.new_context(viewport={"width": 1400, "height": 3000}, locale="ko-KR").new_page()
        found = []
        for u in START:
            try:
                pg.goto(u, wait_until="domcontentloaded", timeout=90_000)
                time.sleep(5)
            except Exception as e:
                print(f"{u}: 열기 실패 {str(e)[:120]}")
                continue
            got = links(pg)
            print(f"{u} → DMF·원료 링크 {len(got)}개")
            for t, h in got:
                print(f"   {t} → {h}")
                if h not in found:
                    found.append(h)
        for u in (EXTRA + found)[:12]:
            look(pg, u)
        br.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
