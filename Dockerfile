FROM python:3.12-slim
WORKDIR /app
COPY server.py ./
COPY config ./config
COPY static ./static
ENV PORT=8000 DB_PATH=/data/survey.db
# Mount a persistent volume here, or responses are lost on restart.
VOLUME /data
EXPOSE 8000
CMD ["python3", "server.py"]
