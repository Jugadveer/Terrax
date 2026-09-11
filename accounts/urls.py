from django.urls import path

from accounts import views

app_name = "accounts"

urlpatterns = [
    path("sign-up/", views.sign_up, name="sign_up"),
    path("sign-in/", views.sign_in, name="login"),
    path("sign-out/", views.sign_out, name="logout"),

    path("settings/", views.settings_view, name="settings"),
    path("settings/notifications/", views.save_preferences, name="save_preferences"),

    path("identity/", views.identity, name="identity"),
    path("wallet/", views.wallet, name="wallet"),
    path("wallet/disconnect/", views.disconnect_wallet, name="disconnect_wallet"),

    path("listings/", views.my_listings, name="my_listings"),
    path("watchlist/", views.watchlist, name="watchlist"),
    path("searches/", views.saved_searches, name="saved_searches"),
    path("searches/save/", views.save_search, name="save_search"),
    path("searches/<int:pk>/delete/", views.delete_saved_search, name="delete_saved_search"),

    path("notifications/", views.notifications, name="notifications"),
    path("notifications/read/", views.read_notifications, name="read_notifications"),

    path("seller/<uuid:public_id>/", views.public_profile, name="public_profile"),
]
