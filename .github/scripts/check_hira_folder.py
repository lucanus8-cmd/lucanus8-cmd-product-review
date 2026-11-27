"""드라이브 HIRA 약가 폴더의 파일 목록과, 사이트가 어떤 파일을 최신 고시로 읽는지 본다 (읽기만, 공개 자료)."""
import sys, warnings, logging
warnings.filterwarnings("ignore"); logging.disable(logging.WARNING)
sys.path.insert(0, "."); sys.path.insert(0, ".github/scripts")
import gsheet  # noqa: E402


def main():
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    creds = service_account.Credentials.from_service_account_info(
        gsheet._info(), scopes=["https://www.googleapis.com/auth/drive.readonly"])
    drive = build("drive", "v3", credentials=creds, cache_discovery=False)
    import bootstrap as b
    fs = drive.files().list(q=f"'{b.HIRA_PRICE_FOLDER_ID}' in parents and trashed=false",
                            fields="files(id,name,mimeType,modifiedTime,size)", pageSize=500,
                            supportsAllDrives=True, includeItemsFromAllDrives=True).execute().get("files", [])
    print(f"폴더 파일 {len(fs)}개")
    for f in sorted(fs, key=lambda x: x["name"]):
        print(f"  · {f['name']} | {f['mimeType'].split('.')[-1]} | 수정 {f['modifiedTime'][:16]} | "
              f"크기 {int(f.get('size', 0))//1024}KB | 읽는 날짜 → {b._parse_price_date(f['name'])}")
    ok = [f for f in fs if b._parse_price_date(f["name"]) is not None]
    if ok:
        last = max(ok, key=lambda x: b._parse_price_date(x["name"]))
        print("사이트가 최신 고시로 읽는 파일:", last["name"], "→", b._parse_price_date(last["name"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
