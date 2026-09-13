FROM python:3.12-slim
WORKDIR /app

# 의존성
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 앱 + 데이터 (saved_data/*.pkl, scout_data/*.parquet 포함)
COPY . .

# 비밀번호는 실행 시 환경변수/시크릿으로 주입 (이미지에 넣지 말 것)
EXPOSE 8501
HEALTHCHECK CMD curl --fail http://localhost:8501/_stcore/health || exit 1
CMD ["streamlit", "run", "scouting.py", \
     "--server.port=8501", "--server.address=0.0.0.0", \
     "--server.headless=true", "--browser.gatherUsageStats=false"]
