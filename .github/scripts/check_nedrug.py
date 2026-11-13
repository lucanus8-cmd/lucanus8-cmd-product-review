"""식약처 의약품안전나라에서 품목 상세 화면을 열어 '특허'가 나오는 부분을 적는다.

공개 화면을 읽기만 한다. 제품 검토 사이트와는 상관없다.
    ITEM_SEQ=201310988 python .github/scripts/check_nedrug.py
"""
import os
import re
import sys
import time

from playwright.sync_api import sync_playwright

SEQS = [s.strip() for s in (os.environ.get("ITEM_SEQ") or "").split(",") if s.strip()]


def dump(pg, seq):
    url = f"https://nedrug.mfds.go.kr/pbp/CCBBB01/getItemDetail?itemSeq={seq}"
    pg.goto(url, wait_until="domcontentloaded", timeout=90_000)
    time.sleep(6)
    txt = pg.inner_text("body")
    title = re.search(r"제품명\s*\n?\s*([^\n]+)", txt)
    print(f"\n===== {seq} · {title.group(1).strip() if title else '?'} · 글자 {len(txt):,}자")
    lines = [l.strip() for l in txt.splitlines() if l.strip()]
    hits = [i for i, l in enumerate(lines) if "특허" in l]
    print(f"'특허' 나오는 줄 {len(hits)}개")
    shown = set()
    for i in hits:
        for j in range(max(0, i - 3), min(len(lines), i + 40)):
            if j not in shown:
                shown.add(j)
                print(f"   {lines[j][:160]}")
        if len(shown) > 260:
            break
    links = sorted({a for a in re.findall(r'href="([^"]*)"', pg.content()) if "atent" in a or "특허" in a})
    if links:
        print("특허 관련 링크:", links[:10])


def main():
    if not SEQS:
        print("ITEM_SEQ 가 없다")
        return 1
    with sync_playwright() as p:
        br = p.chromium.launch(executable_path=os.environ.get("CHROMIUM_PATH") or None)
        pg = br.new_context(viewport={"width": 1400, "height": 3000}, locale="ko-KR").new_page()
        for s in SEQS:
            try:
                dump(pg, s)
            except Exception as e:
                print(f"{s}: 실패 {str(e)[:200]}")
        br.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
