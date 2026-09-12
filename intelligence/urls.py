from django.urls import path

from intelligence import views

app_name = "intelligence"

urlpatterns = [
    path("search/", views.natural_search, name="natural_search"),
    path("status/", views.status, name="status"),
]
