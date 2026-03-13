"""
生产者-消费者模式处理文档向量化
使用队列和单一工作线程避免连接数过多
"""
import queue
import threading
import os
import time

# 任务队列
_task_queue = queue.Queue()

# 工作线程（单线程，避免连接过多）
_worker_thread = None
_worker_lock = threading.Lock()


def _worker():
    """工作线程，不断从队列中取任务并处理"""
    from rag_app.services.llama_service import ingest_document
    from rag_app.models import Document
    from rag_app.services.s3_service import s3_service
    import tempfile

    print("[VectorWorker] Worker thread started")

    while True:
        try:
            # 获取任务，超时后继续循环
            task = _task_queue.get(timeout=5)
            if task is None:  # 退出信号
                print("[VectorWorker] Worker thread exiting")
                break

            document_id, s3_key, filename, tenant_id = task

            print(f"[VectorWorker] Processing document: {document_id}")
            file_path = None

            try:
                # 下载文件（如果是S3）
                if s3_service.is_configured() and s3_key and '/' in s3_key:
                    ext = os.path.splitext(filename)[1] if filename else '.tmp'
                    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=ext)
                    temp_path = temp_file.name
                    temp_file.close()

                    if s3_service.download_file(s3_key, temp_path):
                        file_path = temp_path
                    else:
                        raise Exception(f"Failed to download from S3: {s3_key}")

                # 向量化
                if file_path and os.path.exists(file_path):
                    metadata = {
                        'doc_id': document_id,
                        'tenant_id': tenant_id,
                        's3_key': s3_key,
                    }
                    ingest_document(file_path, metadata)

                    # 清理临时文件
                    try:
                        os.remove(file_path)
                    except:
                        pass

                # 更新状态
                try:
                    doc = Document.objects.get(id=document_id)
                    doc.status = 'COMPLETED'
                    doc.save()
                    print(f"[VectorWorker] Document {document_id} completed")
                except Exception as e:
                    print(f"[VectorWorker] Failed to update status: {e}")

            except Exception as e:
                print(f"[VectorWorker] Error processing {document_id}: {e}")
                import traceback
                traceback.print_exc()

                # 更新为失败状态
                try:
                    doc = Document.objects.get(id=document_id)
                    doc.status = 'FAILED'
                    doc.save()
                except:
                    pass
            finally:
                # 清理临时文件
                if file_path and os.path.exists(file_path):
                    try:
                        os.remove(file_path)
                    except:
                        pass

                _task_queue.task_done()

        except queue.Empty:
            continue
        except Exception as e:
            print(f"[VectorWorker] Worker error: {e}")
            import traceback
            traceback.print_exc()


def _ensure_worker():
    """确保工作线程正在运行"""
    global _worker_thread
    with _worker_lock:
        if _worker_thread is None or not _worker_thread.is_alive():
            _worker_thread = threading.Thread(target=_worker, daemon=True)
            _worker_thread.start()
            print("[VectorWorker] Started new worker thread")


def submit_task(document_id: str, s3_key: str, filename: str, tenant_id: str):
    """
    提交向量化任务（生产者）
    """
    _ensure_worker()
    _task_queue.put((document_id, s3_key, filename, tenant_id))
    print(f"[VectorWorker] Task submitted for document: {document_id}")
