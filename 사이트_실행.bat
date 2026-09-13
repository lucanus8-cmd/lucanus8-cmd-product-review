@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================
echo  제품 검토 사이트 시작 (앱 + 인터넷 공개 터널)
echo  비밀번호는 .streamlit\secrets.toml 의 APP_PASSWORD
echo ============================================
echo.
echo [1/2] 앱 시작 중... (localhost:8502)
start "product-review-app" /min python -m streamlit run scouting.py --server.port 8502 --server.headless true
echo      앱 뜰 때까지 8초 대기...
timeout /t 8 >nul
echo.
echo [2/2] 인터넷 공개 터널 시작 (아래 https://....trycloudflare.com 주소로 접속)
echo      * 이 창을 닫으면 사이트가 내려갑니다. 켜두세요.
echo.
cloudflared.exe tunnel --url http://localhost:8502 --no-autoupdate
pause
