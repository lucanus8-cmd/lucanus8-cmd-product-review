"""UBIST 드라이브 파일 점검 (읽기만) — 매출 숫자는 절대 적지 않는다.

적는 것: 파일 이름·수정 시각·버전(메타정보), 머리글의 기간 이름, 사이트 파서가 만든 칸 이름,
연도별로 잡힌 분기 목록. 값(처방조제액 등)은 읽기만 하고 화면·로그에 내지 않는다.
"""
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


def main():
    if not gsheet.available():
        print("구글 키 없음")
        return 1
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    creds = service_account.Credentials.from_service_account_info(
        gsheet._info(), scopes=["https://www.googleapis.com/auth/drive.readonly"])
    drive = build("drive", "v3", credentials=creds, cache_discovery=False)
    import bootstrap
    fid = bootstrap.UBIST_ID
    meta = drive.files().get(fileId=fid, supportsAllDrives=True,
                             fields="name,modifiedTime,version,size,mimeType,headRevisionId").execute()
    print(f"파일: {meta.get('name')} · 수정 {meta.get('modifiedTime')} · 버전 {meta.get('version')} · "
          f"크기 {int(meta.get('size', 0)) // 1024 // 1024}MB · {meta.get('mimeType')}")
    buf = bootstrap._download(drive, fid)
    head = pd.read_excel(buf, sheet_name=0, header=None, nrows=2)
    r0 = [str(x).strip() if pd.notna(x) else "" for x in head.iloc[0].tolist()]
    r1 = [str(x).strip() if pd.notna(x) else "" for x in head.iloc[1].tolist()]
    per = {}
    for a, b in zip(r0, r1):
        if a in ("처방조제액(원)", "처방건수_P", "처방량_P"):
            per.setdefault(a, []).append(b)
    for m, labs in per.items():
        print(f"\n[{m}] 기간 머리글 {len(labs)}개: 처음 {labs[:2]} · 끝 {labs[-6:]}")
    other = [b or a for a, b in zip(r0, r1) if a not in per]
    print(f"\n기간이 아닌 칸: {other[:20]}")
    buf.seek(0)
    df = bootstrap._parse_ubist(buf)
    print(f"\n파서 결과: {len(df):,}행")
    for m in ("처방조제액(원)", "처방건수_P", "처방량_P"):
        q = [c for c in df.columns if re.match(rf"{re.escape(m)}_\d{{4}}Q[0-4]$", c)]
        y = sorted(c for c in df.columns if re.match(rf"{re.escape(m)}_\d{{4}}년$", c))
        byyr = {}
        for c in q:
            mm = re.search(r"_(\d{4})Q([0-4])$", c)
            byyr.setdefault(mm.group(1), []).append("Q" + mm.group(2))
        print(f"   {m}: 분기 칸 {len(q)}개 · 연도별 {dict(sorted(byyr.items()))} · 연간 칸 {[c.split('_')[-1] for c in y]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
