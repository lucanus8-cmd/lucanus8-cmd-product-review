"""심평원(건강보험심사평가원) 홈페이지의 '약제급여목록표' 게시판에서 최신 고시 엑셀을
로봇이 받을 수 있는지 본다. 공개 자료(보험 약가)만 다룬다. 매출 자료와는 상관없다.

1) 게시판 첫 쪽의 글 제목·날짜를 적는다.
2) 맨 위 글을 열어 첨부 파일 이름을 적는다.
3) 엑셀 첨부를 내려받아 머리글 줄·줄 수·칸 이름만 적는다(행 내용은 몇 줄만).
    python .github/scripts/check_hira_price.py
"""
import io
import os
import re
import sys
import time
import zipfile

from playwright.sync_api import sync_playwright

BOARDS = [u.strip() for u in (os.environ.get("URLS") or "").split(",") if u.strip()] or [
    "https://www.hira.or.kr/bbsDummy.do?pgmid=HIRAA030014050000",
]
OUT = os.environ.get("OUT_DIR") or "out"


def lines_of(pg):
    try:
        return [l.strip() for l in pg.inner_text("body").splitlines() if l.strip()]
    except Exception:
        return []


def inspect_excel(path):
    import pandas as pd
    try:
        raw = pd.read_excel(path, dtype=str, header=None)
    except Exception as e:
        print(f"   엑셀 읽기 실패: {str(e)[:160]}")
        return
    print(f"   첫 줄(제목): {' '.join(str(x) for x in raw.iloc[0].tolist() if str(x) != 'nan')[:120]}")
    hdr = next((i for i in range(min(10, len(raw)))
                if any("제품코드" in str(x) for x in raw.iloc[i].tolist())), None)
    if hdr is None:
        print("   '제품코드' 머리글 줄을 못 찾음")
        return
    cols = [re.sub(r"\s+", "", str(c)) for c in raw.iloc[hdr].tolist()]
    body = raw.iloc[hdr + 1:]
    print(f"   머리글 {hdr + 1}번째 줄 · 데이터 {len(body):,}줄 · 칸 {len(cols)}개")
    print(f"   칸: {' | '.join(cols)}")
    for _, r in body.head(3).iterrows():
        print("      " + " | ".join(str(x)[:20] for x in r.tolist()))


def handle_file(path):
    name = os.path.basename(path)
    print(f"   받은 파일: {name} · {os.path.getsize(path) / 1e6:.1f}MB")
    if name.lower().endswith(".zip"):
        with zipfile.ZipFile(path) as z:
            for n in z.namelist():
                print(f"     zip 안: {n}")
                if n.lower().endswith((".xlsx", ".xls")):
                    p = os.path.join(OUT, os.path.basename(n))
                    with open(p, "wb") as f:
                        f.write(z.read(n))
                    inspect_excel(p)
    elif name.lower().endswith((".xlsx", ".xls")):
        inspect_excel(path)


def board(pg, url, opened=False):
    print(f"\n===== {url}")
    if not opened:
        pg.goto(url, wait_until="domcontentloaded", timeout=120_000)
        time.sleep(6)
    print(f"   제목: {pg.title()}")
    # 게시판 글 목록(표의 줄)
    rows = pg.query_selector_all("table tbody tr")
    print(f"   글 줄 {len(rows)}개")
    for r in rows[:8]:
        print("      " + " | ".join(c.inner_text().strip().replace("\n", " ")[:50]
                                    for c in r.query_selector_all("td")))
    if not rows:
        for l in lines_of(pg)[:60]:
            print("      " + l[:120])
        return
    # 맨 위 글을 연다(공지 고정글이 아니라 제목에 '약제급여목록'이 든 첫 글)
    target = None
    for r in rows:
        a = r.query_selector("a")
        if a and ("약제급여" in (a.inner_text() or "") or "급여목록" in (a.inner_text() or "")):
            target = a
            break
    target = target or rows[0].query_selector("a")
    if not target:
        print("   글 링크가 없다")
        return
    print(f"   여는 글: {target.inner_text().strip()[:80]}")
    target.click()
    time.sleep(6)
    # 첨부 파일 이름·링크
    cands = []
    for a in pg.query_selector_all("a"):
        t = (a.inner_text() or "").strip()
        h = a.get_attribute("href") or ""
        oc = a.get_attribute("onclick") or ""
        if re.search(r"\.(xlsx|xls|zip|hwp|pdf)\b", t, re.I) or "down" in (h + oc).lower():
            cands.append((t[:80], (h or oc)[:120], a))
    print(f"   첨부로 보이는 링크 {len(cands)}개")
    for t, h, _ in cands[:12]:
        print(f"      {t} → {h}")
    os.makedirs(OUT, exist_ok=True)
    xl = [c for c in cands if re.search(r"\.(xlsx|xls|zip)\b", c[0], re.I)] or cands
    for t, h, a in xl[:2]:
        try:
            with pg.expect_download(timeout=120_000) as dl:
                a.click()
            d = dl.value
            p = os.path.join(OUT, d.suggested_filename)
            d.save_as(p)
            handle_file(p)
        except Exception as e:
            print(f"   '{t}' 내려받기 실패: {str(e)[:160]}")


KEYS = ("약제급여목록", "급여상한금액", "약제 급여 목록", "약가파일", "목록표", "약제급여")


def discover(pg, start):
    """첫 화면·메뉴에서 약제급여목록 관련 링크를 모은다."""
    print(f"\n----- 찾기: {start}")
    try:
        pg.goto(start, wait_until="domcontentloaded", timeout=120_000)
        time.sleep(6)
    except Exception as e:
        print(f"   열기 실패: {str(e)[:120]}")
        return []
    print(f"   제목: {pg.title()}")
    out = []
    for a in pg.query_selector_all("a"):
        try:
            t = " ".join((a.inner_text() or a.get_attribute("title") or "").split())
            h = a.get_attribute("href") or ""
        except Exception:
            continue
        if any(k in t for k in KEYS):
            if h.startswith("/"):
                h = re.match(r"https?://[^/]+", start).group(0) + h
            print(f"   {t[:60]} → {h[:160]}")
            if h.startswith("http") and h not in out:
                out.append(h)
    return out


def main():
    with sync_playwright() as p:
        br = p.chromium.launch(executable_path=os.environ.get("CHROMIUM_PATH") or None)
        ctx = br.new_context(viewport={"width": 1400, "height": 2600}, locale="ko-KR",
                             accept_downloads=True)
        pg = ctx.new_page()
        found = []
        if not os.environ.get("URLS"):
            # '목록표' 메뉴 화면의 하위 메뉴를 모두 적고, 약제급여목록표 게시판을 고른다
            pg.goto("https://www.hira.or.kr/bbsDummy.do?pgmid=HIRAA030019000000",
                    wait_until="domcontentloaded", timeout=120_000)
            time.sleep(6)
            subs = []
            for a in pg.query_selector_all("a"):
                try:
                    t = " ".join((a.inner_text() or "").split())
                    h = a.get_attribute("href") or ""
                except Exception:
                    continue
                if "pgmid=HIRAA0300" in h and t and (t, h) not in subs:
                    subs.append((t, h))
            print("\n----- 목록표 화면의 약제기준정보 메뉴")
            for t, h in subs:
                print(f"   {t[:50]} → {h[:120]}")
            # 주소로 바로 열면 400 이 나는 게시판이 있어, 메뉴를 눌러 들어간다
            link = pg.locator("a", has_text="약제급여목록표").first
            if link.count():
                link.click()
                time.sleep(7)
                board(pg, "메뉴 클릭 → 약제급여목록표 · " + pg.url, opened=True)
            else:
                print("   약제급여목록표 메뉴 링크가 없다")
            br.close()
            return 0
        for u in (found[:4] or BOARDS):
            try:
                board(pg, u)
            except Exception as e:
                print(f"{u}: 실패 {str(e)[:200]}")
        br.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
