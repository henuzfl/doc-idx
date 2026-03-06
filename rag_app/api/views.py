from rest_framework import viewsets, status
from rest_framework.response import Response
from rest_framework.decorators import action
from rag_app.models import Document, ChatSession, ChatMessage
from rag_app.api.serializers import DocumentSerializer, ChatSessionSerializer
from rag_app.services.llama_service import ask_question, delete_document_from_vector
from rag_app.services.s3_service import s3_service
from rag_app.services.celery_tasks import process_document
from django.core.files.storage import FileSystemStorage
import os
import threading


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
        # 1. Save document record first
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        # Save with PENDING status
        document = serializer.save(status='PENDING', filename=request.FILES['file'].name)

        # Get local file path
        file_path = document.file.path
        s3_key = f"{document.tenant_id}/{document.id}/{document.filename}"

        try:
            # 2. Upload to S3
            if s3_service.is_configured():
                s3_service.upload_file(file_path, s3_key)
                # Update document with S3 key
                document.file.name = s3_key
                document.save()

            # 3. Run vectorization in background thread
            def vectorize():
                try:
                    process_document(str(document.id))
                except Exception as e:
                    print(f"Vectorization error: {e}")

            thread = threading.Thread(target=vectorize)
            thread.start()

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

            # Delete from S3
            s3_key = str(instance.file)
            if s3_key and s3_service.is_configured():
                s3_service.delete_file(s3_key)

            # Delete local file if exists
            if instance.file:
                instance.file.delete()

            # Delete from database
            self.perform_destroy(instance)
            return Response(status=status.HTTP_204_NO_CONTENT)
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=False, methods=['post'])
    def retry(self, request):
        """Retry vectorizing a failed document"""
        doc_id = request.data.get('document_id')
        if not doc_id:
            return Response({'error': 'document_id is required'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            document = Document.objects.get(id=doc_id)
            document.status = 'PENDING'
            document.save()
            process_document.delay(str(document.id))
            return Response({'status': 'queued', 'document_id': str(document.id)})
        except Document.DoesNotExist:
            return Response({'error': 'Document not found'}, status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class ChatViewSet(viewsets.ModelViewSet):
    queryset = ChatSession.objects.all()
    serializer_class = ChatSessionSerializer

    def get_queryset(self):
        queryset = ChatSession.objects.all()
        tenant_id = self.request.query_params.get('tenant_id')
        if tenant_id:
            queryset = queryset.filter(tenant_id=tenant_id)
        return queryset.order_by('-created_at')

    @action(detail=True, methods=['get'])
    def messages(self, request, pk=None):
        """Get messages for a specific chat session"""
        session = self.get_object()
        messages = ChatMessage.objects.filter(session=session).order_by('created_at')
        data = [{
            'id': str(m.id),
            'role': m.role,
            'content': m.content,
            'sources': m.sources,
            'created_at': m.created_at.isoformat()
        } for m in messages]
        return Response(data)

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
