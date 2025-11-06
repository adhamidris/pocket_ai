from __future__ import annotations

from django import forms

from .models import Customer


class CustomerAdminForm(forms.ModelForm):
    """
    Custom admin form that lets staff enter a plain-text address while we
    continue storing structured JSON behind the scenes.
    """

    primary_address = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 4}),
        help_text="Enter a JSON object or plain text (we'll convert it automatically).",
        label="Primary address",
    )

    class Meta:
        model = Customer
        fields = "__all__"

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        value = self.initial.get("primary_address")
        if isinstance(value, dict):
            # Present a nicer default than the Python dict repr.
            self.initial["primary_address"] = Customer.format_primary_address(value)

    def clean_primary_address(self):
        raw = self.cleaned_data.get("primary_address")
        return Customer.normalize_primary_address(raw)
