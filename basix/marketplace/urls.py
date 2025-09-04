from django.urls import path
from . import views

urlpatterns = [
    path('', views.home, name='landing'),
    path('marketplace/', views.marketplace,name="marketplace"),
]
