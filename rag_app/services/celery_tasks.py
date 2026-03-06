import os
import tempfile
from rag_app.models import Document
from rag_app.services.llama_service import ingest_document
from rag_app.services.s3_service import s3_service


def process_document(document_id):
    """
    Process and vectorize a document
    """
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
        # Update document status to failed
        try:
            document = Document.objects.get(id=document_id)
            document.status = 'FAILED'
            document.save()
        except:
            pass
        raise e


def process_pending_documents():
    """
    Process all pending documents that haven't been vectorized
    """
    pending_docs = Document.objects.filter(status='PENDING')
    results = []

    for doc in pending_docs:
        process_document(str(doc.id))
        results.append(str(doc.id))

    return {'processed': len(results), 'document_ids': results}
