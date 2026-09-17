from django.urls import path

from chain import views

app_name = "chain"

urlpatterns = [
    path("", views.status, name="status"),
    path("verify/<uuid:public_id>/", views.verify, name="verify"),
]
