"""매출 자료에서 단일제/복합제를 가를 수 있는지 본다 (읽기만) — 매출 숫자는 적지 않는다.
적는 것: 성분 칸 이름, 구분자(+ , / 등)가 있는 행 수, 성분 표기 예시(공개 성분명), 판정 규칙별 개수."""
import re, sys, warnings, logging
from collections import Counter
warnings.filterwarnings("ignore"); logging.disable(logging.WARNING)
sys.path.insert(0, "."); sys.path.insert(0, ".github/scripts")
import gsheet  # noqa: E402


def main():
    if not gsheet.available():
        print("키 없음"); return 1
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    creds = service_account.Credentials.from_service_account_info(
        gsheet._info(), scopes=["https://www.googleapis.com/auth/drive.readonly"])
    drive = build("drive", "v3", credentials=creds, cache_discovery=False)
    import bootstrap
    for name, fid, parse in (("IQVIA", bootstrap.IQVIA_ID, bootstrap._parse_iqvia),
                             ("UBIST", bootstrap.UBIST_ID, bootstrap._parse_ubist)):
        print(f"\n===== {name} =====")
        df = parse(bootstrap._download(drive, fid))
        txt = [c for c in df.columns if not re.search(r"\d{4}|LC-|DU", str(c))]
        print("글자 칸:", txt)
        col = "성분명" if "성분명" in df.columns else ("성분" if "성분" in df.columns else None)
        if not col:
            print("성분 칸 없음"); continue
        s = df[col].astype(str)
        print(f"행 {len(df):,} · 성분 종류 {s.nunique():,}")
        for sep, pat in (("+", r"\+"), (",", r","), ("/", r"/"), (" & ", r"&"), ("·", r"·"), ("|", r"\|")):
            n = int(s.str.contains(pat, regex=True).sum())
            print(f"  '{sep}' 포함 행 {n:,}")
        ex = s[s.str.contains(r"\+|,|/|&", regex=True)].drop_duplicates().head(12).tolist()
        print("  구분자 있는 성분 예:", ex)
        print("  구분자 없는 성분 예:", s[~s.str.contains(r"\+|,|/|&", regex=True)].drop_duplicates().head(6).tolist())
        if "용량" in df.columns:
            print("  용량에 '/' 있는 행:", int(df["용량"].astype(str).str.contains("/").sum()))
        for c in df.columns:
            if any(k in str(c) for k in ("복합", "성분수", "COMBI", "MOLECULE", "분자")):
                print(f"  후보 칸 [{c}] 값 예:", Counter(df[c].astype(str)).most_common(6))
    return 0


if __name__ == "__main__":
    sys.exit(main())
