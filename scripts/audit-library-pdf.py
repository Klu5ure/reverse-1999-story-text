"""Cross-check chapter reports against the delivered directory and final PDF files."""
import hashlib
import json
from collections import OrderedDict
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs/pdf/library-zh-en-v3'
ASSETS = ROOT / 'local/reader-snapshot'


def audit():
    report_path = OUT / 'batch-report.json'
    report = json.loads(report_path.read_text(encoding='utf-8'))
    groups = OrderedDict()
    for entry in json.loads((ASSETS / 'directory.json').read_text(encoding='utf-8')):
        groups.setdefault((entry['categoryTitle'], entry['chapterTitle']), []).append(entry)
    assert report['processed'] == report['total'] == len(groups) == len(report['results'])
    pages, scenes, long_pairs = 0, 0, 0
    for result in report['results']:
        if result['status'] != 'validated':
            continue
        path = OUT / result['pdf']
        chapter = json.loads(path.with_suffix('.json').read_text(encoding='utf-8'))
        expected = groups[result['category'], result['title']]
        assert [e['id'] for e in expected] == [s['id'] for s in chapter['scenes']], result['title']
        for scene in chapter['scenes']:
            assert hashlib.sha256((ASSETS / (scene['id'] + '.json')).read_bytes()).hexdigest() == scene['sha256']
        # Correct per-book metadata, including files generated before the batch's
        # metadata fix. This changes no page geometry, text, links or bookmarks.
        with pymupdf.open(path) as doc:
            if doc.metadata['title'] != result['title']:
                doc.set_metadata({**doc.metadata, 'title': result['title']})
                doc.saveIncr()
            assert len(doc) == chapter['pages'] == result['pages']
            toc = doc.get_toc()
            assert toc == chapter['bookmarks']
            links = [l for pg in list(doc)[:toc[0][2] - 1] for l in pg.get_links()]
            targets = list(dict.fromkeys((l['page'], round(l['to'].y, 1)) for l in links))
            assert len(targets) == len(toc)
            assert [t[0] + 1 for t in targets] == [t[2] for t in toc]
            for pg in doc:
                text = pg.get_text()
                assert '\ufffd' not in text
                assert '中英双语对照 · 私人阅读版' not in text
                assert '本文件为非官方、非商业私人阅读版本' not in text
        typ_path = path.with_suffix('.typ')
        typ = typ_path.read_text(encoding='utf-8')
        typ = typ.replace('title: "此即明日 · 中英双语序章"', 'title: ' + json.dumps(result['title'], ensure_ascii=False))
        typ_path.write_text(typ, encoding='utf-8')
        chapter['pdfSha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
        chapter['layoutRevision'] = 'a4-bilingual-slash-toc-v3'
        chapter['catalogueOrderCheck'] = 'Exact chapter entry sequence matches delivered directory'
        path.with_suffix('.json').write_text(json.dumps(chapter, ensure_ascii=False, indent=2), encoding='utf-8')
        pages += result['pages']
        scenes += len(chapter['scenes'])
        long_pairs += len(chapter.get('longPairIds', []))
    passed = sum(r['status'] == 'validated' for r in report['results'])
    report['summary'] = dict(validated=passed, failed=len(groups) - passed, pages=pages,
                             scenes=scenes, longPairs=long_pairs,
                             audit='Directory order, source hashes, final metadata, PDF hashes, TOC links and bookmarks checked',
                             visualReview='Targeted rendered-page review; not a printed proof or exhaustive manual text proofread')
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    (OUT / '来源与使用说明.txt').write_text(
        (ROOT / 'docs/来源与使用说明.txt').read_text(encoding='utf-8'), encoding='utf-8')
    print(json.dumps(report['summary'], ensure_ascii=False))


if __name__ == '__main__':
    audit()
