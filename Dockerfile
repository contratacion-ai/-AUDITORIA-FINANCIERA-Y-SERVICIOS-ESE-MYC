FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DJANGO_ENV=production
WORKDIR /app
RUN useradd --create-home --uid 1000 abc
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN DJANGO_SECRET_KEY=build python manage.py collectstatic --noinput && mkdir -p /app/media && chown -R abc /app/media
USER abc
EXPOSE 8000
CMD ["sh", "-c", "python manage.py migrate --noinput && gunicorn abc_salud.wsgi:application --bind 0.0.0.0:8000 --workers ${GUNICORN_WORKERS:-3} --timeout ${GUNICORN_TIMEOUT:-300}"]
