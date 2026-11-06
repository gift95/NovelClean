FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    WORK_DIR=/data/work

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY cleaner ./cleaner
COPY templates ./templates
COPY app.py cli.py ./

RUN mkdir -p /data/work
VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/healthz').read()" || exit 1

# 单 worker + 多线程：成品临时文件按内存外的目录存放，多进程会让下载链接落到没有该目录的进程
CMD ["gunicorn", "-b", "0.0.0.0:8000", "-w", "1", "--threads", "4", "--timeout", "300", "app:app"]
