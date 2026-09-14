from django.urls import path

from market import views

app_name = "market"

urlpatterns = [
    path("offers/", views.offers, name="offers"),
    path("offers/<uuid:public_id>/", views.offer_detail, name="offer_detail"),
    path("offers/<uuid:public_id>/<str:action>/", views.respond, name="respond"),
    path("listing/<uuid:public_id>/offer/", views.make_offer, name="make_offer"),
    path("listing/<uuid:public_id>/buy/", views.buy_shares, name="buy_shares"),
    path("listing/<uuid:public_id>/sell/", views.sell_shares, name="sell_shares"),
    path("portfolio/", views.portfolio, name="portfolio"),
    path("activity/", views.activity, name="activity"),
]
