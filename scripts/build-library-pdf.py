"""Batch the approved A4 layout; isolate failures without silently dropping chapters."""
import importlib.util
import json
import re
import traceback
from collections import OrderedDict
from pathlib import Path
import time
import sys
import html

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('book', ROOT / 'scripts/build-prologue-pdf.py')
book = importlib.util.module_from_spec(spec)
spec.loader.exec_module(book)
entries = json.loads((book.ASSETS / 'directory.json').read_text(encoding='utf-8'))
groups = OrderedDict()
for entry in entries:
    groups.setdefault((entry['categoryTitle'], entry['chapterTitle']), []).append(entry)
out = ROOT / 'outputs/pdf/library-zh-en-v3'
out.mkdir(parents=True, exist_ok=True)
previous = {}
if '--retry' in sys.argv and (out / 'batch-report.json').exists():
    previous = {(r['category'], r['title']): r for r in json.loads((out / 'batch-report.json').read_text(encoding='utf-8'))['results']}
results = []
for index, ((category, title), members) in enumerate(groups.items(), 1):
    if previous.get((category, title), {}).get('status') == 'validated':
        results.append(previous[(category, title)])
        continue
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', title).strip(' .')[:75]
    stem = out / category / f'{index:03}-{name}'
    result = dict(category=category, title=title, scenes=len(members), pdf=str(stem.with_suffix('.pdf').relative_to(out)))
    try:
        report = book.build(entries=members, title=title, output=stem, render=False)
        result.update(status='validated', pages=report['pages'], events=report['events'], longPairs=len(report.get('longPairIds', [])))
    except Exception as exc:
        result.update(status='failed', error=f'{type(exc).__name__}: {exc}')
        stem.parent.mkdir(parents=True, exist_ok=True)
        stem.with_suffix('.error.txt').write_text(traceback.format_exc(), encoding='utf-8')
    results.append(result)
    (out / 'batch-report.json').write_text(json.dumps(dict(date='2026-09-27', environment='Windows; delivered reader assets; no raw resources required', total=len(groups), processed=len(results), results=results), ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'{index}/{len(groups)} {result["status"]} {category} / {title} {result.get("error", "")}', flush=True)


(out / 'batch-report.json').write_text(json.dumps(dict(date='2026-09-27', environment='Windows; delivered reader assets; no raw resources required', total=len(groups), processed=len(results), results=results), ensure_ascii=False, indent=2), encoding='utf-8')
valid = [r for r in results if r['status'] == 'validated']
failed = [r for r in results if r['status'] == 'failed']
index = '<!doctype html><meta charset="utf-8"><title>中英对照 PDF 批量预览</title><style>body{max-width:900px;margin:40px auto;font:18px/1.7 system-ui}li{margin:8px 0}</style><h1>中英对照 PDF · 双语目录修订版</h1>'
index += f'<p>自动校验通过 {len(valid)} / {len(results)} 章。批量版尚未逐页人工验收，不等于正式出版定稿。异常章节单列，不提供为完成品。</p><ul>'
for r in valid:
    index += '<li>' + html.escape(r['category']) + ' / <a href="' + html.escape(r['pdf'].replace('\\', '/'), quote=True) + '">' + html.escape(r['title']) + '</a> · ' + str(r['pages']) + ' 页</li>'
index += '</ul>'
if failed:
    index += '<h2>待处理</h2><ul>'
for r in failed:
    index += '<li>' + html.escape(r['category'] + ' / ' + r['title'] + ': ' + r['error']) + '</li>'
if failed:
    index += '</ul>'
index += '<p>来源与使用说明：本合集由雨幕档案已交付阅读器快照生成，非官方、非商业。剧情文本、角色名称及相关权利归原权利人所有；私人保存不代表获得公开分发许可。正文中的缺文与分支提示为阅读辅助。长篇文献按英文全文、中文全文续排，未强行逐段配对。</p>'
(out / 'index.html').write_text(index, encoding='utf-8')
print(f'DONE validated={len(valid)} failed={len(failed)}', flush=True)
