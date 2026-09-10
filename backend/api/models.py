import uuid

from django.contrib.auth.models import User
from django.db import models


def generate_document_source_id():
    return f"doc_{uuid.uuid4().hex}"


class Organization(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "启用"
        DISABLED = "DISABLED", "停用"

    name = models.CharField(max_length=100)
    slug = models.SlugField(max_length=120, unique=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)
    created_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name="created_organizations"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name", "id"]


class OrganizationMembership(models.Model):
    class Role(models.TextChoices):
        OWNER = "OWNER", "所有者"
        ADMIN = "ADMIN", "管理员"
        MEMBER = "MEMBER", "成员"
        AUDITOR = "AUDITOR", "审计员"

    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="memberships"
    )
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="organization_memberships")
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.MEMBER)
    created_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="created_organization_memberships",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["organization_id", "user__username", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "user"], name="unique_organization_membership"
            )
        ]


class Workspace(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "启用"
        DISABLED = "DISABLED", "停用"

    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="workspaces"
    )
    name = models.CharField(max_length=100)
    slug = models.SlugField(max_length=120)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)
    is_default = models.BooleanField(default=False)
    created_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name="created_workspaces"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["organization_id", "name", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "slug"], name="unique_organization_workspace_slug"
            )
        ]


class WorkspaceMembership(models.Model):
    class Role(models.TextChoices):
        ADMIN = "ADMIN", "管理员"
        DEVELOPER = "DEVELOPER", "开发者"
        OPERATOR = "OPERATOR", "运营者"
        VIEWER = "VIEWER", "查看者"
        AUDITOR = "AUDITOR", "审计员"

    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="workspace_memberships")
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.VIEWER)
    created_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="created_workspace_memberships",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["workspace_id", "user__username", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["workspace", "user"], name="unique_workspace_membership"
            )
        ]


class AuditEvent(models.Model):
    class Result(models.TextChoices):
        SUCCESS = "SUCCESS", "成功"
        FAILURE = "FAILURE", "失败"
        REJECTED = "REJECTED", "拒绝"

    organization = models.ForeignKey(
        Organization, on_delete=models.PROTECT, related_name="audit_events"
    )
    workspace = models.ForeignKey(
        Workspace, null=True, blank=True, on_delete=models.SET_NULL, related_name="audit_events"
    )
    actor = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name="audit_events"
    )
    action = models.CharField(max_length=100, db_index=True)
    resource_type = models.CharField(max_length=80, blank=True, default="")
    resource_id = models.CharField(max_length=80, blank=True, default="")
    result = models.CharField(max_length=20, choices=Result.choices, db_index=True)
    request_id = models.CharField(max_length=64, blank=True, default="", db_index=True)
    ip_hash = models.CharField(max_length=64, blank=True, default="")
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at", "-id"]


class ModelConfig(models.Model):
    class ModelType(models.TextChoices):
        CHAT = "CHAT", "Chat模型"
        EMBEDDING = "EMBEDDING", "Embedding模型"

    class TestStatus(models.TextChoices):
        UNTESTED = "UNTESTED", "未测试"
        SUCCESS = "SUCCESS", "连接成功"
        FAILURE = "FAILURE", "连接失败"

    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name="model_configs")
    workspace = models.ForeignKey(
        Workspace, on_delete=models.PROTECT, related_name="model_configs"
    )
    name = models.CharField(max_length=100)
    model_type = models.CharField(max_length=20, choices=ModelType.choices)
    base_url = models.URLField(max_length=500)
    model_name = models.CharField(max_length=200)
    encrypted_api_key = models.TextField()
    api_key_last4 = models.CharField(max_length=4, blank=True)
    timeout_seconds = models.PositiveSmallIntegerField(default=30)
    revision = models.PositiveIntegerField(default=1)
    last_test_status = models.CharField(
        max_length=20,
        choices=TestStatus.choices,
        default=TestStatus.UNTESTED,
    )
    last_test_message = models.CharField(max_length=200, blank=True)
    last_test_at = models.DateTimeField(null=True, blank=True)
    embedding_dimension = models.PositiveIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["workspace", "name"], name="unique_workspace_model_config_name"
            )
        ]

    def save(self, *args, **kwargs):
        if not self.workspace_id and self.owner_id:
            from .services.workspaces import ensure_personal_workspace

            self.workspace = ensure_personal_workspace(self.owner)
        return super().save(*args, **kwargs)


class KnowledgeBase(models.Model):
    class RetrievalMode(models.TextChoices):
        VECTOR = "VECTOR", "纯向量"
        HYBRID = "HYBRID", "混合检索"

    class FusionMethod(models.TextChoices):
        WEIGHTED = "WEIGHTED", "加权融合"
        RRF = "RRF", "RRF排名融合"

    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name="knowledge_bases")
    workspace = models.ForeignKey(
        Workspace, on_delete=models.PROTECT, related_name="knowledge_bases"
    )
    chat_model_config = models.ForeignKey(
        ModelConfig,
        null=True,
        blank=True,
        on_delete=models.RESTRICT,
        related_name="chat_knowledge_bases",
    )
    embedding_model_config = models.ForeignKey(
        ModelConfig,
        null=True,
        blank=True,
        on_delete=models.RESTRICT,
        related_name="embedding_knowledge_bases",
    )
    retrieval_mode = models.CharField(
        max_length=10,
        choices=RetrievalMode.choices,
        default=RetrievalMode.VECTOR,
    )
    retrieval_top_k = models.PositiveSmallIntegerField(default=5)
    similarity_threshold = models.FloatField(default=0)
    vector_weight = models.FloatField(default=1)
    fusion_method = models.CharField(
        max_length=10,
        choices=FusionMethod.choices,
        default=FusionMethod.WEIGHTED,
    )
    vector_candidate_k = models.PositiveSmallIntegerField(default=30)
    keyword_candidate_k = models.PositiveSmallIntegerField(default=30)
    rrf_k = models.PositiveSmallIntegerField(default=60)
    rerank_enabled = models.BooleanField(default=False)
    rerank_candidate_k = models.PositiveSmallIntegerField(default=20)
    max_context_chars = models.PositiveIntegerField(default=6000)
    system_prompt = models.TextField(blank=True, default="", max_length=2000)
    no_answer_message = models.CharField(blank=True, default="", max_length=500)
    agent_enabled = models.BooleanField(default=False)
    agent_max_steps = models.PositiveSmallIntegerField(default=5)
    agent_system_prompt = models.TextField(blank=True, default="", max_length=2000)
    enabled_tools = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(retrieval_top_k__gte=1, retrieval_top_k__lte=20),
                name="knowledge_retrieval_top_k_range",
            ),
            models.CheckConstraint(
                condition=models.Q(similarity_threshold__gte=0, similarity_threshold__lte=1),
                name="knowledge_similarity_threshold_range",
            ),
            models.CheckConstraint(
                condition=models.Q(vector_weight__gte=0, vector_weight__lte=1),
                name="knowledge_vector_weight_range",
            ),
            models.CheckConstraint(
                condition=models.Q(vector_candidate_k__gte=1, vector_candidate_k__lte=100),
                name="knowledge_vector_candidate_k_range",
            ),
            models.CheckConstraint(
                condition=models.Q(keyword_candidate_k__gte=1, keyword_candidate_k__lte=100),
                name="knowledge_keyword_candidate_k_range",
            ),
            models.CheckConstraint(
                condition=models.Q(rrf_k__gte=1, rrf_k__lte=200),
                name="knowledge_rrf_k_range",
            ),
            models.CheckConstraint(
                condition=models.Q(rerank_candidate_k__gte=1, rerank_candidate_k__lte=50),
                name="knowledge_rerank_candidate_k_range",
            ),
            models.CheckConstraint(
                condition=models.Q(max_context_chars__gte=1000, max_context_chars__lte=30000),
                name="knowledge_context_chars_range",
            ),
            models.CheckConstraint(
                condition=models.Q(agent_max_steps__gte=1, agent_max_steps__lte=10),
                name="knowledge_agent_max_steps_range",
            ),
        ]

    def save(self, *args, **kwargs):
        if not self.workspace_id and self.owner_id:
            from .services.workspaces import ensure_personal_workspace

            self.workspace = ensure_personal_workspace(self.owner)
        return super().save(*args, **kwargs)


class Application(models.Model):
    class Status(models.TextChoices):
        DRAFT = "DRAFT", "草稿"
        PUBLISHED = "PUBLISHED", "已发布"
        DISABLED = "DISABLED", "已停用"

    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name="ai_applications")
    workspace = models.ForeignKey(
        Workspace, on_delete=models.PROTECT, related_name="applications"
    )
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True, default="", max_length=1000)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    chat_model_config = models.ForeignKey(
        ModelConfig,
        null=True,
        blank=True,
        on_delete=models.RESTRICT,
        related_name="applications",
    )
    system_prompt = models.TextField(blank=True, default="", max_length=4000)
    welcome_message = models.CharField(blank=True, default="", max_length=500)
    suggested_questions = models.JSONField(default=list, blank=True)
    show_references = models.BooleanField(default=True)
    agent_enabled = models.BooleanField(default=False)
    agent_max_steps = models.PositiveSmallIntegerField(default=5)
    agent_system_prompt = models.TextField(blank=True, default="", max_length=4000)
    enabled_tools = models.JSONField(default=list, blank=True)
    global_top_k = models.PositiveSmallIntegerField(default=5)
    max_context_chars = models.PositiveIntegerField(default=6000)
    current_published_version = models.ForeignKey(
        "ApplicationVersion",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="current_for_applications",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["workspace", "name"], name="unique_workspace_application_name"
            ),
            models.CheckConstraint(
                condition=models.Q(global_top_k__gte=1, global_top_k__lte=20),
                name="application_global_top_k_range",
            ),
            models.CheckConstraint(
                condition=models.Q(agent_max_steps__gte=1, agent_max_steps__lte=10),
                name="application_agent_steps_range",
            ),
            models.CheckConstraint(
                condition=models.Q(max_context_chars__gte=1000, max_context_chars__lte=30000),
                name="application_context_chars_range",
            ),
        ]

    def save(self, *args, **kwargs):
        if not self.workspace_id and self.owner_id:
            from .services.workspaces import ensure_personal_workspace

            self.workspace = ensure_personal_workspace(self.owner)
        return super().save(*args, **kwargs)


class ApplicationKnowledgeBase(models.Model):
    application = models.ForeignKey(
        Application, on_delete=models.CASCADE, related_name="knowledge_links"
    )
    knowledge_base = models.ForeignKey(
        KnowledgeBase, on_delete=models.PROTECT, related_name="application_links"
    )
    position = models.PositiveSmallIntegerField(default=1)
    weight = models.FloatField(default=1.0)
    enabled = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["position", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["application", "knowledge_base"],
                name="unique_application_knowledge_base",
            ),
            models.CheckConstraint(
                condition=models.Q(weight__gte=0.1, weight__lte=3.0),
                name="application_knowledge_weight_range",
            ),
        ]


class ApplicationVersion(models.Model):
    application = models.ForeignKey(
        Application, on_delete=models.CASCADE, related_name="versions"
    )
    version = models.PositiveIntegerField()
    config_snapshot = models.JSONField(default=dict)
    created_by = models.ForeignKey(
        User, on_delete=models.PROTECT, related_name="published_application_versions"
    )
    published_at = models.DateTimeField(auto_now_add=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-version", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["application", "version"], name="unique_application_version"
            )
        ]


class ApplicationCredential(models.Model):
    application = models.ForeignKey(
        Application, on_delete=models.CASCADE, related_name="credentials"
    )
    name = models.CharField(max_length=100)
    key_prefix = models.CharField(max_length=32, unique=True, db_index=True)
    secret_digest = models.CharField(max_length=64)
    last4 = models.CharField(max_length=4)
    enabled = models.BooleanField(default=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]


class ApplicationPublicAccess(models.Model):
    application = models.OneToOneField(
        Application, on_delete=models.CASCADE, related_name="public_access"
    )
    enabled = models.BooleanField(default=False)
    token_prefix = models.CharField(max_length=32, unique=True, db_index=True)
    token_digest = models.CharField(max_length=64)
    token_last4 = models.CharField(max_length=4)
    allowed_frame_origins = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    rotated_at = models.DateTimeField(auto_now=True)


class Document(models.Model):
    class Status(models.TextChoices):
        PROCESSING = "PROCESSING", "处理中"
        SUCCESS = "SUCCESS", "成功"
        FAILURE = "FAILURE", "失败"

    class ParserType(models.TextChoices):
        AUTO = "AUTO", "自动识别"
        TXT = "TXT", "纯文本"
        MARKDOWN = "MARKDOWN", "Markdown"
        PDF = "PDF", "PDF"
        DOCX = "DOCX", "DOCX"

    class ChunkStrategy(models.TextChoices):
        LEGACY = "LEGACY", "兼容切片"
        PARENT_CHILD = "PARENT_CHILD", "父子切片"

    knowledge_base = models.ForeignKey(KnowledgeBase, on_delete=models.CASCADE, related_name="documents")
    name = models.CharField(max_length=255)
    file = models.FileField(upload_to="documents/%Y/%m/%d")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PROCESSING)
    error_message = models.TextField(blank=True)
    paragraph_count = models.PositiveIntegerField(default=0)
    parent_chunk_count = models.PositiveIntegerField(default=0)
    embedding_signature = models.CharField(max_length=100, blank=True, default="")
    source_id = models.CharField(
        max_length=80,
        db_index=True,
        default=generate_document_source_id,
        editable=False,
        unique=True,
    )
    source_sha256 = models.CharField(max_length=64, blank=True, default="")
    parser_type = models.CharField(
        max_length=20,
        choices=ParserType.choices,
        default=ParserType.AUTO,
    )
    chunk_strategy = models.CharField(
        max_length=30,
        choices=ChunkStrategy.choices,
        default=ChunkStrategy.LEGACY,
    )
    parent_max_tokens = models.PositiveSmallIntegerField(default=1500)
    child_target_tokens = models.PositiveSmallIntegerField(default=400)
    child_overlap_tokens = models.PositiveSmallIntegerField(default=60)
    preserve_tables = models.BooleanField(default=True)
    preserve_code_blocks = models.BooleanField(default=True)
    indexed_chunking_signature = models.CharField(max_length=64, blank=True, default="")
    parser_version = models.CharField(max_length=30, blank=True, default="")
    chunker_version = models.CharField(max_length=30, blank=True, default="")
    parsing_warnings = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(parent_max_tokens__gte=400, parent_max_tokens__lte=4000),
                name="document_parent_token_range",
            ),
            models.CheckConstraint(
                condition=models.Q(child_target_tokens__gte=100, child_target_tokens__lte=1200),
                name="document_child_token_range",
            ),
            models.CheckConstraint(
                condition=models.Q(child_overlap_tokens__gte=0, child_overlap_tokens__lte=300),
                name="document_overlap_token_range",
            ),
            models.CheckConstraint(
                condition=models.Q(child_overlap_tokens__lt=models.F("child_target_tokens")),
                name="document_overlap_less_than_child",
            ),
        ]


class DocumentProcessingTask(models.Model):
    class TaskType(models.TextChoices):
        UPLOAD = "UPLOAD", "首次上传"
        REPROCESS = "REPROCESS", "重新处理"

    class Status(models.TextChoices):
        PENDING = "PENDING", "等待处理"
        PROCESSING = "PROCESSING", "处理中"
        RETRYING = "RETRYING", "等待重试"
        SUCCESS = "SUCCESS", "处理成功"
        FAILURE = "FAILURE", "处理失败"
        ENQUEUE_FAILED = "ENQUEUE_FAILED", "任务投递失败"
        CANCEL_REQUESTED = "CANCEL_REQUESTED", "正在取消"
        CANCELLED = "CANCELLED", "已取消"

    class Stage(models.TextChoices):
        WAITING = "WAITING", "等待处理"
        READING = "READING", "正在解析"
        SPLITTING = "SPLITTING", "正在切片"
        EMBEDDING = "EMBEDDING", "正在生成向量"
        SAVING = "SAVING", "正在保存"
        DONE = "DONE", "处理完成"
        FAILED = "FAILED", "处理失败"
        CANCELLING = "CANCELLING", "正在取消"
        CANCELLED = "CANCELLED", "已取消"

    ACTIVE_STATUSES = (
        Status.PENDING,
        Status.PROCESSING,
        Status.RETRYING,
        Status.CANCEL_REQUESTED,
    )

    document = models.ForeignKey(
        Document,
        on_delete=models.CASCADE,
        related_name="processing_tasks",
    )
    task_type = models.CharField(max_length=20, choices=TaskType.choices)
    status = models.CharField(max_length=30, choices=Status.choices, default=Status.PENDING)
    celery_task_id = models.CharField(max_length=255, blank=True, default="")
    progress = models.PositiveSmallIntegerField(default=0)
    current_stage = models.CharField(
        max_length=30,
        choices=Stage.choices,
        default=Stage.WAITING,
    )
    attempt_count = models.PositiveSmallIntegerField(default=0)
    idempotency_key = models.CharField(max_length=100, blank=True, default="")
    error_message = models.CharField(max_length=500, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(progress__gte=0, progress__lte=100),
                name="document_task_progress_range",
            ),
            models.UniqueConstraint(
                fields=["document"],
                condition=models.Q(
                    status__in=("PENDING", "PROCESSING", "RETRYING", "CANCEL_REQUESTED")
                ),
                name="unique_active_document_processing_task",
            ),
            models.UniqueConstraint(
                fields=["document", "idempotency_key"],
                condition=~models.Q(idempotency_key=""),
                name="unique_document_processing_idempotency_key",
            ),
        ]


class DocumentSection(models.Model):
    class StructureType(models.TextChoices):
        TEXT = "TEXT", "正文"
        TABLE = "TABLE", "表格"
        CODE = "CODE", "代码"
        LIST = "LIST", "列表"
        MIXED = "MIXED", "混合"

    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name="sections")
    position = models.PositiveIntegerField()
    content = models.TextField()
    heading_path = models.JSONField(default=list, blank=True)
    page_start = models.PositiveIntegerField(null=True, blank=True)
    page_end = models.PositiveIntegerField(null=True, blank=True)
    token_count = models.PositiveIntegerField(default=0)
    content_sha256 = models.CharField(max_length=64, blank=True, default="", db_index=True)
    parser_version = models.CharField(max_length=30, blank=True, default="")
    chunker_version = models.CharField(max_length=30, blank=True, default="")
    source_block_ids = models.JSONField(default=list, blank=True)
    structure_type = models.CharField(
        max_length=20,
        choices=StructureType.choices,
        default=StructureType.TEXT,
    )

    class Meta:
        ordering = ["position"]
        constraints = [
            models.UniqueConstraint(
                fields=["document", "position"], name="unique_document_section_position"
            )
        ]


class Paragraph(models.Model):
    class ChunkType(models.TextChoices):
        LEGACY = "LEGACY", "兼容切片"
        CHILD = "CHILD", "子切片"

    class StructureType(models.TextChoices):
        TEXT = "TEXT", "正文"
        TABLE = "TABLE", "表格"
        CODE = "CODE", "代码"
        LIST = "LIST", "列表"
        MIXED = "MIXED", "混合"

    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name="paragraphs")
    position = models.PositiveIntegerField()
    content = models.TextField()
    embedding = models.JSONField(default=list)
    chunk_type = models.CharField(
        max_length=20,
        choices=ChunkType.choices,
        default=ChunkType.LEGACY,
    )
    parent_section = models.ForeignKey(
        DocumentSection,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="children",
    )
    heading_path = models.JSONField(default=list, blank=True)
    page_start = models.PositiveIntegerField(null=True, blank=True)
    page_end = models.PositiveIntegerField(null=True, blank=True)
    token_count = models.PositiveIntegerField(default=0)
    content_sha256 = models.CharField(max_length=64, blank=True, default="", db_index=True)
    parser_version = models.CharField(max_length=30, blank=True, default="")
    chunker_version = models.CharField(max_length=30, blank=True, default="")
    source_block_ids = models.JSONField(default=list, blank=True)
    structure_type = models.CharField(
        max_length=20,
        choices=StructureType.choices,
        default=StructureType.TEXT,
    )
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["position"]
        constraints = [
            models.UniqueConstraint(fields=["document", "position"], name="unique_document_position")
        ]


class Conversation(models.Model):
    class AccessType(models.TextChoices):
        KNOWLEDGE = "KNOWLEDGE", "知识库后台"
        PREVIEW = "PREVIEW", "应用预览"
        PUBLIC_WEB = "PUBLIC_WEB", "公开页面"
        EMBED = "EMBED", "嵌入页面"
        API = "API", "第三方API"

    knowledge_base = models.ForeignKey(
        KnowledgeBase,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="conversations",
    )
    application = models.ForeignKey(
        Application,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="conversations",
    )
    application_version = models.ForeignKey(
        ApplicationVersion,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="conversations",
    )
    owner = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.CASCADE, related_name="conversations"
    )
    visitor_id_hash = models.CharField(max_length=64, blank=True, default="")
    access_type = models.CharField(
        max_length=20, choices=AccessType.choices, default=AccessType.KNOWLEDGE
    )
    title = models.CharField(max_length=100, default="新对话")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(
                        knowledge_base__isnull=False,
                        application__isnull=True,
                        owner__isnull=False,
                        visitor_id_hash="",
                    )
                    | models.Q(
                        knowledge_base__isnull=True,
                        application__isnull=False,
                        owner__isnull=False,
                        visitor_id_hash="",
                    )
                    | (
                        models.Q(
                            knowledge_base__isnull=True,
                            application__isnull=False,
                            owner__isnull=True,
                        )
                        & ~models.Q(visitor_id_hash="")
                    )
                ),
                name="valid_conversation_scope",
            )
        ]


class ApplicationAccessLog(models.Model):
    class Status(models.TextChoices):
        RUNNING = "RUNNING", "执行中"
        SUCCESS = "SUCCESS", "成功"
        FAILURE = "FAILURE", "失败"
        REJECTED = "REJECTED", "拒绝"

    application = models.ForeignKey(
        Application, on_delete=models.CASCADE, related_name="access_logs"
    )
    application_version = models.ForeignKey(
        ApplicationVersion,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="access_logs",
    )
    credential = models.ForeignKey(
        ApplicationCredential,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="access_logs",
    )
    conversation = models.ForeignKey(
        Conversation,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="application_access_logs",
    )
    request_id = models.CharField(max_length=64, unique=True)
    access_type = models.CharField(max_length=20, choices=Conversation.AccessType.choices)
    client_fingerprint = models.CharField(max_length=64, blank=True, default="")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.RUNNING)
    status_code = models.PositiveSmallIntegerField(default=200)
    first_token_latency_ms = models.PositiveIntegerField(default=0)
    retrieval_latency_ms = models.PositiveIntegerField(default=0)
    model_latency_ms = models.PositiveIntegerField(default=0)
    total_latency_ms = models.PositiveIntegerField(default=0)
    retrieved_paragraph_count = models.PositiveSmallIntegerField(default=0)
    error_code = models.CharField(max_length=50, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]


class Message(models.Model):
    class Role(models.TextChoices):
        USER = "user", "用户"
        ASSISTANT = "assistant", "助手"

    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name="messages")
    role = models.CharField(max_length=20, choices=Role.choices)
    content = models.TextField()
    references = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]


class AgentRun(models.Model):
    class Status(models.TextChoices):
        RUNNING = "RUNNING", "执行中"
        SUCCESS = "SUCCESS", "成功"
        FAILURE = "FAILURE", "失败"
        LIMIT_REACHED = "LIMIT_REACHED", "达到步骤上限"
        CANCELLED = "CANCELLED", "已取消"

    conversation = models.ForeignKey(
        Conversation,
        on_delete=models.CASCADE,
        related_name="agent_runs",
    )
    user_message = models.OneToOneField(
        Message,
        on_delete=models.CASCADE,
        related_name="agent_run_as_user",
    )
    assistant_message = models.OneToOneField(
        Message,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="agent_run_as_assistant",
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.RUNNING)
    step_count = models.PositiveSmallIntegerField(default=0)
    error_code = models.CharField(max_length=50, blank=True, default="")
    error_message = models.CharField(max_length=500, blank=True, default="")
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]


class ToolExecution(models.Model):
    class Status(models.TextChoices):
        RUNNING = "RUNNING", "执行中"
        SUCCESS = "SUCCESS", "成功"
        FAILURE = "FAILURE", "失败"
        REJECTED = "REJECTED", "已拒绝"

    agent_run = models.ForeignKey(
        AgentRun,
        on_delete=models.CASCADE,
        related_name="tool_executions",
    )
    step = models.PositiveSmallIntegerField()
    sequence = models.PositiveSmallIntegerField()
    tool_call_id = models.CharField(max_length=200)
    tool_name = models.CharField(max_length=100)
    arguments = models.JSONField(default=dict)
    result_summary = models.CharField(max_length=500, blank=True, default="")
    result_payload = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.RUNNING)
    latency_ms = models.PositiveIntegerField(default=0)
    error_code = models.CharField(max_length=50, blank=True, default="")
    error_message = models.CharField(max_length=500, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["step", "sequence", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["agent_run", "step", "sequence"],
                name="unique_agent_tool_step_sequence",
            )
        ]
