# 제품 검토 대시보드 — 배포 가이드 (Streamlit Cloud, 크롬 + 비밀번호)

크롬에서 URL 접속 → **비밀번호 로그인** → 사용. 데이터는 레포에 넣지 않고 **비공개 구글 드라이브에서 받아옵니다.**
(IQVIA·UBIST는 유료 라이선스 데이터이므로 완전 공개 금지 — 반드시 비밀번호 뒤에 둡니다.)

---

## 준비 1. 데이터 파일을 구글 드라이브에 올리고 파일 ID 확보
앱이 런타임에 아래 파일들을 드라이브에서 내려받습니다. **각 파일을 드라이브에 올리고, 서비스계정에 공유 후, 파일 ID를 확보하세요.**

| 앱 내 경로 | 올릴 파일 |
|---|---|
| `saved_data/iqvia.pkl` | 의약품분석/saved_data/iqvia.pkl |
| `saved_data/ubist.pkl` | 의약품분석/saved_data/ubist.pkl |
| `scout_data/approval.parquet` | 의약품분석/scout_data/approval.parquet |
| `scout_data/patent.parquet` | 〃 patent.parquet |
| `scout_data/clinical.parquet` | 〃 clinical.parquet |
| `scout_data/price.parquet` | 〃 price.parquet |
| `scout_data/nego.parquet` | 〃 nego.parquet |

- 각 파일 우클릭 → 공유 → 서비스계정(`project-pilot-study@sales-508422.iam.gserviceaccount.com`)에 **뷰어**
- 우클릭 → 링크 복사 → `.../file/d/`**`파일ID`**`/view` 의 파일ID

## 준비 2. GitHub 비공개 저장소에 **코드만** 올리기
- `.gitignore`가 데이터·시크릿을 자동 제외합니다. `git add .` 하면 코드만 올라갑니다.
- 올라가는 것: `scouting.py, scout_sources.py, sales_analysis.py, app.py, bootstrap.py, gemini_ai.py, build_*.py, requirements.txt, .streamlit/config.toml, README*` 등
- **절대 올리면 안 되는 것(자동 제외됨)**: `saved_data/`, `scout_data/`, `secrets.toml`, `secrets/`

## 준비 3. Streamlit Community Cloud 배포
1. https://share.streamlit.io → **New app** → 저장소 선택 → Main file: `의약품분석/scouting.py`
2. **Advanced settings → Secrets** 에 아래를 붙여넣기(값 채우기):

```toml
# 접속 비밀번호
APP_PASSWORD = "회사에서_정한_비밀번호"

# Gemini
GEMINI_API_KEY = "AIza... 또는 발급받은 키"
GEMINI_MODEL = "gemini-3.6-flash"

# 데이터 파일 ID (준비 1에서 확보)
[drive_files]
"saved_data/iqvia.pkl"       = "파일ID"
"saved_data/ubist.pkl"       = "파일ID"
"scout_data/approval.parquet"= "파일ID"
"scout_data/patent.parquet"  = "파일ID"
"scout_data/clinical.parquet"= "파일ID"
"scout_data/price.parquet"   = "파일ID"
"scout_data/nego.parquet"    = "파일ID"

# 드라이브 접근용 서비스계정 키 (service-account.json 내용 그대로)
[gcp_service_account]
type = "service_account"
project_id = "sales-508422"
private_key_id = "..."
private_key = "-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----\n"
client_email = "project-pilot-study@sales-508422.iam.gserviceaccount.com"
client_id = "..."
token_uri = "https://oauth2.googleapis.com/token"
```
> `[gcp_service_account]` 값은 `secrets/service-account.json` 파일 내용을 그대로 옮기면 됩니다.

3. Deploy → `https://<앱이름>.streamlit.app` 생성 → 크롬 접속 → 비밀번호 입력.

---

## 주의 (무료 플랜 메모리)
- 데이터가 커서(특히 iqvia.pkl) 무료 플랜(약 1GB RAM)에서 메모리가 빠듯할 수 있습니다.
  실행 중 재시작/오류가 잦으면: (a) 유료 플랜, 또는 (b) Docker로 소형 VM 배포(`README` 하단 A안)를 권장합니다.

## 데이터 갱신
새 심평원 약가/시트 반영 시 로컬에서 `build_*.py` 실행 → 갱신된 파일을 드라이브의 **같은 파일에 덮어쓰기**(파일 ID 유지)하면 앱이 다음 재시작 때 최신을 받습니다.

## (대안 A) Docker / VM 배포
데이터를 서버에 번들하고 로그인 뒤에서만 노출하는 방식 — 라이선스 데이터에 가장 안전.
```bash
docker build -t product-review .
docker run -d -p 8501:8501 -v $(pwd)/.streamlit:/app/.streamlit:ro product-review
```

## 보안 체크리스트
- [ ] `secrets.toml`·`secrets/`·`saved_data/`·`scout_data/` git 제외 확인 (`git status`)
- [ ] 강한 APP_PASSWORD
- [ ] 라이선스 데이터는 로그인 뒤에서만 노출
- [ ] API 키는 Secrets에만(코드/채팅 금지), 유출 시 재발급
