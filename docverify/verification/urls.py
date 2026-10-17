from django.urls import path

from . import views

urlpatterns = [
    path('', views.home, name='home'),

    path('register/', views.register_document, name='register_document'),
    path('document/<uuid:doc_uuid>/retry/', views.retry_registration, name='retry_registration'),

    path('verify/', views.verify_document, name='verify_document'),

    path('document/<uuid:doc_uuid>/', views.document_detail, name='document_detail'),
    path('document/<uuid:doc_uuid>/revoke/', views.revoke_document, name='revoke_document'),

    path('dashboard/', views.dashboard, name='dashboard'),
]
