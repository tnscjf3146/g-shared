# AI Scheduler (FastAPI + XGBoost)
FROM python:3.11-slim
WORKDIR /app

# 의존성 먼저 설치 (requirements.txt 안 바뀌면 캐시 재사용)
# ⚠️ requirements.txt는 반드시 UTF-8 인코딩이어야 함 (UTF-16이면 pip가 못 읽음)
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# 앱 코드 복사
COPY app ./app
COPY models ./models
COPY data ./data

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
