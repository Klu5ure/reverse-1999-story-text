# 构建与发布目录

需要 Python、`pip install -r requirements-pdf.txt`、Typst，以及已安装的 Constantia 和 Noto Serif SC 字体。Typst 可放 PATH，或通过 `TYPST_BIN` 指定；本机迁移副本位于 `local/bin/typst.exe`。字体授权不随仓库转移。

输入为 `local/reader-snapshot/directory.json` 及目录引用的同名场景 JSON，必须来自明确的交付快照。该目录默认不上传；新电脑仅克隆仓库不足以重新生成 PDF，需要另行恢复获准保存的输入快照及依赖。当前本机迁移记录在 `local/migration.json`。

```sh
python -m unittest discover -s scripts -p test_prologue_pdf.py
python scripts/build-library-pdf.py
python scripts/audit-library-pdf.py
```

`--retry` 只重试同批次失败项；改变排版或输入后应完整重建。样章可导入 `build-prologue-pdf.py` 的 `build(entries, title, output, render)` 指定独立输出；不要把临时样章覆盖到已确认版本。

## 目录约定

```text
README.md                 面向读者的说明与下载导航
pdf/双语/简中-英文/       最终 PDF 原件，按剧情分类，可纳入 Git
catalog/                  自动生成的书目、分册链接与下载包说明
scripts/                  生成、校验、打包工具
requirements-pdf.txt      Python 依赖
docs/BUILD.md             构建与发布规则
AGENTS.md                 协作规则
DEVELOPMENT.md            当前进度（维护者）
local/                    本地输入快照、工具；Git 忽略
outputs/pdf/              经校验的 PDF；Git 忽略
outputs/txt/              后续 TXT 输出；Git 忽略
outputs/epub/             后续 EPUB 输出；Git 忽略
outputs/releases/         准备上传 Releases 的包；Git 忽略
```

TXT、EPUB 路径是规划，当前没有相应成品。旧语言目录及配套 structured/ 已移至 `local/archive/legacy-markdown/`，仅作本地历史参考，不上传；不要把旧统计用于新成品。

成品按“格式 → 语言或语言组合 → 剧情分类 → 分册”组织。语言代码采用 zh-CN、zh-TW、en、ja、ko，组合以连字符连接；排列规则与实际正文先后顺序分别记录，不由文件名猜测。

经确认的最终 PDF 放入 pdf/，进入 Git 版本管理；试排稿不提交。正式发布时另提供 GitHub Releases ZIP 附件。每种格式/组合按剧情分类打包；ZIP 内只含成品、书目及使用说明。包外提供 manifest.json 和 SHA256SUMS.txt，不上传含本地路径的构建报告、场景 JSON、Typst 源文件、渲染预览、字体或编译器。GitHub 自动生成的 Source code ZIP 包含整个仓库（含 PDF 和源码）；只需剧情的读者可下载后续提供的分类 ZIP。

```sh
python scripts/package-release.py
```

该命令将已审计且哈希一致的 PDF 复制至 pdf/双语/简中-英文/，从这些正式原件打包，逐文件核验 ZIP 并生成带分册链接的 catalog/pdf-zh-CN-en.md。已有正式原件如与构建结果不同会停止，需先审阅版本变化再替换。它不会上传。发布前核对素材来源与适用许可、公开附件范围、版本号和说明；发布后才把 README 与书目中的“未发布”改成真实下载链接。私有输入不进入公开提交。

正式分册文件名采用“分类内三位序号-书名.pdf”，每类从 001 开始，保持输入目录中的相对顺序；编号是文件排序号，不是游戏关卡 ID。构建暂存的全库编号可保留，打包脚本负责映射正式文件名。
