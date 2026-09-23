FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV HUMAN_QUEUE_DB=/data/human-queue.db
VOLUME ["/data"]
EXPOSE 7482
CMD ["python","-m","humanqueue","serve","--host","0.0.0.0","--port","7482"]
