"""배포된 사이트에서 특정 성분의 연도별 약가를 받아, 30% 이상 인하가 있었는지 센다.

왜: "약가코드에 품목이 여럿이면 이미 조정된 것 아니냐"를 확인하려면 가격 이력이
필요하다. 조정이 일어났다면 어느 해에 30% 안팎으로 뚝 떨어진 자국이 남는다.
로컬 약가 파일은 연습용이라 이 신호가 없어 밖에서 확인할 수 없다.

사이트는 바꾸지 않는다 — 검색창에 입력하고 CSV 단추를 누를 뿐이다.
집계만 로그에 남기고 원자료는 남기지 않는다. 작업 브랜치 전용.
"""
import os
import time

import pandas as pd
from playwright.sync_api import sync_playwright

URL = (os.environ.get("APP_URL") or "").rstrip("/")
PW = os.environ.get("APP_PASSWORD") or ""
TERM = os.environ.get("DRUG") or "아토르바스타틴"
APP_SEL = ("[data-testid='stApp']", "[data-testid='stAppViewContainer']",
           "[data-testid='stMain']", "input[type='password']", "section.main")
OUT = "yearly.csv"


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


def fetch():
    with sync_playwright() as pw:
        br = pw.chromium.launch()
        ctx = br.new_context(viewport={"width": 1500, "height": 1300},
                             locale="ko-KR", accept_downloads=True)
        pg = ctx.new_page()
        pg.goto(URL + "/?page=price", wait_until="domcontentloaded", timeout=120_000)
        for _ in range(60):
            if app_frame(pg):
                break
            time.sleep(2)
        if not app_frame(pg):
            print("앱 화면을 못 찾았다")
            br.close()
            return False
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
            if find_all(pg, "[data-testid='stTextInput']"):
                break
            time.sleep(2)

        # '제품/성분 검색' 칸에 성분명을 넣어 400개 이하로 좁힌다
        box = None
        for w in find_all(pg, "[data-testid='stTextInput']"):
            try:
                if "제품/성분" in (w.inner_text() or ""):
                    box = w.query_selector("input")
                    break
            except Exception:
                pass
        if box is None:
            print("제품/성분 검색 칸을 못 찾았다")
            print((text_of(pg) or "")[:600])
            br.close()
            return False
        box.fill(TERM)
        box.press("Enter")
        time.sleep(18)

        btn = None
        for b in find_all(pg, "button"):
            try:
                if "연도별 약가 CSV" in (b.inner_text() or ""):
                    btn = b
                    break
            except Exception:
                pass
        if btn is None:
            print("연도별 약가 CSV 단추가 없다 — 그때 화면:")
            body = text_of(pg) or ""
            idx = body.find("연도별 상한금액")
            print(body[max(0, idx - 200):idx + 400] if idx >= 0 else body[:700])
            br.close()
            return False
        with pg.expect_download(timeout=180_000) as dl:
            btn.click()
        dl.value.save_as(OUT)
        br.close()
        return True


def main():
    if not fetch():
        return 1
    df = pd.read_csv(OUT, dtype=str, keep_default_na=False)
    print(f"\n받은 표 {df.shape[0]:,}행 · 열 {list(df.columns)[:10]}")

    years = [c for c in df.columns if str(c).strip().isdigit()]
    years.sort()
    if len(years) < 2:
        print("연도 열이 2개 미만이라 비교할 수 없다")
        return 1
    print(f"연도 열 {len(years)}개: {years[0]} ~ {years[-1]}\n")

    v = df[years].apply(pd.to_numeric, errors="coerce")
    name_col = next((c for c in df.columns if "제품" in str(c)), df.columns[0])

    rows, big = [], []
    for i in range(len(df)):
        s = v.iloc[i]
        prev_y, prev_x = None, None
        for y in years:
            x = s[y]
            if pd.isna(x) or x <= 0:          # 미등재(빈칸)·급여삭제(0)는 건너뛴다
                continue
            if prev_x is not None and x < prev_x:
                r = (x - prev_x) / prev_x
                rows.append(r)
                if r <= -0.30:
                    big.append((df[name_col].iloc[i], prev_y, y, prev_x, x, r))
            prev_y, prev_x = y, x

    print(f"연도 간 하락 {len(rows):,}건 · 그중 30% 이상 {len(big):,}건")
    if rows:
        q = pd.Series(rows)
        print("하락률 분포:  " + " · ".join(
            f"{int(p*100)}%지점 {q.quantile(p)*100:.1f}%" for p in (0.05, 0.25, 0.5, 0.75, 0.95)))
    if big:
        yc = pd.Series([b[2] for b in big]).value_counts().sort_index()
        print("\n30% 이상 인하가 일어난 해")
        for y, n in yc.items():
            print(f"   {y}  {n:>5,}건  {'█' * max(1, int(n / max(yc) * 32))}")
        print("\n예시 8건")
        for nm, y0, y1, p0, p1, r in big[:8]:
            print(f"   {str(nm)[:30]:<32}{y0}→{y1}  {p0:>8,.0f} → {p1:>8,.0f}원  {r*100:>6.1f}%")
    else:
        print("\n30% 이상 인하가 한 건도 없다 — 연도말 기준이라 같은 해 안의 인하는 묻힐 수 있다.")
    return 0


raise SystemExit(main())
