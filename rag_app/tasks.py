"""
Celery 任务 - 支持优先级队列
"""
from celery import shared_task
import os
import tempfile


@shared_task(bind=True, queue='high')
def process_document_high(self, document_id):
    """
    高优先级文档向量化任务
    """
    return _process_document(document_id)


@shared_task(bind=True, queue='default')
def process_document(self, document_id):
    """
    普通优先级文档向量化任务
    """
    return _process_document(document_id)


def _process_document(document_id):
    """
    处理文档向量化
    """
    from rag_app.models import Document
    from rag_app.services.llama_service import ingest_document
    from rag_app.services.s3_service import s3_service

    try:
        # Get document from database
        document = Document.objects.get(id=document_id)

        # Get file path - could be local or S3
        s3_key = str(document.file)
        file_path = None

        if s3_service.is_configured() and '/' in s3_key:
            # File is in S3, download to temp
            ext = os.path.splitext(document.filename)[1]
            temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=ext)
            temp_path = temp_file.name
            temp_file.close()

            if s3_service.download_file(s3_key, temp_path):
                file_path = temp_path
            else:
                raise Exception(f"Failed to download file from S3: {s3_key}")
        else:
            # File is local
            file_path = document.file.path

        if not file_path or not os.path.exists(file_path):
            raise Exception(f"File not found: {file_path}")

        # Vectorize document
        metadata = {
            'doc_id': str(document.id),
            'tenant_id': document.tenant_id,
            's3_key': s3_key,
        }

        print(f"[Celery] Starting vectorization for document: {document_id}, file: {file_path}")
        ingest_document(file_path, metadata)

        # Clean up temp file if downloaded from S3
        if s3_service.is_configured() and '/' in s3_key and file_path and os.path.exists(file_path):
            os.remove(file_path)

        # Update document status
        document.status = 'COMPLETED'
        document.save()

        return {'status': 'success', 'document_id': str(document.id)}

    except Document.DoesNotExist:
        return {'status': 'error', 'message': f'Document {document_id} not found'}
    except Exception as e:
        print(f"[Celery] Error: {e}")
        import traceback
        traceback.print_exc()
        try:
            document = Document.objects.get(id=document_id)
            document.status = 'FAILED'
            document.save()
        except:
            pass
        raise e
