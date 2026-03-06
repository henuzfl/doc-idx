from rest_framework import viewsets, status
from rest_framework.response import Response
from rest_framework.decorators import action
from rag_app.models import Document, ChatSession, ChatMessage
from rag_app.api.serializers import DocumentSerializer, ChatSessionSerializer
from rag_app.services.llama_service import ingest_document, ask_question, delete_document_from_vector
from django.core.files.storage import FileSystemStorage
import os
import json

class DocumentViewSet(viewsets.ModelViewSet):
    queryset = Document.objects.all()
    serializer_class = DocumentSerializer

    def get_queryset(self):
        queryset = Document.objects.all()
        tenant_id = self.request.query_params.get('tenant_id')
        if tenant_id:
            queryset = queryset.filter(tenant_id=tenant_id)
        return queryset.order_by('-created_at')

    def create(self, request, *args, **kwargs):
        # 1. Save document record
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        document = serializer.save(status='PROCESSING', filename=request.FILES['file'].name)

        # 2. Trigger Ingestion (ideal to use Celery here for async, keeping sync for demo)
        file_path = document.file.path
        
        try:
            metadata = {
                'doc_id': str(document.id),
                'tenant_id': document.tenant_id,
            }
            # Calls the LlamaIndex service to chunk and embed into pgVector
            ingest_document(file_path, metadata)
            
            document.status = 'COMPLETED'
            document.save()
            return Response(serializer.data, status=status.HTTP_201_CREATED)
            
        except Exception as e:
            document.status = 'FAILED'
            document.save()
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    def destroy(self, request, *args, **kwargs):
        try:
            instance = self.get_object()
            # Delete from vector store
            delete_document_from_vector(str(instance.id), instance.tenant_id)
            # Delete file
            if instance.file:
                instance.file.delete()
            # Delete from database
            self.perform_destroy(instance)
            return Response(status=status.HTTP_204_NO_CONTENT)
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

class ChatViewSet(viewsets.ModelViewSet):
    queryset = ChatSession.objects.all()
    serializer_class = ChatSessionSerializer

    @action(detail=False, methods=['post'])
    def ask(self, request):
        tenant_id = request.data.get('tenant_id')
        query = request.data.get('query')
        session_id = request.data.get('session_id')

        if not tenant_id or not query:
            return Response({'error': 'tenant_id and query are required'}, status=status.HTTP_400_BAD_REQUEST)

        # Get or create session
        if session_id:
            try:
                session = ChatSession.objects.get(id=session_id, tenant_id=tenant_id)
            except ChatSession.DoesNotExist:
                return Response({'error': 'Session not found'}, status=status.HTTP_404_NOT_FOUND)
        else:
            session = ChatSession.objects.create(tenant_id=tenant_id, title=query[:50])

        # Save user message
        ChatMessage.objects.create(session=session, role='user', content=query)

        # Query LlamaIndex (Qwen + pgVector)
        try:
            answer, sources = ask_question(query, tenant_id)

            # Ensure UTF-8 encoding
            answer = str(answer)
            if isinstance(answer, bytes):
                answer = answer.decode('utf-8', errors='replace')

            # Clean sources for JSON
            clean_sources = []
            for s in sources:
                clean_sources.append({
                    'score': float(s.get('score', 0)),
                    'metadata': s.get('metadata', {}),
                    'content_preview': str(s.get('content_preview', ''))[:200]
                })

            # Save assistant message
            ChatMessage.objects.create(
                session=session,
                role='assistant',
                content=answer,
                sources=clean_sources
            )

            return Response({
                'session_id': str(session.id),
                'answer': answer,
                'sources': clean_sources
            })
        except Exception as e:
            import traceback
            return Response({'error': str(e), 'trace': traceback.format_exc()}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
