from rest_framework import viewsets, status
from rest_framework.response import Response
from rest_framework.decorators import action
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from django.http import FileResponse, Http404, HttpResponse, StreamingHttpResponse
from django.views.decorators.http import require_http_methods
from rag_app.models import Document, ChatSession, ChatMessage, Tenant
from rag_app.api.serializers import DocumentSerializer, ChatSessionSerializer, TenantSerializer, ChatSessionListSerializer
from rag_app.services.llama_service import ask_question, delete_document_from_vector
from rag_app.services.s3_service import s3_service
from rag_app.services.vector_worker import submit_task
import tempfile
import os
import re
import time


def sanitize_filename(filename: str) -> str:
    """将文件名转换为安全的 ASCII 名称，保留文件扩展名"""
    # 分离文件名和扩展名
    name, ext = os.path.splitext(filename)
    if not ext:
        ext = ''

    # 将中文字符替换为拼音或时间戳
    # 检查是否包含非 ASCII 字符
    if not name.isascii():
        # 使用时间戳 + 随机数作为名称
        safe_name = f"doc_{int(time.time())}_{hash(name) % 10000}"
    else:
        # 保留原始名称，但移除不安全字符
        safe_name = re.sub(r'[^\w\-_.]', '_', name)

    return safe_name + ext


class TenantViewSet(viewsets.ModelViewSet):
    """租户管理 ViewSet"""
    queryset = Tenant.objects.all()
    serializer_class = TenantSerializer

    def get_queryset(self):
        return Tenant.objects.order_by('-created_at')


class DocumentViewSet(viewsets.ModelViewSet):
    """文档管理 ViewSet"""
    queryset = Document.objects.all()
    serializer_class = DocumentSerializer

    def get_queryset(self):
        queryset = Document.objects.all()
        tenant_id = self.request.query_params.get('tenant_id')
        if tenant_id:
            queryset = queryset.filter(tenant_id=tenant_id)

        # 搜索文件名
        search = self.request.query_params.get('search')
        if search:
            queryset = queryset.filter(filename__icontains=search)

        # 按状态筛选
        status = self.request.query_params.get('status')
        if status:
            queryset = queryset.filter(status=status)

        # 分页支持
        try:
            page = int(self.request.query_params.get('page', 0))
            page_size = int(self.request.query_params.get('page_size', 10))
            if page > 0 and page_size > 0:
                start = (page - 1) * page_size
                end = start + page_size
                return queryset[start:end]
        except (ValueError, TypeError):
            pass

        return queryset.order_by('-updated_at')

    def list(self, request, *args, **kwargs):
        queryset = self.get_queryset()

        # 获取总数（应用筛选条件后）
        tenant_id = request.query_params.get('tenant_id')
        search = request.query_params.get('search')
        status = request.query_params.get('status')

        # 构建基础筛选
        filters = {}
        if tenant_id:
            filters['tenant_id'] = tenant_id
        if search:
            filters['filename__icontains'] = search
        if status:
            filters['status'] = status

        total = Document.objects.filter(**filters).count()

        serializer = self.get_serializer(queryset, many=True)
        return Response({
            'results': serializer.data,
            'total': total,
            'page': int(request.query_params.get('page', 1)),
            'page_size': int(request.query_params.get('page_size', 10))
        })

    def create(self, request, *args, **kwargs):
        # 检查是否为批量上传
        files = request.FILES.getlist('files')

        if files and len(files) > 1:
            # 批量上传
            return self._batch_upload(request, files)
        else:
            # 单文件上传
            return self._single_upload(request)

    def _single_upload(self, request):
        """单文件上传"""
        # 支持 files（批量）或 file（单文件）两种字段名
        files = request.FILES.getlist('files')
        uploaded_file = files[0] if files else request.FILES.get('file')

        if not uploaded_file:
            return Response({'error': 'No file was submitted.'}, status=status.HTTP_400_BAD_REQUEST)

        # 手动构造数据，避免 serializer 验证问题
        import uuid
        tenant_id = request.data.get('tenant_id')
        doc_id = str(uuid.uuid4())
        # S3 key 使用安全文件名，filename 字段保留原始文件名
        safe_filename = sanitize_filename(uploaded_file.name)
        original_filename = uploaded_file.name
        s3_key = f"{tenant_id}/{doc_id}/{safe_filename}"

        try:
            if s3_service.is_configured():
                s3_service.upload_file_obj(uploaded_file, s3_key)
                document = Document.objects.create(
                    id=doc_id,
                    status='PENDING',
                    filename=original_filename,
                    tenant_id=tenant_id,
                    file=s3_key
                )
            else:
                document = Document.objects.create(
                    status='PENDING',
                    filename=original_filename,
                    tenant_id=tenant_id
                )

            # 使用工作线程处理向量化（生产者-消费者模式）
            submit_task(
                str(document.id),
                str(document.file),
                document.filename,
                tenant_id
            )

            serializer = DocumentSerializer(document)
            return Response(serializer.data, status=status.HTTP_201_CREATED)

        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    def _batch_upload(self, request, files):
        """批量上传多个文件"""
        import uuid
        tenant_id = request.data.get('tenant_id')
        uploaded_docs = []
        errors = []

        for uploaded_file in files:
            try:
                doc_id = str(uuid.uuid4())
                # S3 key 使用安全文件名，filename 字段保留原始文件名
                safe_filename = sanitize_filename(uploaded_file.name)
                original_filename = uploaded_file.name
                s3_key = f"{tenant_id}/{doc_id}/{safe_filename}"

                # 直接上传到 S3
                if s3_service.is_configured():
                    s3_service.upload_file_obj(uploaded_file, s3_key)
                    document = Document.objects.create(
                        id=doc_id,
                        status='PENDING',
                        filename=original_filename,
                        tenant_id=tenant_id,
                        file=s3_key
                    )
                else:
                    document = Document.objects.create(
                        status='PENDING',
                        filename=original_filename,
                        tenant_id=tenant_id
                    )

                # 使用工作线程处理向量化（生产者-消费者模式）
                submit_task(
                    str(document.id),
                    str(document.file),
                    document.filename,
                    tenant_id
                )

                uploaded_docs.append({
                    'id': str(document.id),
                    'filename': document.filename,
                    'status': document.status
                })

            except Exception as e:
                errors.append({
                    'filename': uploaded_file.name,
                    'error': str(e)
                })

        return Response({
            'uploaded': uploaded_docs,
            'errors': errors,
            'total': len(files),
            'success': len(uploaded_docs),
            'failed': len(errors)
        }, status=status.HTTP_201_CREATED)

    def _process_upload(self, request, uploaded_file, serializer):
        """处理单个文件上传"""
        import uuid

        filename = uploaded_file.name
        # S3 key 使用安全文件名，filename 字段保留原始文件名
        safe_filename = sanitize_filename(filename)
        original_filename = filename
        tenant_id = request.data.get('tenant_id')

        # 生成 S3 key
        doc_id = str(uuid.uuid4())
        s3_key = f"{tenant_id}/{doc_id}/{safe_filename}"

        try:
            # 直接上传到 S3（不保存到本地）
            if s3_service.is_configured():
                # 使用内存文件上传到 S3
                s3_service.upload_file_obj(uploaded_file, s3_key)
                # 保存文档记录，S3 key 作为文件路径
                document = serializer.save(
                    id=doc_id,
                    status='PENDING',
                    filename=original_filename,
                    tenant_id=tenant_id,
                    file=s3_key
                )
            else:
                # 如果没有配置 S3，保存到本地
                document = serializer.save(status='PENDING', filename=original_filename)

            # 使用工作线程处理向量化（生产者-消费者模式）
            submit_task(
                str(document.id),
                str(document.file),
                document.filename,
                tenant_id
            )

            return Response(serializer.data, status=status.HTTP_201_CREATED)

        except Exception as e:
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

            # 使用工作线程处理向量化
            submit_task(
                str(document.id),
                str(document.file),
                document.filename,
                document.tenant_id
            )
            return Response({'status': 'queued', 'document_id': str(document.id)})
        except Document.DoesNotExist:
            return Response({'error': 'Document not found'}, status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=True, methods=['get'])
    def download(self, request, pk=None):
        """Download document file from S3"""
        try:
            document = self.get_object()
            s3_key = str(document.file)

            # 只支持 S3 文件下载
            if not s3_service.is_configured():
                return Response({'error': 'S3 not configured'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

            if '/' not in s3_key:
                return Response({'error': 'Not a S3 file'}, status=status.HTTP_400_BAD_REQUEST)

            # 获取文件后缀
            ext = os.path.splitext(document.filename)[1]
            # 使用 mkdtemp 创建临时目录
            temp_dir = tempfile.mkdtemp()
            temp_path = os.path.join(temp_dir, document.filename + ext)

            if s3_service.download_file(s3_key, temp_path):
                # 读取文件内容
                with open(temp_path, 'rb') as f:
                    file_content = f.read()

                # 清理临时文件
                try:
                    os.remove(temp_path)
                    os.rmdir(temp_dir)
                except:
                    pass

                # 返回文件
                from django.http import HttpResponse

                # 获取 content-type（根据文件扩展名）
                content_type = 'application/octet-stream'
                if document.filename.lower().endswith('.pdf'):
                    content_type = 'application/pdf'
                elif document.filename.lower().endswith(('.jpg', '.jpeg')):
                    content_type = 'image/jpeg'
                elif document.filename.lower().endswith('.png'):
                    content_type = 'image/png'

                # 使用 HttpResponse 直接返回二进制内容
                response = HttpResponse(file_content, content_type=content_type)

                # 构造 Content-Disposition header，先对中文进行 URL 编码
                from urllib.parse import quote
                ascii_filename = quote(document.filename, safe='')
                response['Content-Disposition'] = f'attachment; filename="{document.filename}"; filename*=UTF-8\'\'{ascii_filename}'

                return response
            else:
                return Response({'error': 'Failed to download from S3'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        except Http404:
            return Response({'error': 'Document not found'}, status=status.HTTP_404_NOT_FOUND)
class ChatViewSet(viewsets.ModelViewSet):
    queryset = ChatSession.objects.all()
    serializer_class = ChatSessionSerializer

    def get_queryset(self):
        queryset = ChatSession.objects.all()
        tenant_id = self.request.query_params.get('tenant_id')
        if tenant_id:
            queryset = queryset.filter(tenant_id=tenant_id)
        return queryset.order_by('-created_at')

    def list(self, request, *args, **kwargs):
        # 只返回标题列表，不返回 messages
        queryset = self.get_queryset()
        serializer = ChatSessionListSerializer(queryset, many=True)
        return Response(serializer.data)

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


# 独立的文件下载视图 (使用 Django HttpResponse 避免 DRF 编码问题)
@require_http_methods(["GET"])
def download_file(request, doc_id):
    """独立文件下载视图"""
    try:
        document = Document.objects.get(id=doc_id)
        s3_key = str(document.file)

        # 只支持 S3 文件下载
        if not s3_service.is_configured():
            return HttpResponse('S3 not configured', status=500)

        if '/' not in s3_key:
            return HttpResponse('Not a S3 file', status=400)

        # 获取文件后缀
        ext = os.path.splitext(document.filename)[1]
        temp_dir = tempfile.mkdtemp()
        temp_path = os.path.join(temp_dir, document.filename + ext)

        if s3_service.download_file(s3_key, temp_path):
            # 读取文件内容
            with open(temp_path, 'rb') as f:
                file_content = f.read()

            # 清理临时文件
            try:
                os.remove(temp_path)
                os.rmdir(temp_dir)
            except:
                pass

            # 获取 content-type
            content_type = 'application/octet-stream'
            is_pdf = document.filename.lower().endswith('.pdf')
            if is_pdf:
                content_type = 'application/pdf'
            elif document.filename.lower().endswith(('.jpg', '.jpeg')):
                content_type = 'image/jpeg'
            elif document.filename.lower().endswith('.png'):
                content_type = 'image/png'

            # 使用 Django HttpResponse 直接返回
            response = HttpResponse(file_content, content_type=content_type)

            # 统一使用安全的文件名（doc_id + 扩展名）
            ext = os.path.splitext(document.filename)[1]
            safe_filename = f"document_{doc_id}{ext}"

            if is_pdf:
                response['Content-Disposition'] = f'inline; filename="{safe_filename}"'
            else:
                response['Content-Disposition'] = f'attachment; filename="{safe_filename}"'
            return response
        else:
            return HttpResponse('Failed to download from S3', status=500)

    except Document.DoesNotExist:
        return HttpResponse('Document not found', status=404)
    except Exception as e:
        return HttpResponse(str(e), status=500)

