from django.urls import path

from api import web_views

app_name = "api_keys"

urlpatterns = [
    path("", web_views.key_list, name="list"),
    path("new/", web_views.key_create, name="create"),
    path("<int:pk>/extend/", web_views.key_extend, name="extend"),
    path("<int:pk>/revoke/", web_views.key_revoke, name="revoke"),
]
