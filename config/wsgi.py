"""WSGI config for spotter_fuel_routing project."""
import os
from django.core.wsgi import get_wsgi_application

settings_module = "config.settings.prod" if os.getenv("VERCEL") else "config.settings.dev"
os.environ.setdefault("DJANGO_SETTINGS_MODULE", settings_module)

application = get_wsgi_application()

# Alias for Vercel serverless Python runtime entrypoint
app = application
