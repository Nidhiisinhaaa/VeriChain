import re

from django import forms

from .models import Document

ALLOWED_EXTENSIONS = ('.pdf', '.png', '.jpg', '.jpeg', '.tiff', '.bmp')


class DocumentUploadForm(forms.ModelForm):
    class Meta:
        model = Document
        fields = ['title', 'document_type', 'file']
        widgets = {
            'title': forms.TextInput(attrs={
                'placeholder': 'e.g. B.Sc Computer Science Degree Certificate',
            }),
            'document_type': forms.Select(),
            'file': forms.ClearableFileInput(attrs={
                'accept': '.pdf,.png,.jpg,.jpeg,.tiff,.bmp',
            }),
        }

    def clean_file(self):
        file = self.cleaned_data['file']
        ext = ('.' + file.name.rsplit('.', 1)[-1]).lower() if '.' in file.name else ''
        if ext not in ALLOWED_EXTENSIONS:
            raise forms.ValidationError(
                f"Unsupported file type '{ext}'. Allowed: {', '.join(ALLOWED_EXTENSIONS)}"
            )
        return file


class VerifyByHashForm(forms.Form):
    hash = forms.CharField(
        label='Document hash (SHA-256)',
        min_length=64,
        max_length=66,  # allow an optional 0x prefix
        widget=forms.TextInput(attrs={
            'placeholder': 'e.g. 9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08',
            'autocomplete': 'off',
        }),
    )

    def clean_hash(self):
        value = self.cleaned_data['hash'].strip().lower()
        if value.startswith('0x'):
            value = value[2:]
        if len(value) != 64 or not re.fullmatch(r'[0-9a-f]{64}', value):
            raise forms.ValidationError('That does not look like a valid 64-character SHA-256 hash.')
        return value


class VerifyByFileForm(forms.Form):
    file = forms.FileField(
        label='Document to verify',
        widget=forms.ClearableFileInput(attrs={
            'accept': '.pdf,.png,.jpg,.jpeg,.tiff,.bmp',
        }),
    )

    def clean_file(self):
        file = self.cleaned_data['file']
        ext = ('.' + file.name.rsplit('.', 1)[-1]).lower() if '.' in file.name else ''
        if ext not in ALLOWED_EXTENSIONS:
            raise forms.ValidationError(
                f"Unsupported file type '{ext}'. Allowed: {', '.join(ALLOWED_EXTENSIONS)}"
            )
        return file
