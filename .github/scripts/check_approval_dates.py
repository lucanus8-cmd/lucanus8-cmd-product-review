"""허가 시트 원본에서 오래된 오리지널의 허가일이 어떻게 적혀 있는지 본다 (읽기만, 공개 자료).
노바스크·코자플러스처럼 허가일이 실제보다 늦게 나오는 이유를 찾기 위함."""
import os, sys, warnings, logging
warnings.filterwarnings("ignore"); logging.disable(logging.WARNING)
sys.path.insert(0, "."); sys.path.insert(0, ".github/scripts")
import re
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
        cols = [c for c in ["품목기준코드", "품목명", "업체명", "허가일자", "상태", "취하일자", "제조/수입", "신약구분", "PERMIT_KIND_NAME"] if c in m.columns]
        if not m.empty:
            print(m[cols].to_string(index=False))
            h = str(m["구분"].iloc[0]) if "구분" in m.columns else ""
            print("   첫 품목 변경이력에서 가장 이른 날:", min(re.findall(r"(\d{4}-\d{2}-\d{2})", h)) if re.findall(r"\d{4}-\d{2}-\d{2}", h) else "-")
    # 같은 성분의 옛 오리지널 허가(취하 등)가 시트에 있는지: 회사명으로
    for ing, cos in (("암로디핀베실산염", "화이자"), ("로사르탄칼륨", "엠에스디|머크|오가논"), ("클로피도그렐", "사노피|한독")):
        z = ap[ap["주성분"].astype(str).str.contains(ing) & ap["업체명"].astype(str).str.contains(cos, regex=True)]
        print(f"\n[{ing} · {cos}] {len(z)}행")
        if not z.empty:
            print(z[[c for c in ["품목기준코드", "품목명", "업체명", "허가일자", "상태", "취하일자", "제조/수입"] if c in z.columns]].head(12).to_string(index=False))
    print("\n제조/수입 값:", ap["제조/수입"].astype(str).value_counts().head(5).to_dict() if "제조/수입" in ap.columns else "-")
    print("상태별 행 수:", ap["상태"].astype(str).value_counts().to_dict())
    y = pd.to_numeric(ap["허가일자"].astype(str).str[:4], errors="coerce")
    print("허가 연도 분포(10년 단위):", (y // 10 * 10).value_counts().sort_index().to_dict())
    return 0


if __name__ == "__main__":
    sys.exit(main())
