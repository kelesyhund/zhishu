import json
import re
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from api.services.document_parser import BlockType, parse_document
from api.services.retrieval_evaluation import EvaluationDataError
from api.services.stage11_evaluation import load_corpus_manifest, load_stage11_cases


NO_ANSWER_QUESTIONS = (
    "Docker Compose如何配置Oracle RAC节点自动扩容？",
    "Docker Engine怎样计算AWS EC2当月账单？",
    "如何用Docker命令重置PostgreSQL超级用户密码？",
    "Docker Compose如何创建Kubernetes自定义调度器？",
    "Docker Engine怎样修改Linux内核源代码并重新编译？",
    "Docker文档是否给出了Azure订阅退款流程？",
    "如何通过Docker Compose申请苹果开发者证书？",
    "Docker Engine如何配置Cisco交换机的BGP邻居？",
    "Docker Compose怎样恢复被删除的GitHub仓库？",
    "Docker官方文档如何诊断Oracle RMAN备份失败？",
    "Docker Engine能否自动修复损坏的Windows注册表？",
    "Docker Compose如何设置支付宝商户结算账户？",
    "Docker文档中怎样训练一个图像分类神经网络？",
    "Docker Engine如何为MySQL生成业务数据字典？",
    "Docker Compose能否查询域名ICP备案进度？",
    "Docker文档是否说明SAP许可证续费价格？",
    "Docker Engine如何替用户选择股票投资组合？",
    "Docker Compose怎样配置Hadoop YARN容量调度队列？",
    "Docker官方文档如何办理公司税务登记？",
    "Docker Engine能否生成Android应用签名证书？",
)


def concise(value: str, limit: int = 180) -> str:
    return re.sub(r"\s+", " ", value).strip()[:limit]


def evidence(entry: dict, block, relevance: int = 2) -> dict:
    return {
        "source_id": entry["source_id"],
        "document_sha256": entry["sha256"],
        "source_block_ids": [block.block_id],
        "relevance": relevance,
    }


class Command(BaseCommand):
    help = "从40份真实Docker文档生成待用户集中审核的Stage11候选题（始终AUTO_DRAFT）"

    def add_arguments(self, parser):
        parser.add_argument("--manifest", required=True)
        parser.add_argument("--output", required=True)

    def handle(self, *args, **options):
        manifest_path = Path(options["manifest"]).resolve()
        output = Path(options["output"]).resolve()
        if output.exists():
            raise CommandError("候选集已存在；拒绝静默覆盖，请使用新版本文件名")
        try:
            manifest = load_corpus_manifest(manifest_path)
        except EvaluationDataError as exc:
            raise CommandError(str(exc)) from exc
        sources = []
        for entry in manifest.values():
            if not entry.get("included", True):
                continue
            path = (manifest_path.parent / entry["local_path"]).resolve()
            parsed = parse_document(str(path), entry["source_id"], "MARKDOWN")
            blocks = [block for block in parsed.blocks if block.text and block.block_type != BlockType.HEADING]
            if not blocks:
                raise CommandError(f"{entry['source_id']}没有可用于出题的Block")
            sources.append((entry, parsed, blocks))
        if not 30 <= len(sources) <= 50:
            raise CommandError(f"语料数量必须在30到50之间，当前为{len(sources)}")

        rows = []
        category_counts = {}

        def add(query, category, gold, key_points):
            index = len(rows) + 1
            category_index = category_counts.get(category, 0)
            category_counts[category] = category_index + 1
            # 各类别约30%进入Dev，使Dev/Test都覆盖全部类别。
            dev_limit = {
                "exact_keyword": 9, "semantic_paraphrase": 9, "procedure": 9,
                "table_parameter": 6, "version_difference": 3,
                "multi_document": 9, "no_answer": 5,
            }[category]
            rows.append({
                "case_id": f"docker-stage11-{index:03d}",
                "query": query,
                "category": category,
                "split": "dev" if category_index < dev_limit else "test",
                "answerable": category != "no_answer",
                "gold_evidence": gold,
                "answer_key_points": key_points,
                "review_status": "AUTO_DRAFT",
                "reviewed_at": "",
                "notes": "由程序根据真实公开文档生成的候选题；问题、证据、相关度和关键点均需用户逐题审核。",
            })

        for entry, _, blocks in sources[:30]:
            block = next((item for item in blocks if "`" in item.text), blocks[0])
            terms = re.findall(r"`([^`\n]{2,80})`", block.text)
            term = concise(terms[0] if terms else (block.heading_path[-1] if block.heading_path else entry["title"]), 60)
            add(
                f"在《{entry['title']}》中，{term}的作用、配置要求或限制是什么？",
                "exact_keyword", [evidence(entry, block)], [concise(block.text)],
            )
        for entry, _, blocks in sources[:30]:
            block = blocks[min(1, len(blocks) - 1)]
            topic = block.heading_path[-1] if block.heading_path else entry["title"]
            add(
                f"如果用户遇到与“{topic}”有关的问题，《{entry['title']}》建议怎样理解和处理？",
                "semantic_paraphrase", [evidence(entry, block)], [concise(block.text)],
            )
        for entry, _, blocks in sources[:30]:
            block = next((item for item in blocks if item.block_type in {BlockType.LIST, BlockType.CODE}), blocks[-1])
            topic = block.heading_path[-1] if block.heading_path else entry["title"]
            add(
                f"按照《{entry['title']}》的“{topic}”部分，相关操作步骤和注意事项有哪些？",
                "procedure", [evidence(entry, block)], [concise(block.text)],
            )
        structured = sorted(
            sources,
            key=lambda item: not any(block.block_type == BlockType.TABLE for block in item[2]),
        )[:20]
        for entry, _, blocks in structured:
            block = next((item for item in blocks if item.block_type in {BlockType.TABLE, BlockType.CODE}), blocks[0])
            topic = block.heading_path[-1] if block.heading_path else entry["title"]
            add(
                f"《{entry['title']}》在“{topic}”中列出的参数、取值或命令分别表示什么？",
                "table_parameter", [evidence(entry, block)], [concise(block.text)],
            )
        version_sources = [
            item for item in sources
            if any(re.search(r"\b(version|v\d+|deprecated|legacy)\b", block.text, re.IGNORECASE) for block in item[2])
        ][:10]
        for entry, _, blocks in version_sources:
            block = next(
                item for item in blocks
                if re.search(r"\b(version|v\d+|deprecated|legacy)\b", item.text, re.IGNORECASE)
            )
            add(
                f"《{entry['title']}》提到了哪些版本条件、兼容性差异或弃用限制？",
                "version_difference", [evidence(entry, block)], [concise(block.text)],
            )
        for index in range(30):
            left_entry, _, left_blocks = sources[index % len(sources)]
            right_entry, _, right_blocks = sources[(index + 11) % len(sources)]
            left_block, right_block = left_blocks[0], right_blocks[-1]
            add(
                f"结合《{left_entry['title']}》与《{right_entry['title']}》，部署或排障时应同时考虑哪两方面？",
                "multi_document",
                [evidence(left_entry, left_block), evidence(right_entry, right_block)],
                [concise(left_block.text), concise(right_block.text)],
            )
        for question in NO_ANSWER_QUESTIONS:
            add(question, "no_answer", [], [])

        if len(rows) < 150 or sum(row["split"] == "test" for row in rows) < 100:
            raise CommandError("候选题数量或Test候选数量不足，拒绝输出")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            "\n".join(json.dumps(row, ensure_ascii=False, sort_keys=True) for row in rows) + "\n",
            encoding="utf-8",
        )
        try:
            load_stage11_cases(output, manifest_path=manifest_path)
        except EvaluationDataError as exc:
            output.unlink(missing_ok=True)
            raise CommandError(f"候选集自校验失败，未保留输出：{exc}") from exc
        split_counts = {
            split: sum(row["split"] == split for row in rows) for split in ("dev", "test")
        }
        self.stdout.write(self.style.SUCCESS(
            f"候选集完成：total={len(rows)}, split={split_counts}, categories={category_counts}。全部仍为AUTO_DRAFT。"
        ))
