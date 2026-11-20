"""고친 로봇으로 새로 만든 사이트용 FDA 표(out/fda_site)와 지금 사이트가 읽는 표(data 가지 fda_site)를 비교한다.
읽기만 한다 — 구글 시트·data 가지에 쓰지 않는다."""
import io
import sys
import urllib.request

import pandas as pd

RAW = "https://raw.githubusercontent.com/lucanus8-cmd/lucanus8-cmd-product-review/data/fda_site/"


def old(name):
    with urllib.request.urlopen(urllib.request.Request(RAW + name, headers={"User-Agent": "product-review-robot"}), timeout=120) as r:
        return pd.read_parquet(io.BytesIO(r.read()))


def main():
    a, b = old("ing.parquet"), pd.read_parquet("out/fda_site/ing.parquet")
    print(f"성분 표: 지금 {len(a):,}줄 → 새로 {len(b):,}줄")
    print("국내 상황 (지금):", a["국내 상황"].value_counts().to_dict())
    print("국내 상황 (새로):", b["국내 상황"].value_counts().to_dict())
    m = a[["주성분", "국내 상황", "국내 품목 수"]].merge(b[["주성분", "국내 상황", "한글명", "국내 품목 수", "국내 제형"]],
                                                    on="주성분", how="outer", suffixes=("_지금", "_새로"), indicator=True)
    ch = m[(m["_merge"] != "both") | (m["국내 상황_지금"] != m["국내 상황_새로"])]
    print(f"\n국내 상황이 바뀐 성분 {len(ch)}개:")
    for _, r in ch.sort_values(["국내 상황_지금", "주성분"]).iterrows():
        print(f"   {str(r['주성분'])[:40]:40s} | {r['국내 상황_지금']} → {r['국내 상황_새로']} | 한글명 {str(r['한글명'])[:30]}"
              f" | 국내 품목 {r['국내 품목 수_지금']}→{r['국내 품목 수_새로']} | 국내 제형 {str(r['국내 제형'])[:30]}")
    same = m[(m["_merge"] == "both") & (m["국내 상황_지금"] == m["국내 상황_새로"])]
    diffn = same[same["국내 품목 수_지금"] != same["국내 품목 수_새로"]]
    print(f"\n국내 상황은 같고 국내 품목 수만 바뀐 성분 {len(diffn)}개 · 예:")
    for _, r in diffn.head(30).iterrows():
        print(f"   {r['주성분'][:40]:40s} | {r['국내 상황_지금']} | 품목 {r['국내 품목 수_지금']}→{r['국내 품목 수_새로']}")
    c1, c2 = old("cmp.parquet"), pd.read_parquet("out/fda_site/cmp.parquet")
    print(f"\n비교 표(국내 성분 없음·제형 없음 목록): 지금 {len(c1):,}줄 → 새로 {len(c2):,}줄 · 구분 {c2['구분'].value_counts().to_dict()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
