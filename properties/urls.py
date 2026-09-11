from django.urls import path

from properties import views

app_name = "properties"

urlpatterns = [
    path("", views.index, name="index"),
    path("map/", views.map_view, name="map"),
    path("compare/", views.compare, name="compare"),

    # The wizard. `new/` starts one; every later step carries the listing id.
    path("new/", views.wizard, name="wizard"),
    path("<uuid:public_id>/edit/<int:step>/", views.wizard, name="wizard_step"),
    path("<uuid:public_id>/submit/", views.submit, name="submit"),
    path("<uuid:public_id>/delete/", views.delete, name="delete"),
    path("<uuid:public_id>/photo/<int:image_id>/delete/", views.delete_image, name="delete_image"),
    path("<uuid:public_id>/photo/<int:image_id>/cover/", views.set_cover, name="set_cover"),
    path("<uuid:public_id>/document/<int:document_id>/delete/", views.delete_document, name="delete_document"),

    # Actions
    path("<uuid:public_id>/watch/", views.toggle_watch, name="watch"),
    path("<uuid:public_id>/describe/", views.draft_description, name="describe"),
    path("<uuid:public_id>/ask/", views.ask, name="ask"),
    path("<uuid:public_id>/revalue/", views.revalue, name="revalue"),

    # Detail last, so it does not shadow the routes above.
    path("<uuid:public_id>/", views.detail, name="detail"),
]
