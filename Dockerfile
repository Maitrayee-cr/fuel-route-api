FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8000
# Assessment demo only; use a production WSGI server for deployment.
CMD ["sh", "-c", "python manage.py migrate && python manage.py createcachetable && python manage.py import_stations data/fuel-prices.csv && python manage.py import_coordinates data/verified-coordinates.json && python manage.py runserver 0.0.0.0:8000 --noreload"]
