from __future__ import annotations

from django import forms
from django.utils.translation import gettext_lazy as _

from .models import CrmCompany, CrmContact, CrmFieldDefinition, CrmImportTemplate


class CrmContactForm(forms.ModelForm):
    class Meta:
        model = CrmContact
        fields = ["display_name", "first_name", "last_name", "primary_email", "primary_phone", "title", "source"]


class CrmCompanyForm(forms.ModelForm):
    class Meta:
        model = CrmCompany
        fields = ["name", "website", "primary_phone", "source"]


class CrmFieldDefinitionForm(forms.ModelForm):
    class Meta:
        model = CrmFieldDefinition
        fields = [
            "target_object",
            "label",
            "key",
            "field_type",
            "required",
            "searchable",
            "filterable",
            "pii",
            "options",
            "schema",
        ]


class CrmImportUploadForm(forms.Form):
    source_file = forms.FileField()
    template_name = forms.CharField(required=False, max_length=255)
    existing_template = forms.ModelChoiceField(queryset=CrmImportTemplate.objects.none(), required=False)

    def __init__(self, *args, business_profile=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["existing_template"].queryset = CrmImportTemplate.objects.filter(business_profile=business_profile).order_by("name")
        self.fields["existing_template"].empty_label = _("Suggested automatically")


class CrmImportQueueForm(forms.Form):
    source_file_id = forms.UUIDField()
    template_id = forms.UUIDField(required=False)
    template_name = forms.CharField(required=False, max_length=255)
    save_as_template = forms.BooleanField(required=False)
