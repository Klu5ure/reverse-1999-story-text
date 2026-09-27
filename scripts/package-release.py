"""Prepare public PDF download packages from audited local outputs; never uploads."""
import hashlib
import json
import zipfile
import shutil
from urllib.parse import quote
from collections import OrderedDict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'outputs/pdf/library-zh-en-v3'
OUT = ROOT / 'outputs/releases/pdf-zh-CN-en-v3'
PUBLIC = ROOT / 'pdf/双语/简中-英文'

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def main():
    report = json.loads((SOURCE / 'batch-report.json').read_text(encoding='utf-8'))
    assert report['processed'] == report['total'] == len(report['results'])
    assert all(r['status'] == 'validated' for r in report['results'])
    groups = OrderedDict()
    for row in report['results']:
        path = (SOURCE / row['pdf']).resolve()
        assert path.is_relative_to(SOURCE.resolve()) and path.suffix == '.pdf'
        audit = json.loads(path.with_suffix('.json').read_text(encoding='utf-8'))
        assert sha(path) == audit['pdfSha256'], f'Changed PDF: {path.name}'
        serial = len(groups.get(row['category'], [])) + 1
        filename = f'{serial:03}-' + path.name.split('-', 1)[1]
        destination = PUBLIC / row['category'] / filename
        assert destination.resolve().is_relative_to(PUBLIC.resolve())
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists() and sha(destination) != sha(path):
            raise RuntimeError(f'Published copy differs; review before replacing: {destination}')
        if not destination.exists():
            shutil.copy2(path, destination)
        assert sha(destination) == audit['pdfSha256']
        groups.setdefault(row['category'], []).append((row, destination))
    OUT.mkdir(parents=True, exist_ok=True)
    notice = (ROOT / 'docs/来源与使用说明.txt').read_text(encoding='utf-8')
    PUBLIC.joinpath('来源与使用说明.txt').write_text(notice, encoding='utf-8')
    packages = []
    for number, (category, members) in enumerate(groups.items(), 1):
        name = f'reverse1999-pdf-zh-CN-en-{number:02}-{category}-v3.zip'
        catalog = [dict(title=r['title'], category=category, file=p.name, pages=r['pages'], sha256=sha(p)) for r,p in members]
        with zipfile.ZipFile(OUT / name, 'w', compression=zipfile.ZIP_DEFLATED) as z:
            for row, path in members:
                z.write(path, category + '/' + path.name)
            z.writestr('来源与使用说明.txt', notice)
            z.writestr('catalog.json', json.dumps(catalog, ensure_ascii=False, indent=2))
        with zipfile.ZipFile(OUT / name) as z:
            assert z.testzip() is None
            assert len(z.namelist()) == len(members) + 2
            for row,path in members:
                assert hashlib.sha256(z.read(category + '/' + path.name)).hexdigest() == sha(path)
        packages.append(dict(file=name, category=category, books=len(members), bytes=(OUT/name).stat().st_size, sha256=sha(OUT/name)))
    (OUT / 'manifest.json').write_text(json.dumps(dict(format='PDF', languages=['zh-CN','en'], layout='A4; English above Chinese', status='local-prepared-not-published', packages=packages), ensure_ascii=False, indent=2), encoding='utf-8')
    (OUT / 'SHA256SUMS.txt').write_text(''.join(p['sha256']+'  '+p['file']+'\n' for p in packages), encoding='utf-8')
    catalogue = ROOT / 'catalog'; catalogue.mkdir(exist_ok=True)
    lines = ['# 中英 PDF 书目', '', '本地发布包已准备，尚未上传。下列文件名用于辨认下载包，并非已生效的下载链接。', '', '| 分类 | 册数 | ZIP 大小 | 下载包 |', '| --- | ---: | ---: | --- |']
    for p in packages:
        lines.append(f"| {p['category']} | {p['books']} | {p['bytes']/1024/1024:.1f} MiB | `{p['file']}` |")
    lines += ['', '## 分册', '']
    for category, members in groups.items():
        lines += ['### '+category, '']
        lines += [f"- [{r['title']}]({quote('../' + path.relative_to(ROOT).as_posix(), safe='/')})（{r['pages']} 页）" for r,path in members]
        lines += ['']
    (catalogue / 'pdf-zh-CN-en.md').write_text('\n'.join(lines), encoding='utf-8')
    print(json.dumps(dict(packages=len(packages), books=sum(p['books'] for p in packages), bytes=sum(p['bytes'] for p in packages)), ensure_ascii=False))

if __name__ == '__main__':
    main()
