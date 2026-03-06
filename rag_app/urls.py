from django.urls import path, include
from rest_framework.routers import DefaultRouter
from rag_app.api.views import DocumentViewSet, ChatViewSet
from rag_app.api.related_views import RelatedDocumentsViewSet

router = DefaultRouter()
router.register(r'documents', DocumentViewSet, basename='document')
router.register(r'chat', ChatViewSet, basename='chat')
router.register(r'related', RelatedDocumentsViewSet, basename='related')

urlpatterns = [
    path('', include(router.urls)),
]
