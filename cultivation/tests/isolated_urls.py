from django.urls import include, path

urlpatterns = [path("api/library/v1/", include("cultivation.urls"))]
