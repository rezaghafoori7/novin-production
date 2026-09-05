FROM python:3.12-slim
WORKDIR /app
COPY . /app
ENV NOVIN_HOST=0.0.0.0
ENV NOVIN_DATA_DIR=/data
RUN mkdir -p /data
EXPOSE 8080
CMD ["python", "server.py"]
