from django.contrib import admin
from django.urls import include, path

admin.site.site_header = "FATONTINE CASH — Administration technique"
admin.site.site_title = "FATONTINE CASH"

urlpatterns = [
    path("gestion-technique/", admin.site.urls),
    path("", include("tontine.urls")),
]

handler404 = "tontine.views.error_404"
