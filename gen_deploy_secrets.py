"""Streamlit Cloud 'Secrets'에 붙여넣을 deploy_secrets.toml 을 생성한다.
- service-account.json, .streamlit/secrets.toml(APP_PASSWORD·GEMINI)에서 값을 채움.
- (선택) 드라이브 폴더ID를 인자로 주면, 그 폴더의 데이터 파일 ID를 자동 수집해 [drive_files] 채움.
사용:  python gen_deploy_secrets.py [데이터폴더ID]
결과:  deploy_secrets.toml  (git 제외됨 — 내용을 Streamlit Secrets에 그대로 붙여넣기)
"""
import json
import sys
import tomllib
from pathlib import Path

BASE = Path(__file__).parent
NAME2PATH = {
    "iqvia.pkl": "saved_data/iqvia.pkl",
    "ubist.pkl": "saved_data/ubist.pkl",
    "approval.parquet": "scout_data/approval.parquet",
    "patent.parquet": "scout_data/patent.parquet",
    "clinical.parquet": "scout_data/clinical.parquet",
    "price.parquet": "scout_data/price.parquet",
    "nego.parquet": "scout_data/nego.parquet",
}

sa = json.load(open(BASE / "secrets" / "service-account.json", encoding="utf-8"))
try:
    sec = tomllib.load(open(BASE / ".streamlit" / "secrets.toml", "rb"))
except Exception:
    sec = {}

drive_files = {}
if len(sys.argv) > 1:
    folder = sys.argv[1].strip()
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    creds = service_account.Credentials.from_service_account_file(
        str(BASE / "secrets" / "service-account.json"),
        scopes=["https://www.googleapis.com/auth/drive.readonly"])
    api = build("drive", "v3", credentials=creds, cache_discovery=False)
    res = api.files().list(q=f"'{folder}' in parents and trashed=false",
                           fields="files(id,name)", pageSize=200).execute()
    for f in res.get("files", []):
        if f["name"] in NAME2PATH:
            drive_files[NAME2PATH[f["name"]]] = f["id"]

L = []
L.append(f'APP_PASSWORD = "{sec.get("APP_PASSWORD", "여기에_비밀번호")}"')
L.append(f'GEMINI_API_KEY = "{sec.get("GEMINI_API_KEY", "")}"')
L.append(f'GEMINI_MODEL = "{sec.get("GEMINI_MODEL", "gemini-3.6-flash")}"')
L.append("")
L.append("# 데이터는 bootstrap.py가 드라이브 소스ID로 직접 생성 → drive_files 불필요")
L.append("[gcp_service_account]")
for k in ["type", "project_id", "private_key_id", "private_key", "client_email",
          "client_id", "auth_uri", "token_uri", "auth_provider_x509_cert_url",
          "client_x509_cert_url", "universe_domain"]:
    if k in sa:
        v = sa[k].replace("\n", "\\n") if k == "private_key" else sa[k]
        L.append(f'{k} = "{v}"')

(BASE / "deploy_secrets.toml").write_text("\n".join(L) + "\n", encoding="utf-8")
missing = [p for p in NAME2PATH.values() if p not in drive_files]
print(f"deploy_secrets.toml 생성 완료 · drive_files {len(drive_files)}/7개 채움")
if missing:
    print("아직 ID 없는 파일:", ", ".join(missing))
