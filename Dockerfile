# life-change 대시보드 + 봇 (Python 단일 컨테이너)
# 참고: sbshop/can-agent 와 동일한 shared-net 규칙을 따르되 내용물만 Python.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Asia/Seoul

WORKDIR /app

# 의존성 먼저 (레이어 캐시)
COPY upbit-rbi-bot/requirements.txt upbit-rbi-bot/constraints.txt ./
RUN pip install --no-cache-dir -r requirements.txt -c constraints.txt

# 앱 코드
COPY upbit-rbi-bot/ .

# 로그/DB 디렉토리 (compose 볼륨으로 마운트되어 재시작에도 유지)
RUN mkdir -p /app/logs

EXPOSE 8080

# nginx 가 /life-change 경로로 프록시 → 앱은 스스로 /life-change 프리픽스 아래 서빙
CMD ["uvicorn", "dashboard.app:app", "--host", "0.0.0.0", "--port", "8080"]
