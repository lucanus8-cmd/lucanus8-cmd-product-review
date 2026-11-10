"""임상 자료의 '질환 칸'과 '성분명 칸'이 실제로 쓸 만한지 센다.

왜 필요한가: 단계별 경쟁 신호 화면을 만들지 말지는 아래 비율이 가른다.
  - 생동에서 성분명이 채워진 비율  → 성분별 추이가 성립하나
  - 질환 칸의 값 종류 수          → 코드처럼 정해진 값인가, 자유 입력인가
자료가 배포 서버 안에만 있어 밖에서 셀 수 없으므로, 임상 분석 화면의
'전체 CSV 내려받기'를 로봇이 눌러 받아 센다. 집계 숫자만 로그로 남기고
원자료는 남기지 않는다. 작업 브랜치 전용 — 사이트 동작과 무관하다.
"""
import os
import time

import pandas as pd
from playwright.sync_api import sync_playwright

URL = (os.environ.get("APP_URL") or "").rstrip("/")
PW = os.environ.get("APP_PASSWORD") or ""
APP_SEL = ("[data-testid='stApp']", "[data-testid='stAppViewContainer']",
           "[data-testid='stMain']", "input[type='password']", "section.main")
OUT = "clinical.csv"


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
    """임상 분석 화면에서 전체 CSV 를 받아 파일로 저장. 성공하면 True."""
    with sync_playwright() as pw:
        br = pw.chromium.launch()
        ctx = br.new_context(viewport={"width": 1500, "height": 1200},
                             locale="ko-KR", accept_downloads=True)
        pg = ctx.new_page()
        pg.goto(URL + "/?page=clinical", wait_until="domcontentloaded", timeout=120_000)

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
            if any("CSV" in (b.inner_text() or "") for b in find_all(pg, "button")):
                break
            time.sleep(2)

        btn = None
        for b in find_all(pg, "button"):
            try:
                t = b.inner_text() or ""
                if "CSV" in t and "전체" in t:
                    btn = b
                    print("내려받기 단추:", t.strip()[:40])
                    break
            except Exception:
                pass
        if btn is None:
            print("내려받기 단추를 못 찾았다 — 그때 화면:")
            print((text_of(pg) or "")[:900])
            br.close()
            return False

        with pg.expect_download(timeout=180_000) as dl:
            btn.click()
        dl.value.save_as(OUT)
        br.close()
        return True


def pct(n, d):
    return f"{100*n/d:5.1f}%" if d else "    -"


def col_report(df, col, label):
    if col not in df.columns:
        print(f"  [{label}] 열 없음")
        return
    s = df[col].astype("object").where(df[col].notna(), "").astype(str).str.strip()
    s = s.mask(s.str.lower().isin(["nan", "none", "<na>"]), "")
    filled = s[s != ""]
    uniq = filled.nunique()
    multi = int(filled.str.contains(",").sum())
    print(f"  [{label}]")
    print(f"      채워짐 {len(filled):>7,}/{len(s):,} ({pct(len(filled), len(s))})"
          f" · 값 종류 {uniq:,}개 · 쉼표로 여러 개 {multi:,}건 ({pct(multi, len(filled))})")
    if uniq:
        top = filled.value_counts().head(8)
        print(f"      많은 값: " + " · ".join(f"{k}({v:,})" for k, v in top.items())[:300])
        # 값 종류가 많은지 적은지 가르는 지표: 1건짜리 값의 비율
        vc = filled.value_counts()
        once = int((vc == 1).sum())
        print(f"      1건뿐인 값 {once:,}개 / {uniq:,}종  → "
              + ("자유 입력에 가깝다" if uniq and once / uniq > 0.5 else "정해진 값에 가깝다"))


def main():
    if not fetch():
        return 1
    df = pd.read_csv(OUT, dtype=str, keep_default_na=False)
    print(f"\n받은 행 {len(df):,} · 열 {list(df.columns)}\n")

    step = "단계"
    if step in df.columns:
        print("── 단계 전체 분포 (상위 15개가 아닌 전부) ──")
        vc = df[step].astype(str).str.strip().replace("", "(공백)").value_counts()
        for k, v in vc.items():
            print(f"  {k:<18}{v:>8,}")
        print(f"  합계 {vc.sum():,} · 값 종류 {len(vc)}개\n")

    for name, sel in [("전체", df),
                      ("생동", df[df.get(step, pd.Series("", index=df.index))
                                 .astype(str).str.contains("생동", na=False)])]:
        print(f"── {name} {len(sel):,}건 ──")
        col_report(sel, "성분명", "성분명")
        col_report(sel, "대상질환", "대상질환 (TRGT_DISS_NM)")
        col_report(sel, "대상질환(세부)", "대상질환 세부 (TRGT_DISS_DTL_CD_NM)")
        print()
    return 0


raise SystemExit(main())
