from django.urls import path

from core import views

app_name = "core"

urlpatterns = [
    path("", views.home, name="home"),
    path("dashboard/", views.dashboard, name="dashboard"),
    path("dashboard/dismiss-onboarding/", views.dismiss_onboarding, name="dismiss_onboarding"),
    path("theme/", views.set_theme, name="set_theme"),

    path("about/", views.about, name="about"),
    path("how-it-works/", views.how_it_works, name="how_it_works"),
    path("help/", views.help_centre, name="help"),
    path("contact/", views.contact, name="contact"),
    path("privacy/", views.privacy, name="privacy"),
    path("terms/", views.terms, name="terms"),

    path("health/", views.health, name="health"),
]
