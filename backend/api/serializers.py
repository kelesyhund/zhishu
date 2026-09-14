from pathlib import Path

from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.core.validators import RegexValidator
from rest_framework import serializers

from .models import (
    AgentRun,
    AccountProfile,
    Application,
    ApplicationAccessLog,
    ApplicationCredential,
    ApplicationKnowledgeBase,
    ApplicationPublicAccess,
    ApplicationVersion,
    Conversation,
    Document,
    DocumentProcessingTask,
    KnowledgeBase,
    Message,
    ModelConfig,
    Paragraph,
    ToolExecution,
)
from .services.agent_tools.registry import TOOL_DEFINITIONS, TOOL_REGISTRY
from .services.model_crypto import ModelCryptoError, encrypt_api_key, mask_api_key
from .services.model_endpoint_security import EndpointValidationError, validate_model_endpoint
from .services.model_resolution import document_needs_reprocess


class RegisterSerializer(serializers.Serializer):
    username = serializers.CharField(
        min_length=3,
        max_length=30,
        validators=[
            RegexValidator(
                regex=r"^[\w.@+-]+$",
                message="用户名只能包含字母、数字、中文及 @/./+/-/_ 字符",
            )
        ],
    )
    email = serializers.EmailField(required=False, allow_blank=True, max_length=254)
    password = serializers.CharField(write_only=True, min_length=8, max_length=128, trim_whitespace=False)
    password_confirm = serializers.CharField(
        write_only=True,
        min_length=8,
        max_length=128,
        trim_whitespace=False,
    )

    def validate_username(self, value):
        username = value.strip()
        if User.objects.filter(username__iexact=username).exists():
            raise serializers.ValidationError("该用户名已被使用")
        return username

    def validate_email(self, value):
        normalized = value.strip().lower()
        if normalized and AccountProfile.objects.filter(normalized_email=normalized).exists():
            raise serializers.ValidationError("该邮箱已被使用")
        return normalized

    def validate(self, attrs):
        if attrs["password"] != attrs.pop("password_confirm"):
            raise serializers.ValidationError({"password_confirm": "两次输入的密码不一致"})
        if attrs["password"].casefold() == attrs["username"].casefold():
            raise serializers.ValidationError({"password": "密码不能与用户名相同"})
        validate_password(attrs["password"])
        return attrs

    def create(self, validated_data):
        user = User.objects.create_user(**validated_data)
        email = validated_data.get("email", "")
        if email:
            profile, _ = AccountProfile.objects.get_or_create(user=user)
            profile.normalized_email = email
            profile.save(update_fields=["normalized_email", "updated_at"])
        return user


class KnowledgeBaseSerializer(serializers.ModelSerializer):
    document_count = serializers.IntegerField(source="documents.count", read_only=True)

    def validate_name(self, value):
        name = value.strip()
        if not name:
            raise serializers.ValidationError("知识库名称不能为空")
        return name

    def validate_description(self, value):
        return value.strip()

    class Meta:
        model = KnowledgeBase
        fields = ["id", "name", "description", "document_count", "created_at"]
        read_only_fields = ["id", "document_count", "created_at"]


class RetrievalConfigSerializer(serializers.ModelSerializer):
    def validate_retrieval_top_k(self, value):
        if value < 1 or value > 20:
            raise serializers.ValidationError("返回切片数量必须在1到20之间")
        return value

    def validate_similarity_threshold(self, value):
        if value < 0 or value > 1:
            raise serializers.ValidationError("最低相关度阈值必须在0到1之间")
        return value

    def validate_vector_weight(self, value):
        if value < 0 or value > 1:
            raise serializers.ValidationError("向量权重必须在0到1之间")
        return value

    def validate_max_context_chars(self, value):
        if value < 1000 or value > 30000:
            raise serializers.ValidationError("最大上下文字符数必须在1000到30000之间")
        return value

    def validate_vector_candidate_k(self, value):
        if value < 1 or value > 100:
            raise serializers.ValidationError("向量候选数量必须在1到100之间")
        return value

    def validate_keyword_candidate_k(self, value):
        if value < 1 or value > 100:
            raise serializers.ValidationError("关键词候选数量必须在1到100之间")
        return value

    def validate_rrf_k(self, value):
        if value < 1 or value > 200:
            raise serializers.ValidationError("RRF参数必须在1到200之间")
        return value

    def validate_rerank_candidate_k(self, value):
        if value < 1 or value > 50:
            raise serializers.ValidationError("重排候选数量必须在1到50之间")
        return value

    def validate_system_prompt(self, value):
        return value.strip()

    def validate_no_answer_message(self, value):
        return value.strip()

    def validate(self, attrs):
        current = self.instance
        value = lambda name, default: attrs.get(name, getattr(current, name, default))
        mode = value("retrieval_mode", KnowledgeBase.RetrievalMode.VECTOR)
        fusion = value("fusion_method", KnowledgeBase.FusionMethod.WEIGHTED)
        rerank_enabled = value("rerank_enabled", False)
        top_k = value("retrieval_top_k", 5)
        vector_k = value("vector_candidate_k", 30)
        keyword_k = value("keyword_candidate_k", 30)
        rerank_k = value("rerank_candidate_k", 20)
        if rerank_enabled and not (
            mode == KnowledgeBase.RetrievalMode.HYBRID
            and fusion == KnowledgeBase.FusionMethod.RRF
        ):
            raise serializers.ValidationError(
                {"rerank_enabled": "Cross-Encoder重排只能与混合检索的RRF融合一起启用"}
            )
        if fusion == KnowledgeBase.FusionMethod.RRF and min(vector_k, keyword_k) < top_k:
            raise serializers.ValidationError("两路候选数量不能小于最终返回数量")
        if rerank_enabled and rerank_k < top_k:
            raise serializers.ValidationError(
                {"rerank_candidate_k": "重排候选数量不能小于最终返回数量"}
            )
        if rerank_enabled and rerank_k > vector_k + keyword_k:
            raise serializers.ValidationError(
                {"rerank_candidate_k": "重排候选数量不能超过两路召回候选数量之和"}
            )
        return attrs

    class Meta:
        model = KnowledgeBase
        fields = [
            "retrieval_mode",
            "retrieval_top_k",
            "similarity_threshold",
            "vector_weight",
            "fusion_method",
            "vector_candidate_k",
            "keyword_candidate_k",
            "rrf_k",
            "rerank_enabled",
            "rerank_candidate_k",
            "max_context_chars",
            "system_prompt",
            "no_answer_message",
        ]


class RetrievalDebugRequestSerializer(serializers.Serializer):
    query = serializers.CharField(max_length=1000, trim_whitespace=True)
    candidate_limit = serializers.IntegerField(default=20, min_value=1, max_value=50)

    def validate_query(self, value):
        query = value.strip()
        if not query:
            raise serializers.ValidationError("测试问题不能为空")
        return query


class RetrievalExperimentSerializer(serializers.Serializer):
    retrieval_mode = serializers.ChoiceField(
        choices=KnowledgeBase.RetrievalMode.choices,
        required=False,
    )
    fusion_method = serializers.ChoiceField(
        choices=KnowledgeBase.FusionMethod.choices,
        required=False,
    )
    retrieval_top_k = serializers.IntegerField(min_value=1, max_value=20, required=False)
    similarity_threshold = serializers.FloatField(min_value=0, max_value=1, required=False)
    vector_weight = serializers.FloatField(min_value=0, max_value=1, required=False)
    vector_candidate_k = serializers.IntegerField(min_value=1, max_value=100, required=False)
    keyword_candidate_k = serializers.IntegerField(min_value=1, max_value=100, required=False)
    rrf_k = serializers.IntegerField(min_value=1, max_value=200, required=False)
    rerank_enabled = serializers.BooleanField(required=False)
    rerank_candidate_k = serializers.IntegerField(min_value=1, max_value=50, required=False)
    max_context_chars = serializers.IntegerField(min_value=1000, max_value=30000, required=False)


class RetrievalCompareRequestSerializer(RetrievalDebugRequestSerializer):
    experimental = RetrievalExperimentSerializer()


class AgentConfigSerializer(serializers.ModelSerializer):
    def validate_agent_max_steps(self, value):
        if value < 1 or value > 10:
            raise serializers.ValidationError("最大执行步骤必须在1到10之间")
        return value

    def validate_agent_system_prompt(self, value):
        return value.strip()

    def validate_enabled_tools(self, value):
        if not isinstance(value, list):
            raise serializers.ValidationError("允许工具必须是列表")
        unknown = [item for item in value if not isinstance(item, str) or item not in TOOL_REGISTRY]
        if unknown:
            raise serializers.ValidationError("包含未注册的工具")
        enabled = set(value)
        return [tool.name for tool in TOOL_DEFINITIONS if tool.name in enabled]

    class Meta:
        model = KnowledgeBase
        fields = ["agent_enabled", "agent_max_steps", "agent_system_prompt", "enabled_tools"]


class DocumentSerializer(serializers.ModelSerializer):
    needs_reprocess = serializers.SerializerMethodField()

    def get_needs_reprocess(self, obj):
        return document_needs_reprocess(obj)

    class Meta:
        model = Document
        fields = [
            "id",
            "name",
            "status",
            "error_message",
            "paragraph_count",
            "parent_chunk_count",
            "needs_reprocess",
            "source_id",
            "source_sha256",
            "parser_type",
            "chunk_strategy",
            "parent_max_tokens",
            "child_target_tokens",
            "child_overlap_tokens",
            "preserve_tables",
            "preserve_code_blocks",
            "parser_version",
            "chunker_version",
            "parsing_warnings",
            "created_at",
        ]
        read_only_fields = fields


class DocumentChunkingConfigSerializer(serializers.ModelSerializer):
    def validate_parser_type(self, value):
        if value == Document.ParserType.AUTO or not self.instance:
            return value
        suffix_map = {".txt": "TXT", ".md": "MARKDOWN", ".pdf": "PDF", ".docx": "DOCX"}
        suffix = Path(self.instance.name).suffix.lower()
        if suffix_map.get(suffix) != value:
            raise serializers.ValidationError("选择的解析器与文档文件格式不匹配")
        return value

    def validate(self, attrs):
        instance = self.instance
        value = lambda name, default: attrs.get(name, getattr(instance, name, default))
        child_tokens = value("child_target_tokens", 400)
        overlap = value("child_overlap_tokens", 60)
        parent_tokens = value("parent_max_tokens", 1500)
        if overlap >= child_tokens:
            raise serializers.ValidationError(
                {"child_overlap_tokens": "Child重叠必须小于Child目标长度"}
            )
        if parent_tokens < child_tokens:
            raise serializers.ValidationError(
                {"parent_max_tokens": "Parent最大长度不能小于Child目标长度"}
            )
        return attrs

    class Meta:
        model = Document
        fields = [
            "parser_type",
            "chunk_strategy",
            "parent_max_tokens",
            "child_target_tokens",
            "child_overlap_tokens",
            "preserve_tables",
            "preserve_code_blocks",
        ]
        extra_kwargs = {
            "parent_max_tokens": {"min_value": 400, "max_value": 4000},
            "child_target_tokens": {"min_value": 100, "max_value": 1200},
            "child_overlap_tokens": {"min_value": 0, "max_value": 300},
        }


class ParagraphSerializer(serializers.ModelSerializer):
    parent_id = serializers.IntegerField(source="parent_section_id", read_only=True)
    parent_position = serializers.SerializerMethodField()

    def get_parent_position(self, obj):
        return obj.parent_section.position if obj.parent_section_id else None

    class Meta:
        model = Paragraph
        fields = [
            "id",
            "position",
            "content",
            "chunk_type",
            "parent_id",
            "parent_position",
            "heading_path",
            "page_start",
            "page_end",
            "token_count",
            "content_sha256",
            "source_block_ids",
            "structure_type",
        ]


class DocumentProcessingTaskListSerializer(serializers.ModelSerializer):
    document_id = serializers.IntegerField(read_only=True)
    document_name = serializers.CharField(source="document.name", read_only=True)

    class Meta:
        model = DocumentProcessingTask
        fields = [
            "id",
            "document_id",
            "document_name",
            "task_type",
            "status",
            "progress",
            "current_stage",
            "attempt_count",
            "error_message",
            "created_at",
            "started_at",
            "finished_at",
            "updated_at",
        ]


class DocumentProcessingTaskDetailSerializer(DocumentProcessingTaskListSerializer):
    class Meta(DocumentProcessingTaskListSerializer.Meta):
        fields = DocumentProcessingTaskListSerializer.Meta.fields


class ToolExecutionSerializer(serializers.ModelSerializer):
    class Meta:
        model = ToolExecution
        fields = [
            "id",
            "step",
            "sequence",
            "tool_call_id",
            "tool_name",
            "arguments",
            "result_summary",
            "result_payload",
            "status",
            "latency_ms",
            "error_code",
            "error_message",
            "created_at",
            "finished_at",
        ]


class AgentRunSerializer(serializers.ModelSerializer):
    tool_executions = ToolExecutionSerializer(many=True, read_only=True)
    user_message_id = serializers.IntegerField(read_only=True)
    assistant_message_id = serializers.IntegerField(read_only=True, allow_null=True)

    class Meta:
        model = AgentRun
        fields = [
            "id",
            "status",
            "step_count",
            "error_code",
            "error_message",
            "user_message_id",
            "assistant_message_id",
            "started_at",
            "finished_at",
            "tool_executions",
        ]


class MessageSerializer(serializers.ModelSerializer):
    agent_trace = serializers.SerializerMethodField()

    def get_agent_trace(self, obj):
        assistant_run = getattr(obj, "agent_run_as_assistant", None)
        if assistant_run is not None:
            return AgentRunSerializer(assistant_run).data
        user_run = getattr(obj, "agent_run_as_user", None)
        if user_run is not None and user_run.assistant_message_id is None:
            return AgentRunSerializer(user_run).data
        return None

    class Meta:
        model = Message
        fields = ["id", "role", "content", "references", "agent_trace", "created_at"]


class ConversationListSerializer(serializers.ModelSerializer):
    message_count = serializers.IntegerField(read_only=True)
    last_message_at = serializers.DateTimeField(read_only=True, allow_null=True)

    class Meta:
        model = Conversation
        fields = ["id", "title", "message_count", "created_at", "last_message_at"]


class ConversationDetailSerializer(ConversationListSerializer):
    pass


class ConversationTitleSerializer(serializers.ModelSerializer):
    def validate_title(self, value):
        title = value.strip()
        if not title:
            raise serializers.ValidationError("会话标题不能为空")
        return title

    class Meta:
        model = Conversation
        fields = ["title"]


class ModelConfigReadSerializer(serializers.ModelSerializer):
    api_key_configured = serializers.SerializerMethodField()
    api_key_masked = serializers.SerializerMethodField()

    def get_api_key_configured(self, obj):
        return bool(obj.encrypted_api_key)

    def get_api_key_masked(self, obj):
        return mask_api_key(obj.api_key_last4) if obj.encrypted_api_key else ""

    class Meta:
        model = ModelConfig
        fields = [
            "id",
            "name",
            "model_type",
            "base_url",
            "model_name",
            "timeout_seconds",
            "revision",
            "api_key_configured",
            "api_key_masked",
            "last_test_status",
            "last_test_message",
            "last_test_at",
            "embedding_dimension",
            "created_at",
            "updated_at",
        ]


class ModelConfigWriteSerializer(serializers.ModelSerializer):
    api_key = serializers.CharField(
        write_only=True,
        required=False,
        allow_blank=True,
        trim_whitespace=True,
        max_length=1000,
    )

    def validate_name(self, value):
        name = value.strip()
        if not name:
            raise serializers.ValidationError("配置名称不能为空")
        return name

    def validate_model_name(self, value):
        model_name = value.strip()
        if not model_name:
            raise serializers.ValidationError("模型名称不能为空")
        return model_name

    def validate_base_url(self, value):
        try:
            return validate_model_endpoint(value)
        except EndpointValidationError as exc:
            raise serializers.ValidationError(str(exc)) from exc

    def validate_timeout_seconds(self, value):
        if value < 1 or value > 120:
            raise serializers.ValidationError("超时时间必须在1到120秒之间")
        return value

    def validate(self, attrs):
        if self.instance and "model_type" in attrs and attrs["model_type"] != self.instance.model_type:
            raise serializers.ValidationError({"model_type": "模型类型创建后不能修改"})
        name = attrs.get("name", self.instance.name if self.instance else "")
        duplicate = ModelConfig.objects.filter(workspace=self.context["workspace"], name=name)
        if self.instance:
            duplicate = duplicate.exclude(pk=self.instance.pk)
        if name and duplicate.exists():
            raise serializers.ValidationError({"name": "模型配置名称已存在"})
        api_key = attrs.get("api_key")
        if self.instance is None and not api_key:
            raise serializers.ValidationError({"api_key": "API Key不能为空"})
        if api_key and ("••••" in api_key or "****" in api_key):
            raise serializers.ValidationError({"api_key": "不能把脱敏文本作为API Key保存"})
        return attrs

    def _encrypted_key_values(self, api_key):
        try:
            return encrypt_api_key(api_key), api_key[-4:]
        except ModelCryptoError as exc:
            raise serializers.ValidationError({"api_key": str(exc)}) from exc

    def create(self, validated_data):
        api_key = validated_data.pop("api_key")
        encrypted, last4 = self._encrypted_key_values(api_key)
        return ModelConfig.objects.create(
            owner=self.context["request"].user,
            workspace=self.context["workspace"],
            encrypted_api_key=encrypted,
            api_key_last4=last4,
            **validated_data,
        )

    def update(self, instance, validated_data):
        api_key = validated_data.pop("api_key", "")
        semantic_change = any(
            field in validated_data and validated_data[field] != getattr(instance, field)
            for field in ("base_url", "model_name")
        )
        if semantic_change:
            validated_data["revision"] = instance.revision + 1
        if api_key:
            encrypted, last4 = self._encrypted_key_values(api_key)
            validated_data["encrypted_api_key"] = encrypted
            validated_data["api_key_last4"] = last4
        if semantic_change or api_key:
            validated_data["last_test_status"] = ModelConfig.TestStatus.UNTESTED
            validated_data["last_test_message"] = ""
            validated_data["last_test_at"] = None
            validated_data["embedding_dimension"] = None
        return super().update(instance, validated_data)

    class Meta:
        model = ModelConfig
        fields = [
            "name",
            "model_type",
            "base_url",
            "model_name",
            "api_key",
            "timeout_seconds",
        ]


class KnowledgeBaseModelSelectionSerializer(serializers.Serializer):
    chat_model_config_id = serializers.IntegerField(required=False, allow_null=True, min_value=1)
    embedding_model_config_id = serializers.IntegerField(required=False, allow_null=True, min_value=1)

    def validate(self, attrs):
        request = self.context["request"]
        mappings = {
            "chat_model_config_id": ModelConfig.ModelType.CHAT,
            "embedding_model_config_id": ModelConfig.ModelType.EMBEDDING,
        }
        resolved = {}
        for field, model_type in mappings.items():
            if field not in attrs:
                continue
            config_id = attrs[field]
            if config_id is None:
                resolved[field] = None
                continue
            config = ModelConfig.objects.filter(
                pk=config_id,
                workspace=self.context["workspace"],
                model_type=model_type,
            ).first()
            if not config:
                raise serializers.ValidationError({field: "模型配置不存在或类型不匹配"})
            resolved[field] = config
        attrs["resolved_configs"] = resolved
        return attrs


class ApplicationKnowledgeLinkSerializer(serializers.ModelSerializer):
    knowledge_base_id = serializers.IntegerField()
    knowledge_base_name = serializers.CharField(source="knowledge_base.name", read_only=True)

    class Meta:
        model = ApplicationKnowledgeBase
        fields = ["knowledge_base_id", "knowledge_base_name", "position", "weight", "enabled"]


class ApplicationReadSerializer(serializers.ModelSerializer):
    knowledge_bases = serializers.SerializerMethodField()
    current_published_version_number = serializers.SerializerMethodField()
    version_count = serializers.IntegerField(read_only=True, default=0)

    def get_knowledge_bases(self, obj):
        links = obj.knowledge_links.all().order_by("position", "id")
        return ApplicationKnowledgeLinkSerializer(links, many=True).data

    def get_current_published_version_number(self, obj):
        version = obj.current_published_version
        return version.version if version else None

    class Meta:
        model = Application
        fields = [
            "id",
            "name",
            "description",
            "status",
            "chat_model_config_id",
            "system_prompt",
            "welcome_message",
            "suggested_questions",
            "show_references",
            "agent_enabled",
            "agent_max_steps",
            "agent_system_prompt",
            "enabled_tools",
            "global_top_k",
            "max_context_chars",
            "knowledge_bases",
            "current_published_version_number",
            "version_count",
            "created_at",
            "updated_at",
        ]


class ApplicationWriteSerializer(serializers.ModelSerializer):
    chat_model_config_id = serializers.IntegerField(required=False, allow_null=True, min_value=1)

    def validate_name(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("应用名称不能为空")
        return value

    def validate_description(self, value):
        return value.strip()

    def validate_suggested_questions(self, value):
        if not isinstance(value, list) or len(value) > 6:
            raise serializers.ValidationError("建议问题必须是最多六项的数组")
        result = []
        for item in value:
            if not isinstance(item, str) or not item.strip() or len(item.strip()) > 200:
                raise serializers.ValidationError("每条建议问题必须是1到200字的文本")
            result.append(item.strip())
        return result

    def validate_enabled_tools(self, value):
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            raise serializers.ValidationError("工具列表格式错误")
        unknown = sorted(set(value) - set(TOOL_REGISTRY))
        if unknown:
            raise serializers.ValidationError(f"不存在的工具：{', '.join(unknown)}")
        return list(dict.fromkeys(value))

    def validate(self, attrs):
        owner = self.context["request"].user
        workspace = self.context["workspace"]
        name = attrs.get("name", self.instance.name if self.instance else "")
        duplicate = Application.objects.filter(workspace=workspace, name=name)
        if self.instance:
            duplicate = duplicate.exclude(pk=self.instance.pk)
        if name and duplicate.exists():
            raise serializers.ValidationError({"name": "应用名称已存在"})
        if "chat_model_config_id" in attrs:
            config_id = attrs["chat_model_config_id"]
            if config_id is not None and not ModelConfig.objects.filter(
                pk=config_id,
                workspace=workspace,
                model_type=ModelConfig.ModelType.CHAT,
            ).exists():
                raise serializers.ValidationError({"chat_model_config_id": "Chat模型配置不存在"})
        return attrs

    def create(self, validated_data):
        return Application.objects.create(
            owner=self.context["request"].user,
            workspace=self.context["workspace"],
            **validated_data,
        )

    class Meta:
        model = Application
        fields = [
            "name",
            "description",
            "chat_model_config_id",
            "system_prompt",
            "welcome_message",
            "suggested_questions",
            "show_references",
            "agent_enabled",
            "agent_max_steps",
            "agent_system_prompt",
            "enabled_tools",
            "global_top_k",
            "max_context_chars",
        ]


class ApplicationKnowledgeSelectionItemSerializer(serializers.Serializer):
    knowledge_base_id = serializers.IntegerField(min_value=1)
    position = serializers.IntegerField(min_value=1, max_value=5)
    weight = serializers.FloatField(min_value=0.1, max_value=3.0)
    enabled = serializers.BooleanField(default=True)


class ApplicationKnowledgeSelectionSerializer(serializers.Serializer):
    knowledge_bases = ApplicationKnowledgeSelectionItemSerializer(many=True)

    def validate_knowledge_bases(self, items):
        if len(items) > 5:
            raise serializers.ValidationError("一个应用最多绑定五个知识库")
        ids = [item["knowledge_base_id"] for item in items]
        if len(ids) != len(set(ids)):
            raise serializers.ValidationError("知识库不能重复绑定")
        if len({item["position"] for item in items}) != len(items):
            raise serializers.ValidationError("知识库顺序不能重复")
        workspace = self.context["workspace"]
        if KnowledgeBase.objects.filter(id__in=ids, workspace=workspace).count() != len(ids):
            raise serializers.ValidationError("知识库不存在或无权访问")
        return items


class ApplicationVersionSerializer(serializers.ModelSerializer):
    class Meta:
        model = ApplicationVersion
        fields = ["id", "version", "config_snapshot", "published_at", "created_at"]


class ApplicationCredentialSerializer(serializers.ModelSerializer):
    api_key_masked = serializers.SerializerMethodField()

    def get_api_key_masked(self, obj):
        return f"kc_app_{obj.key_prefix}_••••{obj.last4}"

    class Meta:
        model = ApplicationCredential
        fields = [
            "id",
            "name",
            "key_prefix",
            "api_key_masked",
            "enabled",
            "expires_at",
            "last_used_at",
            "created_at",
        ]


class ApplicationCredentialWriteSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=100)
    expires_at = serializers.DateTimeField(required=False, allow_null=True)

    def validate_name(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("凭证名称不能为空")
        return value


class ApplicationPublicAccessSerializer(serializers.ModelSerializer):
    token_masked = serializers.SerializerMethodField()

    def get_token_masked(self, obj):
        return f"kc_pub_{obj.token_prefix}_••••{obj.token_last4}"

    class Meta:
        model = ApplicationPublicAccess
        fields = ["enabled", "token_masked", "allowed_frame_origins", "created_at", "rotated_at"]


class ApplicationFrameOriginsSerializer(serializers.Serializer):
    allowed_frame_origins = serializers.ListField(
        child=serializers.URLField(max_length=300), max_length=20
    )

    def validate_allowed_frame_origins(self, values):
        from urllib.parse import urlsplit

        normalized = []
        for value in values:
            parsed = urlsplit(value)
            if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password:
                raise serializers.ValidationError("嵌入白名单只接受http或https Origin")
            if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
                raise serializers.ValidationError("嵌入白名单必须是Origin，不能包含路径或参数")
            origin = f"{parsed.scheme}://{parsed.netloc}".lower()
            if origin not in normalized:
                normalized.append(origin)
        return normalized


class ApplicationAccessLogSerializer(serializers.ModelSerializer):
    version = serializers.IntegerField(source="application_version.version", allow_null=True)
    credential_name = serializers.CharField(source="credential.name", allow_null=True)

    class Meta:
        model = ApplicationAccessLog
        fields = [
            "request_id",
            "access_type",
            "status",
            "status_code",
            "version",
            "credential_name",
            "first_token_latency_ms",
            "retrieval_latency_ms",
            "model_latency_ms",
            "total_latency_ms",
            "retrieved_paragraph_count",
            "error_code",
            "created_at",
        ]
