"""성분 하나의 공개 약가·허가 자료만 뽑아 보기 (읽기만). 매출 자료는 읽지 않는다.

WORDS 에 '영문;한글' (예: fexofenadine;펙소페나딘). 오리지널 가격 이력은 NAME(예: 알레그라)로 거른다.
적는 것: 최신 심평원 고시(드라이브 HIRA 폴더)의 해당 성분 행, 기존 가격이력 엑셀의 오리지널 변동,
허가 시트의 해당 성분 품목(제형·전문/일반·상태). 모두 공개 자료다.
"""
import os
import re
import sys
import warnings
import logging

warnings.filterwarnings("ignore")
logging.disable(logging.WARNING)
sys.path.insert(0, ".")
sys.path.insert(0, ".github/scripts")

import pandas as pd            # noqa: E402
import gsheet                  # noqa: E402

pd.set_option("display.width", 260)
pd.set_option("display.max_columns", 30)
pd.set_option("display.max_colwidth", 48)
pd.set_option("display.max_rows", 200)


def main():
    if not gsheet.available():
        print("구글 키 없음"); return 1
    words = (os.environ.get("WORDS") or "fexofenadine;펙소페나딘").split(";")
    name = os.environ.get("NAME") or "알레그라"
    pat = "|".join(re.escape(w.strip()) for w in words if w.strip())
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    creds = service_account.Credentials.from_service_account_info(
        gsheet._info(), scopes=["https://www.googleapis.com/auth/drive.readonly",
                                "https://www.googleapis.com/auth/spreadsheets.readonly"])
    drive = build("drive", "v3", credentials=creds, cache_discovery=False)
    import bootstrap as b

    # ① 최신 고시 파일
    fs = drive.files().list(q=f"'{b.HIRA_PRICE_FOLDER_ID}' in parents and trashed=false",
                            fields="files(id,name)", pageSize=500,
                            supportsAllDrives=True, includeItemsFromAllDrives=True).execute().get("files", [])
    fs = [f for f in fs if b._parse_price_date(f["name"]) is not None]
    f = max(fs, key=lambda x: b._parse_price_date(x["name"]))
    print(f"[고시] {f['name']}")
    raw = pd.read_excel(b._download(drive, f["id"]), dtype=str, header=None)
    hdr = next(i for i in range(10) if any("제품코드" in str(x) for x in raw.iloc[i].tolist()))
    df = raw.iloc[hdr + 1:].copy().fillna("")
    df.columns = [re.sub(r"\s+", "", str(c)) for c in raw.iloc[hdr].tolist()]
    print("   열:", list(df.columns))
    hit = df[df.apply(lambda r: bool(re.search(pat, " ".join(map(str, r.values)), re.I)), axis=1)]
    keep = [c for c in df.columns if any(k in c for k in ("투여", "분류", "주성분코드", "제품코드", "제품명", "업체",
                                                          "규격", "단위", "상한금액", "전문", "비고", "주성분명"))]
    print(f"   해당 성분 {len(hit)}행")
    print(hit[keep].to_string(index=False))

    # ② 기존 가격이력 엑셀의 오리지널 변동
    try:
        base = pd.read_excel(b._download(drive, b.PRICE_XLSX_ID), sheet_name="가격이력(변동)", dtype=str).fillna("")
        ncol = next((c for c in base.columns if "제품명" in c), None)
        o = base[base[ncol].astype(str).str.contains(name, na=False)] if ncol else base.iloc[0:0]
        print(f"\n[가격이력] '{name}' {len(o)}행 · 열 {list(base.columns)[:12]}")
        print(o.to_string(index=False)[:6000])
    except Exception as e:
        print("가격이력 읽기 실패:", type(e).__name__, str(e)[:100])

    # ③ 허가 시트: 같은 성분 품목의 제형
    try:
        ap = gsheet.read_sheet(b.SHEETS["approval"])
        m = ap[ap.apply(lambda r: bool(re.search(pat, " ".join(map(str, r.values)), re.I)), axis=1)]
        cols = [c for c in ap.columns if any(k in c for k in ("품목명", "업체명", "전문/일반", "상태", "주성분", "제형",
                                                              "성상", "허가일", "신약", "품목기준코드"))]
        print(f"\n[허가] {len(m)}품목 · 열 {cols}")
        print(m[cols].to_string(index=False)[:9000])
    except Exception as e:
        print("허가 읽기 실패:", type(e).__name__, str(e)[:100])
    return 0


if __name__ == "__main__":
    sys.exit(main())
