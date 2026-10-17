import uuid
from django.db import models
from django.contrib.auth.models import User


def upload_path(instance, filename):
    return f"uploads/{instance.uuid}/{filename}"


class Document(models.Model):
    DOCUMENT_TYPES = [
        ('certificate', 'Academic Certificate'),
        ('marksheet', 'Marksheet / Transcript'),
        ('legal', 'Legal Document'),
        ('id_proof', 'ID Proof'),
        ('other', 'Other'),
    ]

    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('registered', 'Registered on Blockchain'),
        ('failed', 'Registration Failed'),
        ('revoked', 'Revoked'),
    ]

    uuid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name='documents')
    file = models.FileField(upload_to=upload_path)
    original_filename = models.CharField(max_length=255)
    document_type = models.CharField(max_length=20, choices=DOCUMENT_TYPES, default='certificate')
    title = models.CharField(max_length=255, blank=True)

    # Extracted content
    extracted_text = models.TextField(blank=True)

    # Hashing
    sha256_hash = models.CharField(max_length=64, unique=True, db_index=True)

    # Forgery detection results (JSON-ish text fields kept simple for SQLite)
    forgery_score = models.FloatField(null=True, blank=True)  # 0 (clean) - 1 (likely tampered)
    forgery_flags = models.TextField(blank=True)  # comma-separated flags/reasons
    is_suspected_forged = models.BooleanField(default=False)

    # Blockchain
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    tx_hash = models.CharField(max_length=100, blank=True)
    block_number = models.BigIntegerField(null=True, blank=True)
    registered_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.title or self.original_filename} ({self.sha256_hash[:10]}...)"


class VerificationLog(models.Model):
    """Every time someone checks a document (uploaded fresh or by hash), log it."""
    RESULT_CHOICES = [
        ('valid', 'Valid - Matches Blockchain Record'),
        ('not_found', 'Not Found on Blockchain'),
        ('revoked', 'Revoked'),
        ('tampered', 'Hash Mismatch / Possibly Tampered'),
    ]

    checked_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    submitted_hash = models.CharField(max_length=64)
    matched_document = models.ForeignKey(Document, on_delete=models.SET_NULL, null=True, blank=True, related_name='verification_logs')
    result = models.CharField(max_length=20, choices=RESULT_CHOICES)
    forgery_score = models.FloatField(null=True, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.submitted_hash[:10]}... -> {self.result}"
