@echo off
chcp 65001 > nul
echo 의약품 매출 분석 대시보드 시작 중...
cd /d "%~dp0"
python -m streamlit run app.py --server.port 8501
pause
