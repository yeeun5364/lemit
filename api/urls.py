from django.contrib.auth.decorators import login_not_required
from django.urls import path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

from api import views

app_name = "api"

urlpatterns = [
    path("v1/me/", views.MeView.as_view(), name="me"),
    # 문서 (공개, 키 불필요)
    path("schema/", login_not_required(SpectacularAPIView.as_view()), name="schema"),
    path("docs/", login_not_required(SpectacularSwaggerView.as_view(url_name="api:schema")), name="docs"),
    path("docs/errors/", views.error_docs, name="error_docs"),
]
