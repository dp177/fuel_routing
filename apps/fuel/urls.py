from django.urls import path
from apps.fuel.views import RouteFuelPlanView

app_name = "fuel"

urlpatterns = [
    path("route/fuel-plan/", RouteFuelPlanView.as_view(), name="route-fuel-plan"),
]
