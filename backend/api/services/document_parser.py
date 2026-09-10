import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from docx import Document as DocxDocument
from docx.oxml.table import CT_Tbl
from docx.oxml.text.paragraph import CT_P
from docx.table import Table
from docx.text.paragraph import Paragraph as DocxParagraph
from pypdf import PdfReader


SUPPORTED_EXTENSIONS = {".txt", ".md", ".pdf", ".docx"}
PARSER_VERSION = "stage11-parser-v1"
CHUNKER_VERSION = "stage11-parent-child-v1"
TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_./:-]+|[\u3400-\u9fff]|[^\s]")


class BlockType:
    HEADING = "HEADING"
    PARAGRAPH = "PARAGRAPH"
    LIST = "LIST"
    TABLE = "TABLE"
    CODE = "CODE"
    QUOTE = "QUOTE"
    PAGE_BREAK = "PAGE_BREAK"


@dataclass(frozen=True)
class DocumentBlock:
    block_id: str
    block_type: str
    text: str
    heading_path: tuple[str, ...]
    page_start: int | None
    page_end: int | None
    source_order: int
    content_sha256: str
    metadata: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ParsedDocument:
    source_sha256: str
    parser_type: str
    blocks: tuple[DocumentBlock, ...]
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class ChunkingOptions:
    strategy: str = "PARENT_CHILD"
    parent_max_tokens: int = 1500
    child_target_tokens: int = 400
    child_overlap_tokens: int = 60
    preserve_tables: bool = True
    preserve_code_blocks: bool = True


@dataclass(frozen=True)
class ParentChunkDraft:
    position: int
    content: str
    heading_path: tuple[str, ...]
    page_start: int | None
    page_end: int | None
    token_count: int
    content_sha256: str
    source_block_ids: tuple[str, ...]
    structure_type: str


@dataclass(frozen=True)
class ChildChunkDraft:
    position: int
    parent_position: int | None
    content: str
    embedding_content: str
    heading_path: tuple[str, ...]
    page_start: int | None
    page_end: int | None
    token_count: int
    content_sha256: str
    source_block_ids: tuple[str, ...]
    structure_type: str


@dataclass(frozen=True)
class ChunkingPlan:
    parents: tuple[ParentChunkDraft, ...]
    children: tuple[ChildChunkDraft, ...]
    warnings: tuple[str, ...] = ()


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def source_file_sha256(file_path: str) -> str:
    digest = hashlib.sha256()
    with Path(file_path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_text(value: str) -> str:
    value = re.sub(r"\r\n?", "\n", value)
    value = re.sub(r"[ \t]+", " ", value)
    return re.sub(r"\n{3,}", "\n\n", value).strip()


def estimate_token_count(value: str) -> int:
    return len(TOKEN_PATTERN.findall(value))


def _make_block_id(
    source_id: str,
    source_sha256: str,
    block_type: str,
    text: str,
    heading_path: tuple[str, ...],
    page_start: int | None,
    source_order: int,
) -> str:
    payload = json.dumps(
        {
            "source_id": source_id,
            "source_sha256": source_sha256,
            "type": block_type,
            "text_sha256": _sha256_text(text),
            "heading_path": heading_path,
            "page": page_start,
            "order": source_order,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return f"blk_{_sha256_text(payload)[:32]}"


def _finalize_blocks(source_id: str, source_sha256: str, raw_blocks) -> tuple[DocumentBlock, ...]:
    blocks = []
    for index, raw in enumerate(raw_blocks, start=1):
        text = normalize_text(raw[1])
        if not text and raw[0] != BlockType.PAGE_BREAK:
            continue
        heading_path = tuple(raw[2] or ())
        page_start = raw[3]
        page_end = raw[4]
        metadata = dict(raw[5] or {})
        blocks.append(
            DocumentBlock(
                block_id=_make_block_id(
                    source_id, source_sha256, raw[0], text, heading_path, page_start, index
                ),
                block_type=raw[0],
                text=text,
                heading_path=heading_path,
                page_start=page_start,
                page_end=page_end,
                source_order=index,
                content_sha256=_sha256_text(text),
                metadata=metadata,
            )
        )
    return tuple(blocks)


def _plain_blocks(text: str):
    return [
        (BlockType.PARAGRAPH, part, (), None, None, {})
        for part in re.split(r"\n\s*\n", normalize_text(text))
        if part.strip()
    ]


def _markdown_blocks(text: str):
    text = re.sub(r"^---\s*\n.*?\n---\s*\n", "", text, count=1, flags=re.DOTALL)
    text = re.sub(r"^\s*\{\{[%<].*?[>%]\}\}\s*$", "", text, flags=re.MULTILINE)
    lines = re.sub(r"\r\n?", "\n", text).splitlines()
    heading_levels: list[str] = []
    raw_blocks = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if not line.strip():
            index += 1
            continue
        heading_match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if heading_match:
            level = len(heading_match.group(1))
            title = heading_match.group(2).strip()
            heading_levels[level - 1 :] = [title]
            raw_blocks.append(
                (BlockType.HEADING, title, tuple(heading_levels), None, None, {"level": level})
            )
            index += 1
            continue
        fence = re.match(r"^\s*(```+|~~~+)\s*([^\s]*)", line)
        if fence:
            marker_char = fence.group(1)[0]
            language = fence.group(2)
            code_lines = []
            index += 1
            while index < len(lines) and not re.match(
                rf"^\s*{re.escape(marker_char)}{{3,}}", lines[index]
            ):
                code_lines.append(lines[index])
                index += 1
            if index < len(lines):
                index += 1
            raw_blocks.append(
                (
                    BlockType.CODE,
                    "\n".join(code_lines).strip("\n"),
                    tuple(heading_levels),
                    None,
                    None,
                    {"language": language},
                )
            )
            continue
        if "|" in line and index + 1 < len(lines) and re.match(
            r"^\s*\|?\s*:?-{3,}", lines[index + 1]
        ):
            table_lines = [line, lines[index + 1]]
            index += 2
            while index < len(lines) and "|" in lines[index] and lines[index].strip():
                table_lines.append(lines[index])
                index += 1
            raw_blocks.append(
                (BlockType.TABLE, "\n".join(table_lines), tuple(heading_levels), None, None, {})
            )
            continue
        if re.match(r"^\s*(?:[-*+] |\d+[.)] )", line):
            items = [line.strip()]
            index += 1
            while index < len(lines) and re.match(r"^\s*(?:[-*+] |\d+[.)] )", lines[index]):
                items.append(lines[index].strip())
                index += 1
            raw_blocks.append(
                (BlockType.LIST, "\n".join(items), tuple(heading_levels), None, None, {})
            )
            continue
        if re.match(r"^\s*>\s?", line):
            quotes = [re.sub(r"^\s*>\s?", "", line)]
            index += 1
            while index < len(lines) and re.match(r"^\s*>\s?", lines[index]):
                quotes.append(re.sub(r"^\s*>\s?", "", lines[index]))
                index += 1
            raw_blocks.append(
                (BlockType.QUOTE, "\n".join(quotes), tuple(heading_levels), None, None, {})
            )
            continue
        paragraph = [line.strip()]
        index += 1
        while index < len(lines) and lines[index].strip():
            candidate = lines[index]
            if re.match(r"^(#{1,6})\s+", candidate) or re.match(
                r"^\s*(```+|~~~+|[-*+] |\d+[.)] |>\s?)", candidate
            ):
                break
            paragraph.append(candidate.strip())
            index += 1
        raw_blocks.append(
            (BlockType.PARAGRAPH, "\n".join(paragraph), tuple(heading_levels), None, None, {})
        )
    return raw_blocks


def _pdf_blocks(file_path: str):
    raw_blocks = []
    reader = PdfReader(file_path)
    for page_number, page in enumerate(reader.pages, start=1):
        page_text = normalize_text(page.extract_text() or "")
        if page_text:
            for part in re.split(r"\n\s*\n", page_text):
                if part.strip():
                    raw_blocks.append(
                        (BlockType.PARAGRAPH, part, (), page_number, page_number, {})
                    )
        if page_number < len(reader.pages):
            raw_blocks.append((BlockType.PAGE_BREAK, "", (), page_number, page_number, {}))
    return raw_blocks


def _docx_table_text(table: Table) -> str:
    rows = [" | ".join(normalize_text(cell.text) for cell in row.cells) for row in table.rows]
    return "\n".join(row for row in rows if row.strip(" |")).strip()


def _docx_blocks(file_path: str):
    document = DocxDocument(file_path)
    heading_levels: list[str] = []
    raw_blocks = []
    for child in document.element.body.iterchildren():
        if isinstance(child, CT_P):
            paragraph = DocxParagraph(child, document)
            text = normalize_text(paragraph.text)
            if not text:
                continue
            style_name = (paragraph.style.name if paragraph.style else "") or ""
            heading_match = re.match(r"(?:Heading|标题)\s*(\d+)", style_name, re.IGNORECASE)
            if heading_match:
                level = max(1, min(int(heading_match.group(1)), 6))
                heading_levels[level - 1 :] = [text]
                raw_blocks.append(
                    (
                        BlockType.HEADING,
                        text,
                        tuple(heading_levels),
                        None,
                        None,
                        {"level": level, "style": style_name},
                    )
                )
                continue
            block_type = (
                BlockType.LIST
                if paragraph._p.pPr is not None and paragraph._p.pPr.numPr is not None
                else BlockType.PARAGRAPH
            )
            raw_blocks.append(
                (block_type, text, tuple(heading_levels), None, None, {"style": style_name})
            )
        elif isinstance(child, CT_Tbl):
            table = Table(child, document)
            text = _docx_table_text(table)
            if text:
                raw_blocks.append(
                    (BlockType.TABLE, text, tuple(heading_levels), None, None, {})
                )
    return raw_blocks


def resolve_parser_type(file_path: str, configured: str = "AUTO") -> str:
    suffix_map = {".txt": "TXT", ".md": "MARKDOWN", ".pdf": "PDF", ".docx": "DOCX"}
    inferred = suffix_map.get(Path(file_path).suffix.lower())
    if not inferred:
        raise ValueError("仅支持 TXT、Markdown、PDF 和 DOCX 文件")
    if configured and configured != "AUTO" and configured != inferred:
        raise ValueError("选择的解析器与文件格式不匹配")
    return inferred


def parse_document(file_path: str, source_id: str, parser_type: str = "AUTO") -> ParsedDocument:
    resolved = resolve_parser_type(file_path, parser_type)
    source_hash = source_file_sha256(file_path)
    warnings = []
    if resolved == "TXT":
        raw_blocks = _plain_blocks(Path(file_path).read_text(encoding="utf-8", errors="ignore"))
    elif resolved == "MARKDOWN":
        raw_blocks = _markdown_blocks(Path(file_path).read_text(encoding="utf-8", errors="ignore"))
    elif resolved == "PDF":
        raw_blocks = _pdf_blocks(file_path)
        warnings.append("PDF表格按可提取文本保留，复杂版式可能丢失结构")
    else:
        raw_blocks = _docx_blocks(file_path)
    blocks = _finalize_blocks(source_id, source_hash, raw_blocks)
    if not any(block.text for block in blocks):
        raise ValueError("文档没有可提取的文字，扫描版 PDF 暂不支持")
    return ParsedDocument(source_hash, resolved, blocks, tuple(warnings))


def _structure_type(blocks: list[DocumentBlock]) -> str:
    values = {BlockType.TABLE: "TABLE", BlockType.CODE: "CODE", BlockType.LIST: "LIST"}
    found = {values.get(block.block_type, "TEXT") for block in blocks if block.text}
    return next(iter(found)) if len(found) == 1 else "MIXED"


def _page_range(blocks: list[DocumentBlock]) -> tuple[int | None, int | None]:
    pages = [page for block in blocks for page in (block.page_start, block.page_end) if page]
    return (min(pages), max(pages)) if pages else (None, None)


def _render_block(block: DocumentBlock) -> str:
    if block.block_type == BlockType.HEADING:
        return f"# {block.text}"
    if block.block_type == BlockType.CODE:
        language = block.metadata.get("language", "")
        return f"```{language}\n{block.text}\n```"
    return block.text


def _split_by_tokens(text: str, target: int, overlap: int) -> list[str]:
    matches = list(TOKEN_PATTERN.finditer(text))
    if len(matches) <= target:
        return [text.strip()] if text.strip() else []
    parts = []
    token_start = 0
    while token_start < len(matches):
        token_end = min(len(matches), token_start + target)
        part = text[matches[token_start].start() : matches[token_end - 1].end()].strip()
        if part:
            parts.append(part)
        if token_end >= len(matches):
            break
        token_start = max(token_start + 1, token_end - overlap)
    return parts


def _split_parent_groups(blocks: tuple[DocumentBlock, ...], max_tokens: int):
    groups: list[list[DocumentBlock]] = []
    current: list[DocumentBlock] = []
    current_tokens = 0
    for block in blocks:
        if block.block_type == BlockType.PAGE_BREAK:
            continue
        rendered = _render_block(block)
        block_tokens = estimate_token_count(rendered)
        starts_new_section = block.block_type == BlockType.HEADING and any(
            item.block_type != BlockType.HEADING for item in current
        )
        if current and (starts_new_section or current_tokens + block_tokens > max_tokens):
            groups.append(current)
            current = []
            current_tokens = 0
        current.append(block)
        current_tokens += block_tokens
    if current:
        groups.append(current)
    return groups


def _child_groups(parent_blocks: list[DocumentBlock], options: ChunkingOptions, warnings: list[str]):
    groups: list[tuple[str, list[DocumentBlock]]] = []
    buffered: list[DocumentBlock] = []
    buffered_tokens = 0

    def flush():
        nonlocal buffered, buffered_tokens
        if buffered:
            groups.append(("\n\n".join(_render_block(item) for item in buffered), buffered))
            buffered = []
            buffered_tokens = 0

    for block in parent_blocks:
        if block.block_type == BlockType.HEADING:
            continue
        rendered = _render_block(block)
        count = estimate_token_count(rendered)
        atomic = (block.block_type == BlockType.TABLE and options.preserve_tables) or (
            block.block_type == BlockType.CODE and options.preserve_code_blocks
        )
        if atomic:
            flush()
            if count <= options.child_target_tokens:
                groups.append((rendered, [block]))
            else:
                warnings.append(f"{block.block_type}内容超过Child上限，已安全拆分")
                for part in _split_by_tokens(
                    rendered, options.child_target_tokens, options.child_overlap_tokens
                ):
                    groups.append((part, [block]))
            continue
        if buffered and buffered_tokens + count > options.child_target_tokens:
            previous_text = "\n\n".join(_render_block(item) for item in buffered)
            previous_blocks = list(buffered)
            flush()
            if options.child_overlap_tokens and previous_text:
                matches = list(TOKEN_PATTERN.finditer(previous_text))
                if matches:
                    start = matches[max(0, len(matches) - options.child_overlap_tokens)].start()
                    overlap_text = previous_text[start:].strip()
                    if overlap_text:
                        tail = previous_blocks[-1]
                        buffered = [
                            DocumentBlock(
                                block_id=tail.block_id,
                                block_type=tail.block_type,
                                text=overlap_text,
                                heading_path=tail.heading_path,
                                page_start=tail.page_start,
                                page_end=tail.page_end,
                                source_order=tail.source_order,
                                content_sha256=_sha256_text(overlap_text),
                                metadata=tail.metadata,
                            )
                        ]
                        buffered_tokens = estimate_token_count(overlap_text)
        if count > options.child_target_tokens:
            flush()
            for part in _split_by_tokens(
                rendered, options.child_target_tokens, options.child_overlap_tokens
            ):
                groups.append((part, [block]))
        else:
            buffered.append(block)
            buffered_tokens += count
    flush()
    return groups


def build_chunking_plan(parsed: ParsedDocument, options: ChunkingOptions) -> ChunkingPlan:
    if options.strategy == "LEGACY":
        text = "\n\n".join(_render_block(block) for block in parsed.blocks if block.text)
        children = tuple(
            ChildChunkDraft(
                position=index,
                parent_position=None,
                content=content,
                embedding_content=content,
                heading_path=(),
                page_start=None,
                page_end=None,
                token_count=estimate_token_count(content),
                content_sha256=_sha256_text(content),
                source_block_ids=tuple(block.block_id for block in parsed.blocks if block.text),
                structure_type="TEXT",
            )
            for index, content in enumerate(split_text(text), start=1)
        )
        return ChunkingPlan((), children, parsed.warnings)

    warnings = list(parsed.warnings)
    parents: list[ParentChunkDraft] = []
    children: list[ChildChunkDraft] = []
    child_position = 1
    for parent_position, blocks in enumerate(
        _split_parent_groups(parsed.blocks, options.parent_max_tokens), start=1
    ):
        content = "\n\n".join(_render_block(block) for block in blocks if block.text).strip()
        if not content:
            continue
        page_start, page_end = _page_range(blocks)
        heading_path = next((block.heading_path for block in reversed(blocks) if block.heading_path), ())
        parents.append(
            ParentChunkDraft(
                position=parent_position,
                content=content,
                heading_path=heading_path,
                page_start=page_start,
                page_end=page_end,
                token_count=estimate_token_count(content),
                content_sha256=_sha256_text(content),
                source_block_ids=tuple(dict.fromkeys(block.block_id for block in blocks if block.text)),
                structure_type=_structure_type(blocks),
            )
        )
        for child_content, child_blocks in _child_groups(blocks, options, warnings):
            child_content = child_content.strip()
            if not child_content:
                continue
            child_heading = next(
                (block.heading_path for block in reversed(child_blocks) if block.heading_path),
                heading_path,
            )
            child_page_start, child_page_end = _page_range(child_blocks)
            heading_prefix = " > ".join(child_heading)
            embedding_content = (
                f"标题路径：{heading_prefix}\n{child_content}" if heading_prefix else child_content
            )
            children.append(
                ChildChunkDraft(
                    position=child_position,
                    parent_position=parent_position,
                    content=child_content,
                    embedding_content=embedding_content,
                    heading_path=child_heading,
                    page_start=child_page_start,
                    page_end=child_page_end,
                    token_count=estimate_token_count(child_content),
                    content_sha256=_sha256_text(child_content),
                    source_block_ids=tuple(
                        dict.fromkeys(block.block_id for block in child_blocks if block.text)
                    ),
                    structure_type=_structure_type(child_blocks),
                )
            )
            child_position += 1
    if not children:
        raise ValueError("文档没有可生成的有效切片")
    return ChunkingPlan(tuple(parents), tuple(children), tuple(dict.fromkeys(warnings)))


def extract_text(file_path: str) -> str:
    """兼容旧调用；新处理链应使用parse_document。"""
    parsed = parse_document(file_path, "compat")
    return "\n\n".join(_render_block(block) for block in parsed.blocks if block.text)


def split_text(text: str, chunk_size: int = 800, overlap: int = 100) -> list[str]:
    if len(text) <= chunk_size:
        return [text]
    chunks = []
    start = 0
    while start < len(text):
        end = min(start + chunk_size, len(text))
        if end < len(text):
            candidates = [
                text.rfind(mark, start + chunk_size // 2, end)
                for mark in ("\n", "。", "！", "？")
            ]
            boundary = max(candidates)
            if boundary > start:
                end = boundary + 1
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return chunks
