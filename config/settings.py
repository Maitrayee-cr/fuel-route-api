import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
SECRET_KEY = os.environ.get('DJANGO_SECRET_KEY', 'local-assessment-only-do-not-deploy')
DEBUG = os.environ.get('DJANGO_DEBUG', '1') == '1'
ALLOWED_HOSTS = os.environ.get('DJANGO_ALLOWED_HOSTS', 'localhost,127.0.0.1,testserver').split(',')
INSTALLED_APPS = ['django.contrib.staticfiles', 'planner']
MIDDLEWARE = ['django.middleware.security.SecurityMiddleware', 'django.middleware.common.CommonMiddleware', 'django.middleware.csrf.CsrfViewMiddleware']
ROOT_URLCONF = 'config.urls'
TEMPLATES = [{'BACKEND': 'django.template.backends.django.DjangoTemplates', 'APP_DIRS': True}]
WSGI_APPLICATION = 'config.wsgi.application'
DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': os.environ.get('DATABASE_PATH', BASE_DIR / 'db.sqlite3'), 'OPTIONS': {'timeout': 20}}}
CACHES = {'default': {'BACKEND': 'django.core.cache.backends.db.DatabaseCache', 'LOCATION': 'api_cache'}}
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
STATIC_URL = 'static/'
TIME_ZONE = 'UTC'
USE_TZ = True
DATA_UPLOAD_MAX_MEMORY_SIZE = 16384
GEOAPIFY_API_KEY = os.environ.get('GEOAPIFY_API_KEY', '')
OSRM_URL = os.environ.get('OSRM_URL', 'https://routing.openstreetmap.de/routed-car').rstrip('/')
HTTP_USER_AGENT = os.environ.get('HTTP_USER_AGENT', 'FuelRouteAssessment/1.0 (local development)')
ROUTE_CACHE_SECONDS = 86400
CORRIDOR_MILES = 2.0
FALLBACK_CORRIDOR_MILES = 10.0
MAX_WAYPOINTS = 25
PROVIDER_QUEUE_SECONDS = 3
ENDPOINT_SNAP_METERS = 1500
