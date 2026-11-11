FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
# Пока бот и pipeline запускаются раздельно; в четверг здесь будет общий app.main
CMD ["python", "-m", "app.bot.main"]
