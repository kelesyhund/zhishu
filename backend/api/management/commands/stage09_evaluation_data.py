import json
from pathlib import Path

from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand, CommandError

from api.models import Document, KnowledgeBase, Paragraph
from api.services.embeddings import local_embedding
from api.services.retrieval_evaluation import content_sha256


USERNAME = "stage09-eval-user"
KNOWLEDGE_NAME = "stage09-synthetic-evaluation"
TOPICS = [
    ("celery", "异步任务", "Celery Worker从Redis Broker领取任务，并通过幂等状态避免重复提交破坏数据"),
    ("redis", "分布式协调", "Redis使用带所有权令牌和TTL的锁协调多个Worker，释放时校验锁持有者"),
    ("django", "后端接口", "Django View负责权限和参数，Service负责业务流程，Model负责持久化"),
    ("vue", "前端状态", "Vue Composition API用ref和reactive维护状态，并在组件卸载时停止轮询"),
    ("rag", "知识检索", "RAG先召回相关切片，再把有限上下文交给Chat模型生成带引用回答"),
    ("security", "安全控制", "Fernet加密API Key，SSRF校验阻止模型地址访问内网和元数据服务"),
    ("evaluation", "离线评测", "冻结测试集只用于最终评估，开发集用于调参，逐题结果防止指标造假"),
    ("agent", "工具调用", "Agent通过工具注册表、参数白名单和执行审计限制工具权限与副作用"),
    ("database", "数据一致性", "数据库事务保证切片安全替换，失败时保留旧Paragraph和Embedding签名"),
    ("deployment", "生产部署", "Gunicorn处理HTTP请求，Celery处理长任务，PostgreSQL保存最终业务状态"),
]


class Command(BaseCommand):
    help = "创建或清理stage09合成检索评测语料与60/40 JSONL数据集"

    def add_arguments(self, parser):
        action = parser.add_mutually_exclusive_group(required=True)
        action.add_argument("--setup", action="store_true")
        action.add_argument("--cleanup", action="store_true")
        parser.add_argument("--dataset-dir", default="../evals/datasets")

    def handle(self, *args, **options):
        if options["cleanup"]:
            knowledge_deleted, _ = KnowledgeBase.objects.filter(
                owner__username=USERNAME,
                name=KNOWLEDGE_NAME,
            ).delete()
            user_deleted, _ = User.objects.filter(username=USERNAME).delete()
            self.stdout.write(self.style.SUCCESS(
                f"清理完成：knowledge_rows={knowledge_deleted}, user_rows={user_deleted}"
            ))
            return

        if User.objects.filter(username=USERNAME).exists():
            raise CommandError("stage09评测用户已存在，请先执行--cleanup，避免覆盖数据")
        user = User.objects.create_user(USERNAME, password=None)
        knowledge = KnowledgeBase.objects.create(
            owner=user,
            name=KNOWLEDGE_NAME,
            description="stage09自动生成的非敏感合成评测语料",
            retrieval_mode=KnowledgeBase.RetrievalMode.HYBRID,
            fusion_method=KnowledgeBase.FusionMethod.WEIGHTED,
            vector_weight=0.7,
            retrieval_top_k=5,
            vector_candidate_k=30,
            keyword_candidate_k=30,
            rrf_k=60,
            rerank_candidate_k=20,
        )
        records = []
        sequence = 0
        for topic_index, (topic_key, category, base_fact) in enumerate(TOPICS, start=1):
            document = Document(
                knowledge_base=knowledge,
                name=f"stage09-{topic_key}.txt",
                status=Document.Status.SUCCESS,
                paragraph_count=10,
            )
            source = "\n".join(f"{topic_key}-{number}" for number in range(1, 11))
            document.file.save(
                f"stage09-{topic_key}.txt",
                ContentFile(source.encode("utf-8")),
                save=False,
            )
            document.save()
            for position in range(1, 11):
                sequence += 1
                marker = f"S09{sequence:03d}"
                content = (
                    f"{marker}属于{category}主题的第{position}条说明。{base_fact}。"
                    f"本条验证标记为{topic_key}_{position:02d}，与相邻条目的编号和用途不同。"
                )
                paragraph = Paragraph.objects.create(
                    document=document,
                    position=position,
                    content=content,
                    embedding=local_embedding(content),
                )
                question = (
                    f"在{category}主题中，验证标记{topic_key}_{position:02d}对应的机制是什么？"
                    if sequence <= 60
                    else f"请找出{marker}这条资料说明的主要工程作用。"
                )
                records.append(
                    {
                        "id": f"stage09-{sequence:03d}",
                        "question": question,
                        "relevant_targets": [
                            {
                                "document_name": document.name,
                                "position": paragraph.position,
                                "content_sha256": content_sha256(content),
                            }
                        ],
                        "category": category,
                        "difficulty": "medium" if sequence <= 60 else "hard",
                        "synthetic": True,
                        "notes": "固定、非敏感合成语料；不能代表真实用户准确率",
                    }
                )
        dataset_dir = Path(options["dataset_dir"]).resolve()
        dataset_dir.mkdir(parents=True, exist_ok=True)
        for name, items in (("stage09-dev.jsonl", records[:60]), ("stage09-test.jsonl", records[60:])):
            (dataset_dir / name).write_text(
                "\n".join(json.dumps(item, ensure_ascii=False) for item in items) + "\n",
                encoding="utf-8",
            )
        self.stdout.write(self.style.SUCCESS(
            f"创建完成：knowledge_base_id={knowledge.id}, paragraphs=100, dev=60, test=40"
        ))
