from django.urls import path
from . import views

urlpatterns = [
    path('', views.home, name='landing'),
    path('marketplace/', views.marketplace,name="marketplace"),
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("signup/", views.signup_view, name="signup"),
    # path("kyc-verification/", views.kyc_verification, name="kyc_verification"),
    path('save_listing_step/<int:step>/', views.save_listing_step, name='save_listing_step'),
    path('submit_listing/', views.submit_listing, name='submit_listing'),
]
