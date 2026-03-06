from rest_framework import viewsets, status
from rest_framework.response import Response
from rest_framework.decorators import action
from rag_app.models import Document
from rag_app.api.serializers import DocumentSerializer
from django.conf import settings
import os
import json


class RelatedDocumentsViewSet(viewsets.ViewSet):
    """
    获取与查询相关的文档内容
    """

    @action(detail=False, methods=['post'])
    def search(self, request):
        """
        POST /api/related/search/
        body: {"tenant_id": "xxx", "query": "xxx", "top_k": 5}
        """
        tenant_id = request.data.get('tenant_id')
        query = request.data.get('query')
        top_k = request.data.get('top_k', 5)

        if not tenant_id or not query:
            return Response(
                {'error': 'tenant_id and query are required'},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            from rag_app.services.llama_service import get_relevant_content
            results = get_relevant_content(query, tenant_id, top_k)

            return Response({
                'query': query,
                'tenant_id': tenant_id,
                'count': len(results),
                'results': results
            })
        except Exception as e:
            import traceback
            return Response(
                {'error': str(e), 'trace': traceback.format_exc()},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    @action(detail=False, methods=['get'])
    def by_document(self, request):
        """
        GET /api/related/by_document/?tenant_id=xxx&doc_id=xxx
        获取指定文档的内容
        """
        tenant_id = request.query_params.get('tenant_id')
        doc_id = request.query_params.get('doc_id')

        if not tenant_id or not doc_id:
            return Response(
                {'error': 'tenant_id and doc_id are required'},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            from rag_app.services.llama_service import get_document_content
            results = get_document_content(tenant_id, doc_id)

            return Response({
                'doc_id': doc_id,
                'tenant_id': tenant_id,
                'count': len(results),
                'chunks': results
            })
        except Exception as e:
            import traceback
            return Response(
                {'error': str(e), 'trace': traceback.format_exc()},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
