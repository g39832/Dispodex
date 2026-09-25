from django import forms

from core.skus import normalize_sku
from inventory.models import Item, Status
from inventory.services.items import ItemError, parse_price


class IntakeForm(forms.ModelForm):
    """Validates the intake sheet. The template draws its own inputs; this does the checking."""

    price = forms.CharField(required=False)
    status = forms.CharField(required=False)

    class Meta:
        model = Item
        fields = [
            "sku", "status", "what_is_it", "ebay_category", "ebay_category_path", "ebay_category_id",
            "date_received", "source", "where_it_goes", "functional", "condition",
            "cords_adapters", "keep_items_together", "picture_taken", "power_on",
            "brand_model", "ram", "ssd_gb", "cpu", "os", "compatible_os", "battery_health", "graphics_card",
            "screen_resolution", "diagnostics_test_ran", "wifi_card_installed", "quantity", "notes",
        ]
        error_messages = {
            "sku": {"required": "Please fill in the SKU."},
            "what_is_it": {"required": 'Please enter a value for "What is it?".'},
        }

    def clean_sku(self):
        sku = normalize_sku(self.cleaned_data.get("sku"))
        if not sku:
            raise forms.ValidationError("Please fill in the SKU.")
        return sku

    def clean_what_is_it(self):
        value = (self.cleaned_data.get("what_is_it") or "").strip()
        if not value:
            raise forms.ValidationError('Please enter a value for "What is it?".')
        return value

    def clean_status(self):
        value = (self.cleaned_data.get("status") or "").strip()
        return value or Status.INTAKE

    def clean_quantity(self):
        return max(1, self.cleaned_data.get("quantity") or 1)

    def clean_price(self):
        try:
            return parse_price(self.cleaned_data.get("price"))
        except ItemError as exc:
            raise forms.ValidationError(str(exc)) from exc

    def clean(self):
        data = super().clean()
        for name in ("ebay_category", "ebay_category_path", "ebay_category_id", "source", "where_it_goes",
                     "brand_model", "ram", "ssd_gb", "cpu", "os", "battery_health", "graphics_card",
                     "screen_resolution"):
            if isinstance(data.get(name), str):
                data[name] = data[name].strip()
        return data

    def item_values(self) -> dict:
        """Cleaned values ready to copy onto an Item."""
        values = {name: self.cleaned_data.get(name) for name in self.Meta.fields}
        values["price"] = self.cleaned_data.get("price")
        for name, value in values.items():
            if value is None and name not in ("date_received", "price"):
                values[name] = ""
        return values
