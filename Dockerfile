FROM python:3.12-slim
WORKDIR /app
COPY server.py index.html admin.html login.html manage.html questions.json ASEANEmblem11_RGB.svg ./
ENV PORT=8000 DB_PATH=/data/survey.db
# Mount a persistent volume here, or responses are lost on restart.
VOLUME /data
EXPOSE 8000
CMD ["python3", "server.py"]
