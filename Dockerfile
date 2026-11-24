FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN DJANGO_SECRET_KEY=build python manage.py collectstatic --noinput \
 && useradd -r -u 1001 fatontine && mkdir -p /data/private_media /data/backups && chown -R fatontine /data
USER fatontine
ENV PRIVATE_MEDIA_ROOT=/data/private_media BACKUP_DIR=/data/backups
EXPOSE 8000
CMD ["sh", "-c", "python manage.py migrate --noinput && gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers 3 --access-logfile -"]
