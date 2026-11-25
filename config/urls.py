"""
URL configuration for config project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""

from django.contrib import admin
from django.urls import include, path

from accounts.views import home
from config.views import healthz

urlpatterns = [
    path("", home, name="home"),
    path("accounts/api-keys/", include("api.web_urls")),  # 설정 > API 키 (#102)
    path("accounts/", include("accounts.urls")),
    path("students/", include("students.urls")),
    path("", include("lessons.urls")),
    path("inbox/", include("inbox.urls")),
    path("me/", include("portal.urls")),  # 수강생 화면 (#80)
    path("healthz/", healthz, name="healthz"),
    path("api/", include("api.urls")),  # 외부 API (#102)
    path("admin/", admin.site.urls),
]

handler404 = "config.views.page_not_found"
handler500 = "config.views.server_error"
