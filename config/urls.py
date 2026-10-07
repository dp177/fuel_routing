from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path
from django.views.generic import TemplateView
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularSwaggerView,
)


def health_check(request):
    return JsonResponse({"status": "healthy", "service": "spotter_fuel_routing"})


urlpatterns = [
    path("admin/", admin.site.urls),
    path("health/", health_check, name="health-check"),
    # Interactive Demo
    path("", TemplateView.as_view(template_name="demo.html"), name="home"),
    path("demo/", TemplateView.as_view(template_name="demo.html"), name="demo"),
    # API endpoints
    path("api/v1/", include("apps.fuel.urls")),
    # OpenAPI Documentation
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
]
