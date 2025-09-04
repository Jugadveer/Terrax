from django.contrib import admin
from django.urls import path, include 

urlpatterns = [
    path('admin/', admin.site.urls),
    path('',include('marketplace.urls')),
    # path('users/', include('users.urls')),
    path('dashboard/', include('dashboard.urls')),
]
