from django.contrib import admin
from django import forms
from django.core.exceptions import ValidationError
from django.forms.models import BaseInlineFormSet

from .models import Art, DefinitionRevision, FacilityType, Item, PlaceType, Realm, Recipe, RecipeIngredient
from .validation import validate_material_entries


class DefinitionForm(forms.ModelForm):
    expected_revision = forms.IntegerField(required=False, min_value=1, widget=forms.HiddenInput)

    class Meta:
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.is_bound and "entry_kind" not in self.data:
            self.data = self.data.copy()
            self.data["entry_kind"] = self.instance.entry_kind
        if self.instance.pk:
            self.fields["expected_revision"].initial = self.instance.revision

    def clean_aliases(self):
        if "aliases" not in self.data:
            return self.instance.aliases
        value = self.cleaned_data.get("aliases")
        return [] if value is None else value

    def clean_sources(self):
        if "sources" not in self.data:
            return self.instance.sources
        value = self.cleaned_data.get("sources")
        return [] if value is None else value

    def clean(self):
        cleaned = super().clean()
        expected = cleaned.get("expected_revision")
        if self.instance.pk and expected is not None:
            current = type(self.instance).objects.get(pk=self.instance.pk)
            if current.revision != expected:
                raise ValidationError("资料已被更新，请重新加载后再编辑；本次未覆盖新内容。")
        return cleaned


class DefinitionAdmin(admin.ModelAdmin):
    form = DefinitionForm
    list_display = ("key", "name", "entry_kind", "revision", "source")
    search_fields = ("key", "name")
    list_filter = ("entry_kind", "revision")

    def get_readonly_fields(self, request, obj=None):
        return ("key", "revision", "content_hash") if obj is not None else ("revision", "content_hash")

    def save_model(self, request, obj, form, change):
        expected = form.cleaned_data.get("expected_revision")
        if expected is not None:
            obj._expected_revision = expected
        super().save_model(request, obj, form, change)


@admin.register(Realm)
class RealmAdmin(DefinitionAdmin):
    list_display = ("key", "name", "entry_kind", "order", "revision")


@admin.register(Item)
class ItemAdmin(DefinitionAdmin):
    list_display = ("key", "name", "type", "entry_kind", "minimum_realm", "revision")
    list_filter = ("type", "entry_kind", "minimum_realm", "revision")


@admin.register(FacilityType)
class FacilityTypeAdmin(DefinitionAdmin):
    pass


@admin.register(Art)
class ArtAdmin(DefinitionAdmin):
    list_display = ("key", "name", "kind", "entry_kind", "minimum_realm", "revision")
    list_filter = ("kind", "entry_kind", "minimum_realm", "revision")


@admin.register(PlaceType)
class PlaceTypeAdmin(DefinitionAdmin):
    pass


class MaterialFormSet(BaseInlineFormSet):
    def clean(self):
        super().clean()
        if any(self.errors):
            return
        # Django supplies initial values (including raw FK IDs) for read-only
        # inline forms. Validated instances work for both editable and read-only
        # rows, and cannot take forged read-only values from the POST body.
        entries = [
            {"item": form.instance.item, "quantity": form.instance.quantity}
            for form in self.forms
            if (form.instance.pk or form.has_changed()) and not form.cleaned_data.get("DELETE")
        ]
        validate_material_entries(entries)


class MaterialInline(admin.TabularInline):
    model = RecipeIngredient
    formset = MaterialFormSet
    extra = 1
    min_num = 1


@admin.register(Recipe)
class RecipeAdmin(DefinitionAdmin):
    list_display = ("key", "name", "entry_kind", "minimum_realm", "facility_type", "output_item", "output_quantity", "revision")
    list_filter = ("entry_kind", "minimum_realm", "facility_type", "revision")
    inlines = (MaterialInline,)

    def has_add_permission(self, request):
        # Without ingredient add permission Django hides the inline entirely,
        # which would bypass its mandatory-material validation on creation.
        return super().has_add_permission(request) and request.user.has_perm("cultivation.add_recipeingredient")

    def save_model(self, request, obj, form, change):
        obj._defer_revision = True
        super().save_model(request, obj, form, change)

    def save_related(self, request, form, formsets, change):
        from .revisioning import defer_recipe_children
        with defer_recipe_children(form.instance.pk):
            super().save_related(request, form, formsets, change)
        form.instance._defer_revision = False
        form.instance.save()


@admin.register(DefinitionRevision)
class DefinitionRevisionAdmin(admin.ModelAdmin):
    list_display = ("category", "definition_key", "revision", "created_at")
    list_filter = ("category",)
    search_fields = ("definition_key",)
    readonly_fields = ("category", "definition_key", "revision", "content_hash", "content", "created_at")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
