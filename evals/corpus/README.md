# Stage11真实语料

本目录的业务范围固定为“Docker Engine与Docker Compose部署及故障排查”。语料来源是
Docker官方文档仓库的固定提交，官方仓库声明其文档采用Apache-2.0许可证。

- `manifest.jsonl`：可提交的来源、版本、许可、哈希和本地路径清单。
- `raw/`：精确下载的原始Markdown，按许可策略不提交Git。
- `normalized/`：保留来源注释的规范化副本，不提交Git。
- `fetch_stage11_corpus.py`：只访问代码内固定的Docker官方GitHub地址，不执行文档内容。

获取命令：

```powershell
py -3.10 evals/corpus/fetch_stage11_corpus.py
```

脚本拒绝空响应、HTML错误页和非白名单URL；同一固定提交的文件哈希发生变化时会中止，
不会静默把变化后的内容当作旧版本。
