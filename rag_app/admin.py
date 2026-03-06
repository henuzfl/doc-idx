from django.contrib import admin
from rag_app.models import Document, ChatSession, ChatMessage

@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    list_display = ('filename', 'tenant_id', 'status', 'created_at')
    list_filter = ('status', 'tenant_id')
    search_fields = ('filename', 'tenant_id')
    readonly_fields = ('id', 'created_at', 'updated_at')

class ChatMessageInline(admin.TabularInline):
    model = ChatMessage
    extra = 0
    readonly_fields = ('role', 'content', 'sources', 'created_at')
    can_delete = False

@admin.register(ChatSession)
class ChatSessionAdmin(admin.ModelAdmin):
    list_display = ('title', 'tenant_id', 'created_at')
    list_filter = ('tenant_id', 'created_at')
    search_fields = ('title', 'tenant_id')
    readonly_fields = ('id', 'created_at')
    inlines = [ChatMessageInline]

@admin.register(ChatMessage)
class ChatMessageAdmin(admin.ModelAdmin):
    list_display = ('session', 'role', 'created_at')
    list_filter = ('role', 'created_at')
    search_fields = ('content',)
    readonly_fields = ('session', 'role', 'content', 'sources', 'created_at')
