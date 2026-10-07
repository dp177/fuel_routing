"""Production settings for Vercel and cloud deployments."""
import os
from .base import *  # noqa: F403

DEBUG = os.getenv("DJANGO_DEBUG", "False").lower() in ("true", "1", "yes")
