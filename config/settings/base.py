import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()
BASE_DIR = Path(__file__).resolve().parent.parent.parent
SECRET_KEY = os.getenv('SECRET_KEY') or os.getenv('DJANGO_SECRET_KEY', 'dev-secret-key')
DEBUG = os.getenv('DEBUG', 'True') == 'True'
ALLOWED_HOSTS = [h.strip() for h in os.getenv('ALLOWED_HOSTS', 'localhost,127.0.0.1').split(',') if h.strip()]

INSTALLED_APPS = [
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.staticfiles',
    'corsheaders',
    'rest_framework',
    'modules.gestion_usuarios_acceso_suscripcion',
    'modules.gestion_cocinero',
    'modules.marketplace_platos',
    'modules.storage_uploads',
]

MIDDLEWARE = [
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'
TEMPLATES = [{
    'BACKEND': 'django.template.backends.django.DjangoTemplates',
    'DIRS': [],
    'APP_DIRS': True,
    'OPTIONS': {'context_processors': ['django.template.context_processors.request']},
}]
WSGI_APPLICATION = 'config.wsgi.application'
ASGI_APPLICATION = 'config.asgi.application'

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': os.getenv('DB_NAME', 'postgres'),
        'USER': os.getenv('DB_USER', 'postgres'),
        'PASSWORD': os.getenv('DB_PASSWORD', ''),
        'HOST': os.getenv('DB_HOST', 'db.pimmweiqnensrevyzvqn.supabase.co'),
        'PORT': os.getenv('DB_PORT', '5432'),
        'OPTIONS': {'sslmode': 'require'},
    }
}

# Legacy MongoDB settings are kept only for the one-time migration command.
MONGODB_URI = os.getenv('MONGODB_URI', '')
MONGODB_DB = os.getenv('MONGODB_DB', 'homechef')

LANGUAGE_CODE = 'es-bo'
TIME_ZONE = 'America/La_Paz'
USE_I18N = True
USE_TZ = True
STATIC_URL = 'static/'
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': ('shared.security.jwt_authentication.SupabaseJWTAuthentication',),
    'DEFAULT_PERMISSION_CLASSES': ('rest_framework.permissions.AllowAny',),
}

CORS_ALLOWED_ORIGINS = [x.strip() for x in os.getenv('CORS_ALLOWED_ORIGINS', 'http://localhost:5173').split(',') if x.strip()]
AI_SERVICE_URL = os.getenv('AI_SERVICE_URL', 'http://localhost:8001')
AI_SERVICE_TOKEN = os.getenv('AI_SERVICE_TOKEN', '')

SUPABASE_URL = os.getenv('SUPABASE_URL', 'https://pimmweiqnensrevyzvqn.supabase.co')
SUPABASE_ANON_KEY = os.getenv('SUPABASE_ANON_KEY', os.getenv('SUPABASE_KEY', ''))
SUPABASE_KEY = os.getenv('SUPABASE_KEY', SUPABASE_ANON_KEY)
SUPABASE_SERVICE_ROLE_KEY = os.getenv('SUPABASE_SERVICE_ROLE_KEY', os.getenv('SUPABASE_KEY', ''))
SUPABASE_BUCKET = os.getenv('SUPABASE_BUCKET', 'uploads')

STRIPE_MODE = os.getenv('STRIPE_MODE', 'sandbox')
STRIPE_SECRET_KEY = os.getenv('STRIPE_SECRET_KEY', '')
STRIPE_PUBLISHABLE_KEY = os.getenv('STRIPE_PUBLISHABLE_KEY', '')
STRIPE_WEBHOOK_SECRET = os.getenv('STRIPE_WEBHOOK_SECRET', '')
STRIPE_SUCCESS_URL = os.getenv('STRIPE_SUCCESS_URL', 'http://localhost:5173/chef/ai-subscription?payment=stripe_success')
STRIPE_CANCEL_URL = os.getenv('STRIPE_CANCEL_URL', 'http://localhost:5173/chef/ai-subscription?payment=stripe_cancel')

COINGATE_MODE = os.getenv('COINGATE_MODE', 'sandbox')
COINGATE_API_BASE_URL = os.getenv('COINGATE_API_BASE_URL', 'https://api-sandbox.coingate.com/api/v2')
COINGATE_API_TOKEN = os.getenv('COINGATE_API_TOKEN', '')
COINGATE_RECEIVE_CURRENCY = os.getenv('COINGATE_RECEIVE_CURRENCY', 'USD')
COINGATE_CALLBACK_URL = os.getenv(
    'COINGATE_CALLBACK_URL',
    'http://localhost:8000/api/ia/subscription/payments/coingate/callback/',
)
COINGATE_SUCCESS_URL = os.getenv('COINGATE_SUCCESS_URL', 'http://localhost:5173/chef/ai-subscription?payment=coingate_success')
COINGATE_CANCEL_URL = os.getenv('COINGATE_CANCEL_URL', 'http://localhost:5173/chef/ai-subscription?payment=coingate_cancel')
