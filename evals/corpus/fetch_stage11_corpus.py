"""下载固定提交的Docker官方公开文档，并生成可追溯Manifest。"""

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen


COMMIT = "3d15caeca7608231f930137accb6d933be157b5d"
REPOSITORY = "https://github.com/docker/docs"
RAW_PREFIX = f"https://raw.githubusercontent.com/docker/docs/{COMMIT}/"
PATHS = (
    "content/manuals/compose/how-tos/environment-variables/best-practices.md",
    "content/manuals/compose/how-tos/environment-variables/envvars-precedence.md",
    "content/manuals/compose/how-tos/environment-variables/envvars.md",
    "content/manuals/compose/how-tos/environment-variables/set-environment-variables.md",
    "content/manuals/compose/how-tos/environment-variables/variable-interpolation.md",
    "content/manuals/compose/how-tos/file-watch.md",
    "content/manuals/compose/how-tos/lifecycle.md",
    "content/manuals/compose/how-tos/multiple-compose-files/extends.md",
    "content/manuals/compose/how-tos/multiple-compose-files/include.md",
    "content/manuals/compose/how-tos/multiple-compose-files/merge.md",
    "content/manuals/compose/how-tos/networking.md",
    "content/manuals/compose/how-tos/production.md",
    "content/manuals/compose/how-tos/profiles.md",
    "content/manuals/compose/how-tos/project-name.md",
    "content/manuals/compose/how-tos/startup-order.md",
    "content/manuals/compose/how-tos/use-secrets.md",
    "content/manuals/compose/intro/compose-application-model.md",
    "content/manuals/engine/daemon/live-restore.md",
    "content/manuals/engine/daemon/logs.md",
    "content/manuals/engine/daemon/proxy.md",
    "content/manuals/engine/daemon/start.md",
    "content/manuals/engine/daemon/troubleshoot.md",
    "content/manuals/engine/manage-resources/pruning.md",
    "content/manuals/engine/network/_index.md",
    "content/manuals/engine/network/ca-certs.md",
    "content/manuals/engine/network/drivers/bridge.md",
    "content/manuals/engine/network/drivers/host.md",
    "content/manuals/engine/network/drivers/overlay.md",
    "content/manuals/engine/network/firewall-iptables.md",
    "content/manuals/engine/network/firewall-nftables.md",
    "content/manuals/engine/network/links.md",
    "content/manuals/engine/network/packet-filtering-firewalls.md",
    "content/manuals/engine/network/port-publishing.md",
    "content/manuals/engine/storage/bind-mounts.md",
    "content/manuals/engine/storage/containerd.md",
    "content/manuals/engine/storage/drivers/overlayfs-driver.md",
    "content/manuals/engine/storage/drivers/select-storage-driver.md",
    "content/manuals/engine/storage/image-mounts.md",
    "content/manuals/engine/storage/tmpfs.md",
    "content/manuals/engine/storage/volumes.md",
)


def sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def source_id(path: str) -> str:
    slug = path.removeprefix("content/manuals/").removesuffix(".md")
    return "docker-" + re.sub(r"[^a-z0-9]+", "-", slug.lower()).strip("-")


def title_from_markdown(content: str, fallback: str) -> str:
    front_matter = re.match(r"^---\s*\n(.*?)\n---\s*\n", content, re.DOTALL)
    if front_matter:
        title = re.search(r"^title:\s*[\"']?(.+?)[\"']?\s*$", front_matter.group(1), re.MULTILINE)
        if title:
            return title.group(1).strip()
    heading = re.search(r"^#\s+(.+)$", content, re.MULTILINE)
    return heading.group(1).strip() if heading else fallback


def normalized_markdown(content: str, url: str) -> str:
    content = re.sub(r"^---\s*\n.*?\n---\s*\n", "", content, count=1, flags=re.DOTALL)
    content = re.sub(r"^\s*\{\{[%<].*?[>%]\}\}\s*$", "", content, flags=re.MULTILINE)
    content = re.sub(r"\n{3,}", "\n\n", content).strip()
    return f"<!-- source: {url} -->\n\n{content}\n"


def download(url: str) -> bytes:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.netloc != "raw.githubusercontent.com" or not url.startswith(RAW_PREFIX):
        raise RuntimeError("拒绝访问不在固定Docker文档白名单中的地址")
    request = Request(url, headers={"User-Agent": "Knowledge-Chat-Stage11-Corpus/1.0"})
    with urlopen(request, timeout=30) as response:
        data = response.read(2 * 1024 * 1024)
        content_type = response.headers.get("Content-Type", "")
    if len(data) < 100 or b"<html" in data[:500].lower() or "text/html" in content_type:
        raise RuntimeError(f"下载内容为空或不是Markdown：{url}")
    return data


def main():
    root = Path(__file__).resolve().parent
    raw_root = root / "raw"
    normalized_root = root / "normalized"
    raw_root.mkdir(parents=True, exist_ok=True)
    normalized_root.mkdir(parents=True, exist_ok=True)
    retrieved_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    manifest = []
    for path in PATHS:
        identifier = source_id(path)
        url = RAW_PREFIX + path
        data = download(url)
        content = data.decode("utf-8")
        filename = f"{identifier}.md"
        raw_path = raw_root / filename
        normalized_path = normalized_root / filename
        if raw_path.exists() and sha256(raw_path.read_bytes()) != sha256(data):
            raise RuntimeError(f"固定版本文件发生冲突，拒绝覆盖：{raw_path}")
        raw_path.write_bytes(data)
        normalized_path.write_text(normalized_markdown(content, url), encoding="utf-8")
        manifest.append({
            "source_id": identifier,
            "title": title_from_markdown(content, Path(path).stem),
            "source_url": f"{REPOSITORY}/blob/{COMMIT}/{path}",
            "publisher": "Docker, Inc.",
            "product": "Docker Engine / Docker Compose",
            "product_version": f"docs commit {COMMIT}",
            "content_type": "text/markdown",
            "license": "Apache-2.0",
            "retrieved_at": retrieved_at,
            "sha256": sha256(data),
            "local_path": f"raw/{filename}",
            "normalized_path": f"normalized/{filename}",
            "included": True,
        })
    manifest_path = root / "manifest.jsonl"
    manifest_path.write_text(
        "\n".join(json.dumps(item, ensure_ascii=False, sort_keys=True) for item in manifest) + "\n",
        encoding="utf-8",
    )
    print(f"Stage11 corpus ready: documents={len(manifest)}, manifest={manifest_path}")


if __name__ == "__main__":
    main()
