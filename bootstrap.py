"""배포(Streamlit Cloud 등)에서 데이터 파일을 구글 드라이브에서 받아온다.
- 로컬에 파일이 이미 있으면 아무것도 안 함(로컬 개발 그대로).
- st.secrets["drive_files"] (경로→파일ID) 와 st.secrets["gcp_service_account"] 가 있으면
  없는 파일만 드라이브에서 다운로드.
라이선스 데이터를 레포/이미지에 넣지 않고 비공개 드라이브에서만 받게 하는 용도.
"""
import io
from pathlib import Path
import streamlit as st

BASE = Path(__file__).parent

@st.cache_resource(show_spinner="데이터 준비 중…")
def ensure_data():
    try:
        files = dict(st.secrets.get("drive_files", {}))
    except Exception:
        files = {}
    if not files:
        return "local"  # 시크릿 없음 → 로컬 번들 파일 사용
    missing = {p: fid for p, fid in files.items() if not (BASE / p).exists()}
    if not missing:
        return "ok"
    sa = dict(st.secrets["gcp_service_account"])
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaIoBaseDownload
    creds = service_account.Credentials.from_service_account_info(
        sa, scopes=["https://www.googleapis.com/auth/drive.readonly"])
    api = build("drive", "v3", credentials=creds, cache_discovery=False)
    for path, fid in missing.items():
        dst = BASE / path
        dst.parent.mkdir(parents=True, exist_ok=True)
        req = api.files().get_media(fileId=fid)
        buf = io.BytesIO()
        dl = MediaIoBaseDownload(buf, req, chunksize=10 * 1024 * 1024)
        done = False
        while not done:
            _, done = dl.next_chunk()
        dst.write_bytes(buf.getvalue())
    return f"downloaded {len(missing)}"
