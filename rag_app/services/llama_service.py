import os
from functools import lru_cache
from django.conf import settings
from llama_index.core import VectorStoreIndex, StorageContext, SimpleDirectoryReader
from llama_index.vector_stores.postgres import PGVectorStore

# DashScopeRerank may not be available in all versions, handle gracefully
try:
    from llama_index.postprocessors.dashscope_rerank import DashScopeRerank
    HAS_RERANK = True
except ImportError:
    HAS_RERANK = False
    DashScopeRerank = None


# 缓存 vector index 实例
_vector_index_cache = {}


def get_vector_index(table_name="llama_knowledge"):
    """
    Initializes and returns the VectorStoreIndex using PostgreSQL pgvector.
    Uses separate vector database configuration from settings.VECTOR_DATABASES.
    使用缓存提高性能。
    """
    # 检查缓存
    if table_name in _vector_index_cache:
        return _vector_index_cache[table_name]

    # Get vector database config from Django settings
    vector_db = settings.VECTOR_DATABASES['default']
    db_name = vector_db['NAME']
    host = vector_db['HOST']
    password = vector_db['PASSWORD']
    port = vector_db['PORT']
    user = vector_db['USER']

    # Initialize PGVectorStore
    # text-embedding-v2 embedding dimension is 1536
    vector_store = PGVectorStore.from_params(
        database=db_name,
        host=host,
        password=password,
        port=port,
        user=user,
        table_name=table_name,
        embed_dim=1536
    )

    storage_context = StorageContext.from_defaults(vector_store=vector_store)

    # Return (or create) the index
    index = VectorStoreIndex.from_vector_store(
        vector_store=vector_store,
        storage_context=storage_context
    )

    # 缓存结果
    _vector_index_cache[table_name] = index
    return index


def ingest_document(file_path: str, doc_metadata: dict):
    """
    Loads a document from the file path, injects custom metadata (tenant_id, doc_id),
    and inserts it into the Vector Store.
    """
    # 1. Extract text from the file
    documents = SimpleDirectoryReader(input_files=[file_path]).load_data()

    # 2. Inject enterprise metadata (for data isolation and filtering)
    for doc in documents:
        doc.metadata.update(doc_metadata)

    # 3. Write into Vector Store (pgVector)
    index = get_vector_index()
    for doc in documents:
        index.insert(doc)

    return True


def ask_question(query: str, tenant_id: str, chat_history=None):
    """
    Queries using Vector Search with Rerank.
    - Vector Search: semantic similarity search
    - Rerank: Uses gte-rerank-v2 to improve relevance
    """
    from llama_index.core.vector_stores import MetadataFilters, ExactMatchFilter
    from llama_index.core import PromptTemplate

    index = get_vector_index()

    # Data Isolation: Only retrieve context matching the tenant_id
    filters = MetadataFilters(
        filters=[ExactMatchFilter(key="tenant_id", value=tenant_id)]
    )

    # Vector Retriever
    retriever = index.as_retriever(
        similarity_top_k=10,
        filters=filters,
    )

    # Rerank with gte-rerank-v2 (if available)
    node_postprocessors = []
    if HAS_RERANK and DashScopeRerank:
        try:
            reranker = DashScopeRerank(
                model=settings.RERANK_MODEL,
                api_key=os.getenv('DASHSCOPE_API_KEY'),
                top_n=5,
            )
            node_postprocessors = [reranker]
        except Exception as e:
            print(f"Reranker initialization failed: {e}")

    # Custom system prompt
    system_prompt = """
    你是一个乐于助人的助手。
使用以下内容作为你所学习的知识，放在<context></context> XML标签内。
<context>
{{#context#}}
</context>
回答用户时：
如果你不知道，就直说你不知道。如果你在不确定的时候不知道，就寻求澄清。
避免提及你是从上下文中获取的信息。
并根据用户问题的语言来回答。
    """

    # Custom QA template with system prompt
    qa_template = PromptTemplate(
        system_prompt + "\n\n" + "Context information is below.\n---------------------\n{context_str}\n---------------------\nGiven the context information and not prior knowledge, answer the following question.\nQuestion: {query_str}\nAnswer: "
    )

    # Query Engine with optional reranking and custom prompt
    query_kwargs = {
        'text_qa_template': qa_template,
        'filters': filters,  # Apply tenant filter
    }
    if node_postprocessors:
        query_kwargs['node_postprocessors'] = node_postprocessors

    query_engine = index.as_query_engine(**query_kwargs)

    response = query_engine.query(query)

    # Build sources JSON summary
    source_info = []
    response_str = str(response).strip()

    if response.source_nodes:
        from rag_app.models import Document
        for node in response.source_nodes:
            content = node.node.get_content()[:200]
            # Fix encoding issues
            try:
                content = content.encode('utf-8', errors='replace').decode('utf-8')
            except:
                content = str(content)

            # Get doc_id from metadata and resolve actual filename from database
            doc_id = node.node.metadata.get('doc_id', '')
            original_filename = ''
            if doc_id:
                try:
                    doc = Document.objects.get(id=doc_id)
                    original_filename = doc.filename
                except Document.DoesNotExist:
                    pass

            metadata = dict(node.node.metadata)
            if original_filename:
                metadata['original_filename'] = original_filename

            source_info.append({
                'score': node.score,
                'metadata': metadata,
                'content_preview': content
            })

    # Fix response encoding
    answer = str(response)
    try:
        answer = answer.encode('utf-8', errors='replace').decode('utf-8')
    except:
        pass

    # If still empty after all attempts, return a helpful message
    if not answer or answer.strip() == "Empty Response":
        if not source_info:
            answer = "抱歉，您的知识库中没有任何文档。请先在知识库中上传文档，然后我可以回答您关于这些文档的问题。"
        else:
            answer = "抱歉，我无法从您提供的文档中找到相关的答案。"

    return answer, source_info


def get_relevant_content(query: str, tenant_id: str, top_k: int = 5):
    """
    获取与查询相关的文档内容片段

    Args:
        query: 查询内容
        tenant_id: 租户ID
        top_k: 返回结果数量

    Returns:
        相关文档内容列表
    """
    from llama_index.core.vector_stores import MetadataFilters, ExactMatchFilter

    index = get_vector_index()

    # Data Isolation
    filters = MetadataFilters(
        filters=[ExactMatchFilter(key="tenant_id", value=tenant_id)]
    )

    # Retriever
    retriever = index.as_retriever(
        similarity_top_k=top_k,
        filters=filters,
    )

    # Get relevant nodes
    nodes = retriever.retrieve(query)

    results = []
    for i, node in enumerate(nodes):
        content = node.node.get_content()
        try:
            content = content.encode('utf-8', errors='replace').decode('utf-8')
        except:
            content = str(content)

        results.append({
            'rank': i + 1,
            'score': node.score,
            'doc_id': node.node.metadata.get('doc_id', ''),
            'file_name': node.node.metadata.get('file_name', ''),
            'page_label': node.node.metadata.get('page_label', ''),
            'content': content
        })

    return results


def get_document_content(tenant_id: str, doc_id: str):
    """
    获取指定文档的所有内容片段

    Args:
        tenant_id: 租户ID
        doc_id: 文档ID

    Returns:
        文档内容列表
    """
    from llama_index.core.vector_stores import MetadataFilters, ExactMatchFilter

    index = get_vector_index()

    # Filter by tenant_id and doc_id
    filters = MetadataFilters(
        filters=[
            ExactMatchFilter(key="tenant_id", value=tenant_id),
            ExactMatchFilter(key="doc_id", value=doc_id)
        ]
    )

    # Retriever with high top_k to get all chunks
    retriever = index.as_retriever(
        similarity_top_k=100,
        filters=filters,
    )

    # Use doc_id as query to retrieve all chunks of this document
    # Or use the filename pattern
    query = f"doc_id:{doc_id}"
    nodes = retriever.retrieve(query)

    # If no results, try using the doc_id directly
    if not nodes:
        nodes = retriever.retrieve(doc_id)

    results = []
    for i, node in enumerate(nodes):
        content = node.node.get_content()
        try:
            content = content.encode('utf-8', errors='replace').decode('utf-8')
        except:
            content = str(content)

        results.append({
            'chunk_id': i + 1,
            'score': node.score,
            'page_label': node.node.metadata.get('page_label', ''),
            'content': content
        })

    return results


def delete_document_from_vector(doc_id: str, tenant_id: str):
    """
    从向量数据库中删除指定文档的所有内容
    直接删除 data_llama_knowledge 表中的数据
    """
    import psycopg

    # Get vector database config
    vector_db = settings.VECTOR_DATABASES['default']
    conn = psycopg.connect(
        host=vector_db['HOST'],
        port=vector_db['PORT'],
        user=vector_db['USER'],
        password=vector_db['PASSWORD'],
        dbname=vector_db['NAME']
    )

    try:
        with conn.cursor() as cur:
            # 删除匹配 doc_id 的记录 (在 metadata 任意位置)
            cur.execute(
                """DELETE FROM data_llama_knowledge
                   WHERE metadata_->>'tenant_id' = %s
                   AND metadata_::text LIKE %s""",
                (tenant_id, f'%doc_id%{doc_id}%')
            )
            deleted_count = cur.rowcount

            conn.commit()
            return deleted_count
    finally:
        conn.close()
