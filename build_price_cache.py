"""드라이브의 약가 xlsx 파일 → scout_data/price.parquet (Drive API, 서비스계정).
변환 없이 업로드된 엑셀 파일을 그대로 다운로드해 읽는다.
사용:  python build_price_cache.py <파일ID>        (또는 아래 FILE_ID 상수에 입력)
필요:  Google Drive API 사용설정 + 파일을 서비스계정에 공유 + secrets/service-account.json
"""
import io
import sys
from pathlib import Path
import pandas as pd
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

BASE = Path(__file__).parent
KEYFILE = BASE / "secrets" / "service-account.json"
SD = BASE / "scout_data"; SD.mkdir(exist_ok=True)
SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]

FILE_ID = ""              # ← 약가 xlsx 파일 ID (Drive 링크 .../file/d/여기/view)
SHEET = "가격이력(변동)"    # 읽을 시트 탭

def download_bytes(file_id):
    creds = service_account.Credentials.from_service_account_file(str(KEYFILE), scopes=SCOPES)
    api = build("drive", "v3", credentials=creds, cache_discovery=False)
    meta = api.files().get(fileId=file_id, fields="name,size,mimeType").execute()
    print(f"파일: {meta.get('name')} ({int(meta.get('size',0))/1e6:.1f}MB, {meta.get('mimeType')})")
    req = api.files().get_media(fileId=file_id)
    buf = io.BytesIO()
    dl = MediaIoBaseDownload(buf, req, chunksize=5 * 1024 * 1024)
    done = False
    while not done:
        status, done = dl.next_chunk()
    buf.seek(0)
    return buf

def main():
    file_id = sys.argv[1] if len(sys.argv) > 1 else FILE_ID
    if not file_id:
        print("파일 ID가 필요합니다:  python build_price_cache.py <파일ID>", file=sys.stderr); return
    buf = download_bytes(file_id)
    df = pd.read_excel(buf, sheet_name=SHEET, dtype=str).fillna("")
    df.to_parquet(SD / "price.parquet", index=False)
    print(f"[OK] price: {len(df):,}행, 컬럼 {list(df.columns)} → scout_data/price.parquet")

if __name__ == "__main__":
    main()
