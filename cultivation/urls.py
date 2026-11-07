from rest_framework.routers import DefaultRouter

from .views import ArtViewSet, DefinitionRevisionViewSet, FacilityTypeViewSet, ItemViewSet, PlaceTypeViewSet, RealmViewSet, RecipeViewSet

app_name = "cultivation"
router = DefaultRouter()
router.register("realms", RealmViewSet, basename="realm")
router.register("items", ItemViewSet, basename="item")
router.register("arts", ArtViewSet, basename="art")
router.register("place-types", PlaceTypeViewSet, basename="place-type")
router.register("facility-types", FacilityTypeViewSet, basename="facility-type")
router.register("recipes", RecipeViewSet, basename="recipe")
router.register("revisions", DefinitionRevisionViewSet, basename="revision")
urlpatterns = router.urls
