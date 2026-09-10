import hashlib
import json

from api.models import Document

from .document_parser import (
    CHUNKER_VERSION,
    PARSER_VERSION,
    ChunkingOptions,
    build_chunking_plan,
    parse_document,
)


CONFIG_FIELDS = (
    "parser_type",
    "chunk_strategy",
    "parent_max_tokens",
    "child_target_tokens",
    "child_overlap_tokens",
    "preserve_tables",
    "preserve_code_blocks",
)


def options_from_document(document: Document, overrides: dict | None = None) -> ChunkingOptions:
    values = {
        "strategy": document.chunk_strategy,
        "parent_max_tokens": document.parent_max_tokens,
        "child_target_tokens": document.child_target_tokens,
        "child_overlap_tokens": document.child_overlap_tokens,
        "preserve_tables": document.preserve_tables,
        "preserve_code_blocks": document.preserve_code_blocks,
    }
    overrides = overrides or {}
    aliases = {"chunk_strategy": "strategy"}
    for name, value in overrides.items():
        target = aliases.get(name, name)
        if target in values:
            values[target] = value
    return ChunkingOptions(**values)


def chunking_config_payload(document: Document) -> dict:
    return {name: getattr(document, name) for name in CONFIG_FIELDS}


def chunking_signature(document: Document, overrides: dict | None = None) -> str:
    payload = chunking_config_payload(document)
    payload.update({key: value for key, value in (overrides or {}).items() if key in CONFIG_FIELDS})
    payload.update({"parser_version": PARSER_VERSION, "chunker_version": CHUNKER_VERSION})
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def prepare_document_chunks(document: Document, overrides: dict | None = None):
    parser_type = (overrides or {}).get("parser_type", document.parser_type)
    parsed = parse_document(document.file.path, document.source_id, parser_type)
    plan = build_chunking_plan(parsed, options_from_document(document, overrides))
    return parsed, plan


def preview_document_chunks(document: Document, overrides: dict | None = None) -> dict:
    parsed, plan = prepare_document_chunks(document, overrides)
    children_by_parent: dict[int, list] = {}
    standalone = []
    for child in plan.children:
        if child.parent_position is None:
            standalone.append(child)
        else:
            children_by_parent.setdefault(child.parent_position, []).append(child)

    def child_payload(child):
        return {
            "position": child.position,
            "heading_path": list(child.heading_path),
            "page_start": child.page_start,
            "page_end": child.page_end,
            "structure_type": child.structure_type,
            "token_count": child.token_count,
            "content": child.content[:4000],
            "source_block_ids": list(child.source_block_ids),
        }

    items = []
    for parent in plan.parents[:20]:
        items.append(
            {
                "position": parent.position,
                "heading_path": list(parent.heading_path),
                "page_start": parent.page_start,
                "page_end": parent.page_end,
                "structure_type": parent.structure_type,
                "token_count": parent.token_count,
                "content": parent.content[:6000],
                "source_block_ids": list(parent.source_block_ids),
                "children": [
                    child_payload(child)
                    for child in children_by_parent.get(parent.position, [])[:50]
                ],
            }
        )
    if standalone:
        items.append(
            {
                "position": None,
                "heading_path": [],
                "page_start": None,
                "page_end": None,
                "structure_type": "TEXT",
                "token_count": sum(child.token_count for child in standalone),
                "content": "",
                "source_block_ids": [],
                "children": [child_payload(child) for child in standalone[:100]],
            }
        )
    return {
        "parser_type": parsed.parser_type,
        "parser_version": PARSER_VERSION,
        "chunker_version": CHUNKER_VERSION,
        "source_sha256": parsed.source_sha256,
        "block_count": len(parsed.blocks),
        "parent_count": len(plan.parents),
        "child_count": len(plan.children),
        "warnings": list(plan.warnings),
        "truncated": len(plan.parents) > 20 or len(plan.children) > 100,
        "items": items,
    }
