from django.urls import path

from .views import (
    AgentRunDetailView,
    ChatStreamView,
    ConversationDetailView,
    ConversationListView,
    ConversationMessageListView,
    DocumentDetailView,
    DocumentChunkingConfigView,
    DocumentChunkPreviewView,
    DocumentListView,
    DocumentParagraphListView,
    DocumentReprocessView,
    DocumentProcessingTaskCancelView,
    DocumentProcessingTaskDetailView,
    DocumentProcessingTaskListView,
    DocumentProcessingTaskRetryView,
    HealthView,
    KnowledgeBaseDetailView,
    KnowledgeBaseListView,
    KnowledgeBaseModelConfigView,
    KnowledgeBaseAgentConfigView,
    KnowledgeBaseRetrievalConfigView,
    KnowledgeBaseRetrievalCompareView,
    KnowledgeBaseRetrievalDebugView,
    LoginView,
    ModelConfigDetailView,
    ModelConfigListView,
    ModelConfigTestView,
    SearchView,
    RetrievalCapabilitiesView,
    RegisterView,
)
from .application_views import (
    ApplicationAccessLogListView,
    ApplicationChatCompletionsView,
    ApplicationCredentialDetailView,
    ApplicationCredentialListView,
    ApplicationDetailView,
    ApplicationDisableView,
    ApplicationKnowledgeBaseView,
    ApplicationListView,
    ApplicationPreviewChatView,
    ApplicationPublicAccessDisableView,
    ApplicationPublicAccessEnableView,
    ApplicationPublicAccessRotateView,
    ApplicationPublicAccessView,
    ApplicationPublishView,
    ApplicationRollbackView,
    ApplicationVersionDetailView,
    ApplicationVersionListView,
    PublicApplicationChatView,
    PublicApplicationEmbedView,
    PublicApplicationMessageListView,
    PublicApplicationProfileView,
    PublicApplicationVisitorView,
)
from .workspace_views import (
    AuditEventListView,
    MeContextView,
    OrganizationDetailView,
    OrganizationListView,
    OrganizationMemberDetailView,
    OrganizationMemberListView,
    OrganizationWorkspaceDetailView,
    OrganizationWorkspaceListView,
    WorkspaceMemberDetailView,
    WorkspaceMemberListView,
)


urlpatterns = [
    path("health/", HealthView.as_view()),
    path("login/", LoginView.as_view()),
    path("register/", RegisterView.as_view()),
    path("me/context/", MeContextView.as_view()),
    path("organizations/", OrganizationListView.as_view()),
    path("organizations/<int:organization_id>/", OrganizationDetailView.as_view()),
    path("organizations/<int:organization_id>/members/", OrganizationMemberListView.as_view()),
    path(
        "organizations/<int:organization_id>/members/<int:membership_id>/",
        OrganizationMemberDetailView.as_view(),
    ),
    path("organizations/<int:organization_id>/workspaces/", OrganizationWorkspaceListView.as_view()),
    path(
        "organizations/<int:organization_id>/workspaces/<int:workspace_id>/",
        OrganizationWorkspaceDetailView.as_view(),
    ),
    path("workspaces/<int:workspace_id>/members/", WorkspaceMemberListView.as_view()),
    path(
        "workspaces/<int:workspace_id>/members/<int:membership_id>/",
        WorkspaceMemberDetailView.as_view(),
    ),
    path("audit-events/", AuditEventListView.as_view()),
    path("model-configs/", ModelConfigListView.as_view()),
    path("model-configs/<int:config_id>/", ModelConfigDetailView.as_view()),
    path("model-configs/<int:config_id>/test/", ModelConfigTestView.as_view()),
    path("retrieval/capabilities/", RetrievalCapabilitiesView.as_view()),
    path("applications/", ApplicationListView.as_view()),
    path("applications/<int:application_id>/", ApplicationDetailView.as_view()),
    path(
        "applications/<int:application_id>/knowledge-bases/",
        ApplicationKnowledgeBaseView.as_view(),
    ),
    path(
        "applications/<int:application_id>/preview/chat/stream/",
        ApplicationPreviewChatView.as_view(),
    ),
    path("applications/<int:application_id>/publish/", ApplicationPublishView.as_view()),
    path("applications/<int:application_id>/disable/", ApplicationDisableView.as_view()),
    path("applications/<int:application_id>/versions/", ApplicationVersionListView.as_view()),
    path(
        "applications/<int:application_id>/versions/<int:version_id>/",
        ApplicationVersionDetailView.as_view(),
    ),
    path(
        "applications/<int:application_id>/versions/<int:version_id>/rollback/",
        ApplicationRollbackView.as_view(),
    ),
    path(
        "applications/<int:application_id>/credentials/",
        ApplicationCredentialListView.as_view(),
    ),
    path(
        "applications/<int:application_id>/credentials/<int:credential_id>/",
        ApplicationCredentialDetailView.as_view(),
    ),
    path(
        "applications/<int:application_id>/public-access/",
        ApplicationPublicAccessView.as_view(),
    ),
    path(
        "applications/<int:application_id>/public-access/enable/",
        ApplicationPublicAccessEnableView.as_view(),
    ),
    path(
        "applications/<int:application_id>/public-access/rotate/",
        ApplicationPublicAccessRotateView.as_view(),
    ),
    path(
        "applications/<int:application_id>/public-access/disable/",
        ApplicationPublicAccessDisableView.as_view(),
    ),
    path(
        "applications/<int:application_id>/access-logs/",
        ApplicationAccessLogListView.as_view(),
    ),
    path(
        "public/applications/<str:public_token>/profile/",
        PublicApplicationProfileView.as_view(),
    ),
    path(
        "public/applications/<str:public_token>/visitor/",
        PublicApplicationVisitorView.as_view(),
    ),
    path(
        "public/applications/<str:public_token>/chat/stream/",
        PublicApplicationChatView.as_view(),
    ),
    path(
        "public/applications/<str:public_token>/embed/",
        PublicApplicationEmbedView.as_view(),
    ),
    path(
        "public/applications/<str:public_token>/conversations/<int:conversation_id>/messages/",
        PublicApplicationMessageListView.as_view(),
    ),
    path(
        "v1/applications/<int:application_id>/chat/completions",
        ApplicationChatCompletionsView.as_view(),
    ),
    path("knowledge-bases/", KnowledgeBaseListView.as_view()),
    path("knowledge-bases/<int:pk>/", KnowledgeBaseDetailView.as_view()),
    path(
        "knowledge-bases/<int:knowledge_id>/model-config/",
        KnowledgeBaseModelConfigView.as_view(),
    ),
    path(
        "knowledge-bases/<int:knowledge_id>/retrieval-config/",
        KnowledgeBaseRetrievalConfigView.as_view(),
    ),
    path(
        "knowledge-bases/<int:knowledge_id>/retrieval/debug/",
        KnowledgeBaseRetrievalDebugView.as_view(),
    ),
    path(
        "knowledge-bases/<int:knowledge_id>/retrieval/compare/",
        KnowledgeBaseRetrievalCompareView.as_view(),
    ),
    path(
        "knowledge-bases/<int:knowledge_id>/agent-config/",
        KnowledgeBaseAgentConfigView.as_view(),
    ),
    path(
        "knowledge-bases/<int:knowledge_id>/agent-runs/<int:agent_run_id>/",
        AgentRunDetailView.as_view(),
    ),
    path("knowledge-bases/<int:pk>/documents/", DocumentListView.as_view()),
    path(
        "knowledge-bases/<int:knowledge_id>/documents/<int:document_id>/",
        DocumentDetailView.as_view(),
    ),
    path(
        "knowledge-bases/<int:knowledge_id>/documents/<int:document_id>/paragraphs/",
        DocumentParagraphListView.as_view(),
    ),
    path(
        "knowledge-bases/<int:knowledge_id>/documents/<int:document_id>/reprocess/",
        DocumentReprocessView.as_view(),
    ),
    path(
        "knowledge-bases/<int:knowledge_id>/documents/<int:document_id>/reindex/",
        DocumentReprocessView.as_view(),
    ),
    path(
        "knowledge-bases/<int:knowledge_id>/documents/<int:document_id>/chunking-config/",
        DocumentChunkingConfigView.as_view(),
    ),
    path(
        "knowledge-bases/<int:knowledge_id>/documents/<int:document_id>/chunk-preview/",
        DocumentChunkPreviewView.as_view(),
    ),
    path(
        "knowledge-bases/<int:knowledge_id>/processing-tasks/",
        DocumentProcessingTaskListView.as_view(),
    ),
    path(
        "knowledge-bases/<int:knowledge_id>/processing-tasks/<int:task_id>/",
        DocumentProcessingTaskDetailView.as_view(),
    ),
    path(
        "knowledge-bases/<int:knowledge_id>/processing-tasks/<int:task_id>/retry/",
        DocumentProcessingTaskRetryView.as_view(),
    ),
    path(
        "knowledge-bases/<int:knowledge_id>/processing-tasks/<int:task_id>/cancel/",
        DocumentProcessingTaskCancelView.as_view(),
    ),
    path("knowledge-bases/<int:pk>/search/", SearchView.as_view()),
    path("knowledge-bases/<int:pk>/conversations/", ConversationListView.as_view()),
    path(
        "knowledge-bases/<int:knowledge_id>/conversations/<int:conversation_id>/",
        ConversationDetailView.as_view(),
    ),
    path(
        "knowledge-bases/<int:knowledge_id>/conversations/<int:conversation_id>/messages/",
        ConversationMessageListView.as_view(),
    ),
    path("knowledge-bases/<int:pk>/chat/stream/", ChatStreamView.as_view()),
]
