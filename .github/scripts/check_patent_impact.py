"""물질특허를 '물질 / 괄호 붙은 것(염·수화물·기타…)'으로 나누면 성분별
물질특허 만료일이 얼마나 바뀌는지 센다.

배포된 사이트의 특허 분석 화면에서 상태를 '등록'으로 두고, 표의 '전체 CSV'
단추로 등록 특허 전체를 받는다. 그다음 여기서 계산해 요약과 예시만 적는다.
특허는 식약처 공개 자료다. 읽기만 하므로 사이트는 바뀌지 않는다.

    지금 방식: 유형에 '물질' 글자가 있으면 물질특허
    새 방식  : 유형을 조각으로 나눠 '물질' 조각이 있으면 물질특허,
               '물질(…)' 조각만 있으면 형태 특허(염·수화물 등)
"""
import io
import os
import re
import sys
import time

import pandas as pd
from playwright.sync_api import sync_playwright

URL = (os.environ.get("APP_URL") or "").rstrip("/")
PW = os.environ.get("APP_PASSWORD") or ""
APP_SEL = ("[data-testid='stAppViewContainer']", "[data-testid='stApp']",
           "input[type='password']")
TOKEN = re.compile(r"물질(?:\([^)]*\))?|용도|조성|제법|기타")


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


def settle(pg, gap=1.0, need=4, cap=180):
    last, same, t0 = None, 0, time.time()
    while time.time() - t0 < cap:
        b = body_text(pg)
        busy = app(pg).query_selector("[data-testid='stStatusWidget']")
        same = same + 1 if (b == last and not busy) else 0
        last = b
        if same >= need:
            return
        time.sleep(gap)


def tokens(t):
    return TOKEN.findall(str(t or ""))


def kind(t):
    tk = tokens(t)
    if "물질" in tk:
        return "물질"
    if any(x.startswith("물질(") for x in tk):
        return "형태"
    return ""


def fetch_csv(pg):
    pg.goto(URL + "/?page=patent", wait_until="domcontentloaded", timeout=180_000)
    if not wait_for(pg, "특허 분석"):
        raise RuntimeError("특허 분석 화면이 안 떴다")
    settle(pg)
    # 상태 기본값은 '등록' 하나다. 혹시 다르면 판정이 틀리므로 확인만 한다.
    st_txt = app(pg).locator("[data-testid='stMultiSelect']").filter(
        has_text="상태").first.inner_text().replace("\n", " | ")
    print(f"상태 칸: {st_txt}")
    btn = app(pg).locator("[data-testid='stDownloadButton'] button, button").filter(
        has_text="CSV").first
    if not btn.count():
        raise RuntimeError("전체 CSV 단추가 없다 (500건 이하이면 단추가 안 나온다)")
    with pg.expect_download(timeout=180_000) as d:
        btn.click()
    raw = open(d.value.path(), "rb").read()
    return pd.read_csv(io.BytesIO(raw), dtype=str, encoding="utf-8-sig").fillna("")


def analyse(df):
    df = df.copy()
    df["_d"] = pd.to_datetime(df["만료일"], errors="coerce")
    df = df[df["_d"].notna()]
    df["_k"] = df["유형"].map(kind)
    df["_old"] = df["유형"].astype(str).str.contains("물질", na=False)
    ing = df["성분"].astype(str).str.strip()
    single = ~ing.str.contains(r"[/+,]", regex=True)
    df = df[single & (ing.str.len() > 0)]
    print(f"등록 특허 {len(df):,}건 (단일 성분, 만료일 있는 것) · 성분 {df['성분'].nunique():,}개")

    vc = df["유형"].replace("", "(비어 있음)").value_counts()
    print("\n유형 값 (등록 · 단일 성분)")
    for t, n in vc.items():
        print(f"   {t:<24}{n:>7,}  → {kind(t) or '물질 아님'}")

    g = df.groupby("성분")
    old = df[df["_old"]].groupby("성분")["_d"].max()
    new = df[df["_k"] == "물질"].groupby("성분")["_d"].max()
    form = df[df["_k"] == "형태"].groupby("성분")["_d"].max()
    blank_only = g["유형"].apply(lambda s: (s.astype(str).str.strip() == "").all())

    res = pd.DataFrame({"지금": old, "새물질": new, "형태": form}).dropna(subset=["지금"])
    same = res["새물질"].notna() & (res["새물질"] == res["지금"])
    earlier = res["새물질"].notna() & (res["새물질"] < res["지금"])
    gone = res["새물질"].isna()
    print(f"\n지금 방식으로 물질특허가 살아 있는 성분 {len(res):,}개")
    print(f"   그대로            {int(same.sum()):>5,}개  (성분 자체 물질특허가 가장 늦게 끝남)")
    print(f"   만료일이 당겨짐   {int(earlier.sum()):>5,}개  (가장 늦은 것이 형태 특허였음)")
    print(f"   물질특허 없어짐   {int(gone.sum()):>5,}개  (형태 특허만 있었음 — 미라베그론과 같은 경우)")
    print(f"\n살아 있는 특허가 전부 유형 미기재인 성분 {int(blank_only.sum()):,}개")

    def show(mask, title, n=15):
        sub = res[mask].copy()
        sub["차이(년)"] = ((sub["지금"] - sub["새물질"]).dt.days / 365.25).round(1)
        sub = sub.sort_values("지금", ascending=False).head(n)
        print(f"\n{title} — 예시 {len(sub)}개")
        for k, r in sub.iterrows():
            src = df[(df["성분"] == k) & (df["_d"] == r["지금"]) & df["_old"]]
            ty = " / ".join(sorted(set(src["유형"])))[:30]
            who = " / ".join(sorted(set(src["특허권자"])))[:40]
            nm = "없음" if pd.isna(r["새물질"]) else r["새물질"].date()
            print(f"   {k[:22]:<22} 지금 {r['지금'].date()} → 새 물질 {nm} · 형태 {ty} · {who}")

    show(gone, "물질특허 없어짐")
    show(earlier, "만료일이 당겨짐")
    for q in ("미라베그론",):
        if q in res.index:
            r = res.loc[q]
            print(f"\n확인 · {q}: 지금 {r['지금'].date()} · 새 물질 "
                  f"{'없음' if pd.isna(r['새물질']) else r['새물질'].date()} · 형태 "
                  f"{'없음' if pd.isna(r['형태']) else r['형태'].date()}")


def main():
    if not URL:
        print("APP_URL 이 없다")
        return 1
    with sync_playwright() as p:
        br = p.chromium.launch(executable_path=os.environ.get("CHROMIUM_PATH") or None)
        pg = br.new_context(viewport={"width": 1600, "height": 2000}, locale="ko-KR",
                            accept_downloads=True).new_page()
        try:
            df = fetch_csv(pg)
        except Exception as e:
            print("CSV 를 못 받았다:", str(e)[:300])
            print("   화면 앞부분:", body_text(pg)[:300].replace("\n", " | "))
            br.close()
            return 1
        br.close()
    print(f"받은 표 {len(df):,}줄 · 칸 {list(df.columns)}")
    if "등록" not in set(df.get("상태", pd.Series(dtype=str))):
        print("상태가 '등록'인 줄이 없다 — 판정 불가")
        return 1
    df = df[df["상태"] == "등록"]
    analyse(df)
    return 0


if __name__ == "__main__":
    sys.exit(main())
