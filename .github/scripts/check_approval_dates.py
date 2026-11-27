"""허가 시트 원본에서 오래된 오리지널의 허가일이 어떻게 적혀 있는지 본다 (읽기만, 공개 자료).
노바스크·코자플러스처럼 허가일이 실제보다 늦게 나오는 이유를 찾기 위함."""
import os, sys, warnings, logging
warnings.filterwarnings("ignore"); logging.disable(logging.WARNING)
sys.path.insert(0, "."); sys.path.insert(0, ".github/scripts")
import pandas as pd   # noqa: E402
import gsheet         # noqa: E402
import bootstrap      # noqa: E402

pd.set_option("display.width", 300); pd.set_option("display.max_colwidth", 40); pd.set_option("display.max_columns", 80)


def main():
    if not gsheet.available():
        print("키 없음"); return 1
    ap = gsheet.read_sheet(bootstrap.SHEETS["approval"])
    print(f"허가 시트 {len(ap):,}행 · 열 {len(ap.columns)}개")
    print("열:", list(ap.columns))
    for c in ap.columns:
        if any(k in str(c) for k in ("상태", "취소", "구분", "일자", "일")):
            v = ap[c].astype(str)
            print(f"  [{c}] 예: {v.dropna().unique()[:6].tolist()} · 빈 칸 {int((v.str.strip().isin(['', 'nan', 'None'])).sum()):,}")
    names = os.environ.get("WORDS") or "노바스크,플라빅스,코자플러스,코자정,리피토,아모디핀,오잘탄플러스,크레스토,자누비아"
    for nm in names.split(","):
        m = ap[ap["품목명"].astype(str).str.replace(" ", "").str.startswith(nm.strip())]
        print(f"\n=== {nm} ({len(m)}행) ===")
        if not m.empty:
            print(m.T.to_string()[:6000])
    return 0


if __name__ == "__main__":
    sys.exit(main())
