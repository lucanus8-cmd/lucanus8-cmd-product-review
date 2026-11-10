"""임상 자료의 '질환 칸'과 '성분명 칸'이 실제로 쓸 만한지 센다.

왜 필요한가: 단계별 경쟁 신호 화면을 만들지 말지는 아래 비율이 가른다.
  - 생동에서 성분명이 채워진 비율  → 성분별 추이가 성립하나
  - 질환 칸의 값 종류 수          → 코드처럼 정해진 값인가, 자유 입력인가
자료가 배포 서버 안에만 있어 밖에서 셀 수 없으므로, 임상 분석 화면의
'전체 CSV 내려받기'를 로봇이 눌러 받아 센다. 집계 숫자만 로그로 남기고
원자료는 남기지 않는다. 작업 브랜치 전용 — 사이트 동작과 무관하다.
"""
import os
import re
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


def bucket(v):
    """단계 값을 바구니로. 글자 모양으로 판단한다."""
    t = str(v).strip()
    if "생동" in t:
        return "생동"
    if "연구자" in t:
        return "(제외) 연구자"
    if "연장" in t:
        return "(제외) 연장"
    m = re.fullmatch(r"(\d)[ab]?상", t)
    if m:
        return f"{m.group(1)}상"
    if re.fullmatch(r"\d[ab]?/\d[ab]?상", t):
        return re.sub(r"[ab]", "", t)      # 1/2a상 → 1/2상
    return "미분류"


CODEY = re.compile(r"[A-Za-z]")
DIGIT = re.compile(r"\d")


def looks_like_code(v):
    """한글이 하나도 없는 이름인가. 개발코드(DS-8201a, HCP1701)와
    영문 성분명(nivolumab)이 함께 걸린다 — 둘 다 한글 주성분명과 바로
    못 잇는다는 점은 같아서 하나로 센다. 예시를 같이 뽑아 구분한다."""
    t = str(v).strip()
    if not t:
        return False
    has_kor = any("\uac00" <= ch <= "\ud7a3" for ch in t)
    return (not has_kor) and bool(CODEY.search(t))


def main():
    if not fetch():
        return 1
    df = pd.read_csv(OUT, dtype=str, keep_default_na=False)
    print(f"\n받은 행 {len(df):,}\n")
    df["_b"] = df["단계"].map(bucket) if "단계" in df.columns else "?"

    s = df["성분명"].astype(str).str.strip()
    df["_blank"] = s == ""
    df["_code"] = s.map(looks_like_code)
    df["_multi"] = s.str.contains(",")

    print("── 바구니별 성분명 상태 ──")
    print(f"{'바구니':<14}{'건수':>7}{'빈칸':>7}{'비한글명':>9}{'비율':>9}{'복합(쉼표)':>11}{'이름 종류':>9}")
    for b, g in sorted(df.groupby("_b"), key=lambda kv: -len(kv[1])):
        gs = g["성분명"].astype(str).str.strip()
        nz = gs[gs != ""]
        print(f"{b:<14}{len(g):>7,}{int(g['_blank'].sum()):>7,}"
              f"{int(g['_code'].sum()):>9,}{100*g['_code'].mean():>8.1f}%"
              f"{int(g['_multi'].sum()):>11,}{nz.nunique():>9,}")

    print("\n── 바구니별 '한글 아닌 이름' 예시 ──")
    for b in ["1상", "2상", "3상", "생동"]:
        g = df[(df["_b"] == b) & df["_code"]]["성분명"].astype(str)
        if len(g):
            print(f"  {b}: " + " · ".join(g.value_counts().head(6).index.tolist())[:200])
        else:
            print(f"  {b}: (없음)")
    return 0


raise SystemExit(main())
