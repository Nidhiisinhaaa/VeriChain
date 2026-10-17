import hashlib
import os
import tempfile
from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from . import blockchain_utils
from .blockchain_utils import BlockchainError
from .forgery_detection import analyze_document
from .forms import DocumentUploadForm, VerifyByFileForm, VerifyByHashForm
from .models import Document, VerificationLog
from .ocr_utils import compute_sha256, extract_text


def issuer_required(view_func):
    """Only Django staff accounts ('is_staff') may register documents as an issuer.
    This reuses Django's built-in User.is_staff flag instead of introducing a
    separate Issuer/role model, per the project's "no extra auth complexity" scope.
    """
    @wraps(view_func)
    @login_required
    def wrapper(request, *args, **kwargs):
        if not request.user.is_staff:
            messages.error(
                request,
                "Your account is not authorized to register documents. "
                "Ask an administrator to grant you issuer (staff) access from the Django admin."
            )
            return redirect('home')
        return view_func(request, *args, **kwargs)
    return wrapper


def _get_client_ip(request):
    forwarded = request.META.get('HTTP_X_FORWARDED_FOR')
    if forwarded:
        return forwarded.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR')


def _hash_uploaded_file(uploaded_file) -> str:
    sha256 = hashlib.sha256()
    for chunk in uploaded_file.chunks():
        sha256.update(chunk)
    uploaded_file.seek(0)  # rewind so the form/model field can still read+save the content
    return sha256.hexdigest()


# ---------------------------------------------------------------------------
# Public pages
# ---------------------------------------------------------------------------
def home(request):
    stats = {
        'total_registered': Document.objects.filter(status='registered').count(),
        'total_revoked': Document.objects.filter(status='revoked').count(),
        'total_verifications': VerificationLog.objects.count(),
    }
    return render(request, 'verification/home.html', {'stats': stats})


# ---------------------------------------------------------------------------
# Registration (issuers only)
# ---------------------------------------------------------------------------
@issuer_required
def register_document(request):
    if request.method == 'POST' and request.POST.get('document_uuid'):
        # Second step of the flow: the user already saw a forgery warning for
        # this (already-saved) pending document and is now confirming or cancelling.
        document = get_object_or_404(
            Document, uuid=request.POST['document_uuid'], owner=request.user, status='pending'
        )
        if request.POST.get('action') == 'cancel':
            document.file.delete(save=False)
            document.delete()
            messages.info(request, "Registration cancelled and the upload was discarded.")
            return redirect('register_document')

        if not request.POST.get('confirm_forgery'):
            messages.error(request, "Please tick the confirmation box to proceed despite the forgery warning, or cancel.")
            return _render_forgery_warning(request, document)

        return _finalize_registration(request, document)

    if request.method == 'POST':
        form = DocumentUploadForm(request.POST, request.FILES)
        if form.is_valid():
            uploaded = form.cleaned_data['file']
            doc_hash = _hash_uploaded_file(uploaded)

            existing = Document.objects.filter(sha256_hash=doc_hash).first()
            if existing:
                messages.warning(request, "This exact file has already been registered.")
                return redirect('document_detail', doc_uuid=existing.uuid)

            document = form.save(commit=False)
            document.owner = request.user
            document.original_filename = uploaded.name
            document.sha256_hash = doc_hash
            document.status = 'pending'
            document.save()

            document.extracted_text = extract_text(document.file.path)
            report = analyze_document(document.file.path)
            document.forgery_score = report['score']
            document.forgery_flags = ', '.join(report['flags'])
            document.is_suspected_forged = report['is_suspected']
            document.save(update_fields=['extracted_text', 'forgery_score', 'forgery_flags', 'is_suspected_forged'])

            if report['is_suspected']:
                return _render_forgery_warning(request, document)

            return _finalize_registration(request, document)
    else:
        form = DocumentUploadForm()

    return render(request, 'verification/register.html', {'form': form})


def _render_forgery_warning(request, document):
    flags = document.forgery_flags.split(', ') if document.forgery_flags else []
    return render(request, 'verification/register.html', {
        'form': DocumentUploadForm(),
        'show_warning': True,
        'document': document,
        'flags': flags,
    })


def _finalize_registration(request, document):
    """Send the document's hash to the chain and update the local record accordingly."""
    metadata_uri = document.title or document.original_filename
    try:
        result = blockchain_utils.register_document_onchain(
            document.sha256_hash, document.document_type, metadata_uri
        )
    except BlockchainError as exc:
        document.status = 'failed'
        document.save(update_fields=['status'])
        messages.error(request, f"Blockchain registration failed: {exc}")
        return redirect('document_detail', doc_uuid=document.uuid)

    document.status = 'registered'
    document.tx_hash = result['tx_hash']
    document.block_number = result['block_number']
    document.registered_at = timezone.now()
    document.save(update_fields=['status', 'tx_hash', 'block_number', 'registered_at'])
    messages.success(request, "Document successfully registered on the blockchain.")
    return redirect('document_detail', doc_uuid=document.uuid)


@login_required
def retry_registration(request, doc_uuid):
    document = get_object_or_404(Document, uuid=doc_uuid, owner=request.user)
    if request.method != 'POST':
        return redirect('document_detail', doc_uuid=document.uuid)
    if document.status not in ('pending', 'failed'):
        messages.info(request, "This document has already been registered.")
        return redirect('document_detail', doc_uuid=document.uuid)
    return _finalize_registration(request, document)


# ---------------------------------------------------------------------------
# Verification (public)
# ---------------------------------------------------------------------------
def verify_document(request):
    if request.method == 'POST':
        mode = request.POST.get('mode')
        ip = _get_client_ip(request)
        user = request.user if request.user.is_authenticated else None

        if mode == 'hash':
            hash_form = VerifyByHashForm(request.POST)
            if hash_form.is_valid():
                result = _verify_by_hash(hash_form.cleaned_data['hash'], ip, user)
                return render(request, 'verification/verify_result.html', {'result': result})
            return render(request, 'verification/verify.html', {
                'hash_form': hash_form, 'file_form': VerifyByFileForm(), 'active_tab': 'hash',
            })

        if mode == 'file':
            file_form = VerifyByFileForm(request.POST, request.FILES)
            if file_form.is_valid():
                result = _verify_by_file(file_form.cleaned_data['file'], ip, user)
                return render(request, 'verification/verify_result.html', {'result': result})
            return render(request, 'verification/verify.html', {
                'hash_form': VerifyByHashForm(), 'file_form': file_form, 'active_tab': 'file',
            })

    return render(request, 'verification/verify.html', {
        'hash_form': VerifyByHashForm(), 'file_form': VerifyByFileForm(), 'active_tab': 'file',
    })


def _verify_by_hash(doc_hash: str, ip, user, forgery_is_suspected: bool = False) -> dict:
    local_doc = Document.objects.filter(sha256_hash=doc_hash).first()
    chain_info = None
    chain_error = None
    try:
        chain_info = blockchain_utils.verify_document_onchain(doc_hash)
    except BlockchainError as exc:
        chain_error = str(exc)

    if chain_info is not None:
        if chain_info['revoked']:
            result_code = 'revoked'
        elif chain_info['is_valid']:
            result_code = 'valid'
        else:
            result_code = 'not_found'
    elif local_doc is not None:
        # Chain unreachable - fall back to our local record. The template
        # makes clear this is an unverified/cached result, not a live chain check.
        result_code = {'registered': 'valid', 'revoked': 'revoked'}.get(local_doc.status, 'not_found')
    else:
        result_code = 'not_found'

    if result_code == 'not_found' and forgery_is_suspected:
        result_code = 'tampered'

    VerificationLog.objects.create(
        checked_by=user,
        submitted_hash=doc_hash,
        matched_document=local_doc,
        result=result_code,
        forgery_score=local_doc.forgery_score if local_doc else None,
        ip_address=ip,
    )

    return {
        'mode': 'hash',
        'doc_hash': doc_hash,
        'result_code': result_code,
        'local_doc': local_doc,
        'chain_info': chain_info,
        'chain_error': chain_error,
    }


def _verify_by_file(uploaded_file, ip, user) -> dict:
    suffix = os.path.splitext(uploaded_file.name)[1]
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            for chunk in uploaded_file.chunks():
                tmp.write(chunk)
            tmp_path = tmp.name

        doc_hash = compute_sha256(tmp_path)
        report = analyze_document(tmp_path)

        result = _verify_by_hash(doc_hash, ip, user, forgery_is_suspected=report['is_suspected'])
        result['mode'] = 'file'
        result['uploaded_filename'] = uploaded_file.name
        result['forgery_report'] = report
        return result
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)


# ---------------------------------------------------------------------------
# Document detail / revoke
# ---------------------------------------------------------------------------
def document_detail(request, doc_uuid):
    document = get_object_or_404(Document, uuid=doc_uuid)

    chain_info = None
    chain_error = None
    if document.status in ('registered', 'revoked'):
        try:
            chain_info = blockchain_utils.verify_document_onchain(document.sha256_hash)
        except BlockchainError as exc:
            chain_error = str(exc)

    flags = document.forgery_flags.split(', ') if document.forgery_flags else []
    is_owner = request.user.is_authenticated and request.user == document.owner

    return render(request, 'verification/document_detail.html', {
        'document': document,
        'flags': flags,
        'chain_info': chain_info,
        'chain_error': chain_error,
        'is_owner': is_owner,
        'can_manage': is_owner or request.user.is_staff,
    })


@login_required
def revoke_document(request, doc_uuid):
    document = get_object_or_404(Document, uuid=doc_uuid)
    if request.method != 'POST':
        return redirect('document_detail', doc_uuid=document.uuid)

    if not (request.user == document.owner or request.user.is_staff):
        messages.error(request, "You are not authorized to revoke this document.")
        return redirect('document_detail', doc_uuid=document.uuid)

    if document.status != 'registered':
        messages.error(request, "Only currently-registered documents can be revoked.")
        return redirect('document_detail', doc_uuid=document.uuid)

    try:
        blockchain_utils.revoke_document_onchain(document.sha256_hash)
    except BlockchainError as exc:
        messages.error(request, f"Revocation failed: {exc}")
        return redirect('document_detail', doc_uuid=document.uuid)

    document.status = 'revoked'
    document.save(update_fields=['status'])
    messages.success(request, "Document has been revoked on the blockchain.")
    return redirect('document_detail', doc_uuid=document.uuid)


# ---------------------------------------------------------------------------
# Dashboard (issuers)
# ---------------------------------------------------------------------------
@login_required
def dashboard(request):
    documents = Document.objects.filter(owner=request.user)
    stats = {
        'total': documents.count(),
        'registered': documents.filter(status='registered').count(),
        'revoked': documents.filter(status='revoked').count(),
        'flagged': documents.filter(is_suspected_forged=True).count(),
        'failed': documents.filter(status='failed').count(),
    }
    return render(request, 'verification/dashboard.html', {
        'documents': documents,
        'stats': stats,
    })
