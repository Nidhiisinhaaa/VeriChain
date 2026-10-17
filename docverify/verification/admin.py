from django.contrib import admin

from .models import Document, VerificationLog


@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    list_display = (
        'title', 'original_filename', 'document_type', 'status',
        'is_suspected_forged', 'forgery_score', 'owner', 'registered_at', 'created_at',
    )
    list_filter = ('status', 'document_type', 'is_suspected_forged')
    search_fields = ('title', 'original_filename', 'sha256_hash', 'tx_hash', 'owner__username')
    readonly_fields = (
        'uuid', 'sha256_hash', 'extracted_text', 'forgery_score', 'forgery_flags',
        'tx_hash', 'block_number', 'registered_at', 'created_at', 'updated_at',
    )
    ordering = ('-created_at',)


@admin.register(VerificationLog)
class VerificationLogAdmin(admin.ModelAdmin):
    list_display = ('submitted_hash', 'result', 'matched_document', 'forgery_score', 'ip_address', 'checked_by', 'created_at')
    list_filter = ('result',)
    search_fields = ('submitted_hash', 'ip_address', 'checked_by__username')
    readonly_fields = ('created_at',)
    ordering = ('-created_at',)
