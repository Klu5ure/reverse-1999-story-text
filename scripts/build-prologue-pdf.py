"""Build the delivered prologue as a private, bilingual A4 reading book.

Uses the current directory and graph edges, not source array order. Short text
pairs are indivisible; no sentence-specific pagination rules are applied.
"""
import hashlib
import importlib.util
import json
import re
import subprocess
import os
import shutil
from types import SimpleNamespace
from collections import Counter, OrderedDict, deque
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / 'local/reader-snapshot'
OUT = ROOT / 'outputs/pdf/prologue'


def load_helper():
    return SimpleNamespace(q=lambda value: json.dumps(value, ensure_ascii=False))


def typst_executable():
    candidate = os.environ.get('TYPST_BIN') or shutil.which('typst')
    if candidate:
        return candidate
    local = ROOT / 'local/bin/typst.exe'
    if local.exists():
        return str(local)
    raise RuntimeError('Install Typst on PATH or set TYPST_BIN to its executable')


def walk(scene):
    """Export every verified branch before its nearest shared continuation."""
    nodes = {n['id']: n for n in scene['nodes']}
    events = {e['id']: e for e in scene['lines']}
    terminal = '__end__'

    def targets(n):
        choices = [events[eid] for eid in n['events'] if events[eid]['type'] == 'choice']
        if choices:
            assert len(choices) == 1
            options = choices[0]['options']
            # Verified topic returns are navigation, not another copy of the story.
            result = [(o.get('runtime') or {}).get('target', o.get('target')) for o in options
                      if (o.get('runtime') or {}).get('type') != 'continue']
            assert all(t in nodes for t in result), 'Unverified choice target'
            edge_targets = [edge['target'] for edge in n['edges']]
            if scene.get('trailOf'):
                # Trail builders retain the menu's shared exit as an extra edge.
                assert set(result) <= set(edge_targets), 'Trail choice lacks graph edge'
                # Extra edges close source menu frames; reader choices follow option targets.
            else:
                ordinary = {o['target'] for o in options if not o.get('runtime')}
                assert ordinary == set(edge_targets), 'Choice/edge mismatch'
            return result
        assert len(n['edges']) <= 1, 'Unverified multi-edge branch'
        return [e['target'] for e in n['edges']]

    pd = {terminal: {terminal}}
    active = set()

    def postdom(n):
        if n in pd:
            return pd[n]
        assert n not in active, 'Cyclic graph needs a separate reading policy'
        active.add(n)
        ts = targets(nodes[n]) or [terminal]
        pd[n] = {n} | set.intersection(*(postdom(t) for t in ts))
        active.remove(n)
        return pd[n]

    postdom(scene['entryNodeId'])

    def distances(start):
        result, queue = {start: 0}, deque([start])
        while queue:
            n = queue.popleft()
            for t in targets(nodes[n]):
                if t not in result:
                    result[t] = result[n] + 1
                    queue.append(t)
        return result

    emitted, items = set(), []

    def chain(start, stop=None, prefix=''):
        cursor = start
        while cursor is not None and cursor != stop:
            if cursor in emitted and all(events[k]['type'] == 'choice' for k in nodes[cursor]['events']) and any(
                    (o.get('runtime') or {}).get('type') == 'continue'
                    for k in nodes[cursor]['events'] for o in events[k].get('options', [])):
                items.append(('reference', cursor))
                return
            if cursor in emitted and not nodes[cursor]['events']:
                # Some trail menu closures converge before the final common card.
                return
            assert cursor not in emitted, f'Unexpected repeated node: {cursor}'
            emitted.add(cursor)
            node = nodes[cursor]
            items.extend(('event', events[eid]) for eid in node['events'])
            ts = targets(node)
            if len(ts) > 1:
                common = set.intersection(*(pd[t] for t in ts)) - {terminal}
                ds = [distances(t) for t in ts]
                join = min(common, key=lambda n: sum(d[n] for d in ds)) if common else None
                for number, target in enumerate(ts, 1):
                    if target == join:
                        continue
                    label = f'{prefix}.{number}' if prefix else str(number)
                    items.append(('branch', label))
                    chain(target, join, label)
                if join is not None:
                    items.append(('join', prefix))
                cursor = join
            else:
                cursor = ts[0] if ts else None

    chain(scene['entryNodeId'])
    event_ids = [payload['id'] for kind, payload in items if kind == 'event']
    assert len(event_ids) == len(set(event_ids)), 'Duplicate event in merged branches'
    available = {e['id'] for e in scene['lines'] if e['type'] != 'archived'}
    assert set(event_ids) == available, f'Unwalked delivery events: {available - set(event_ids)}'
    return items


def clean(value):
    # Game text sometimes uses NBSP for every word: allow normal book line wraps.
    value = value.replace('\u00a0', ' ').replace('\u202f', ' ')
    # This game constructs the radical overbar with positioned combining glyphs.
    # Keep the mathematical meaning as the standard Unicode square-root notation.
    value = re.sub(r'√<voffset=-3><space=9>̅<space=-13>2', '√2', value)
    assert not re.search(r'<(?:voffset|space|font|material)\s*=', value, flags=re.I), 'Special layout markup requires an explicit PDF adapter'
    value = re.sub(r'<br\s*/?>', '\n', value, flags=re.I)
    value = re.sub(r'</?(?:nobr|brace|glitch|notShowInLog)\s*>', '', value, flags=re.I)
    value = re.sub(r'<(?:size|color|align|speed)\s*=[^<>]*>|</(?:size|color|align|speed)\s*>', '', value, flags=re.I)
    value = re.sub(r'<sprite\s*=[^<>]*>', '', value, flags=re.I)
    value = re.sub(r'<marktop\s*=[^<>]*>(.*?)</marktop\s*>', r'\1', value, flags=re.I | re.S)
    return value.replace('{p}', '\n\n').replace('{img}', '').strip()


def plain(value):
    return re.sub(r'</?(?:b|i|s|u|sup|sub)>', '', clean(value), flags=re.I)


def rich_text(value):
    """Treat unknown angle-bracket prose as literal text, like the reader."""
    result, stack = [], []
    for part in re.split(r'(</?(?:b|i|s|u|sup|sub)>)', value, flags=re.I):
        tag = part.lower()
        if re.fullmatch(r'<(?:b|i|s|u|sup|sub)>', tag):
            name = tag[1:-1]
            fn = {'b': 'strong', 'i': 'emph', 's': 'strike', 'u': 'underline', 'sup': 'super', 'sub': 'sub'}[name]
            result.append('#' + fn + '[')
            stack.append(name)
        elif tag.startswith('</') and re.fullmatch(r'</(?:b|i|s|u|sup|sub)>', tag):
            if stack and stack[-1] == tag[2:-1]:
                result.append(']')
                stack.pop()
        elif part:
            result.append('#text(' + json.dumps(part, ensure_ascii=False) + ')')
    return ''.join(result) + ']' * len(stack)


THEME = r'''
#set document(title: "此即明日 · 中英双语序章", author: "雨幕档案 / Rain Archive")
#set text(font: "Noto Serif SC", size: 10.5pt, fill: black, lang: "zh", region: "cn")
#set par(justify: false, leading: 0.65em, spacing: 0pt)
#set page(paper: "a4", margin: (left: 22mm, right: 22mm, top: 21mm, bottom: 22mm),
  header: none, footer: context align(center)[#text(font: "Constantia", size: 9pt)[#counter(page).display("1")]])
#set heading(numbering: none)
#show heading.where(level: 1): it => block(above: 0pt, below: 2.5mm, sticky: true)[#text(size: 20pt, weight: "regular")[#it.body]]
#let section(zh, en, tag) = {
  heading(level: 1, supplement: en)[#zh]
  block(above: 0pt, below: 5.5mm, sticky: true)[#text(font: "Constantia", size: 14pt)[#en] #h(3mm) #text(size: 8.5pt)[#tag]]
}
#let pair(zw, z, ew, e, kind, indent: 0mm, long: false) = block(breakable: long, above: 0pt, below: 4mm,
  inset: (left: indent + if kind == "narration" { 4mm } else { 0mm }, right: if kind == "narration" { 4mm } else { 0mm }))[

  #if kind == "choice" { text(size: 8.5pt)[选项 / Choices]; v(2mm, weak: false) }
  #if long { text(size: 9pt)[英文 / ENGLISH]; v(2mm) }
  #block(above: 0pt, below: 0pt)[
    #set text(font: "Constantia", size: 11pt, lang: "en", hyphenate: false)
    #if ew != "" { text(size: 9pt, ew + ":"); h(0.35em) }
    #e
  ]
  #v(2.5mm, weak: false)
  #if long { text(size: 9pt)[中文 / CHINESE]; v(2mm) }
  #block(above: 0pt, below: 0pt)[
    #if zw != "" { text(size: 9pt, zw + "："); h(0.1em) }
    #z
  ]
]
#let path-label(value, indent: 0mm) = block(above: 3mm, below: 3mm, sticky: true, inset: (left: indent))[
  #text(size: 9pt, weight: "semibold")[#value]]
#text(size: 25pt, weight: "regular")[此即明日]
#v(4mm, weak: false)
#text(font: "Constantia", size: 17pt)[This Is Tomorrow]
#v(5mm, weak: false)
#text(size: 10pt)[《重返未来：1999》序章 · 中英双语对照]
#v(10mm, weak: false)
#show outline.entry: it => block(above: 0pt, below: 2mm)[#it]
#outline(title: [#text(size: 16pt)[目录 / Contents]], depth: 1, indent: 0pt)
#v(10mm, weak: false)
#text(size: 9pt)[阅读说明]
#v(3mm, weak: false)
#text(size: 8.5pt)[英文在上，中文在下。正文按剧情节点接续排列；场景分段在同一小节内连续阅读。选项的各条分支分别展开，汇合后的共同内容仅出现一次。场景字幕保留各语言原文，不保证逐字对应。]

#block(above: 10mm, below: 3mm, sticky: true)[#text(size: 10pt)[来源与版本说明]]
#text(size: 8pt)[本文件由雨幕档案已交付阅读器快照生成，收录目录中“此即明日”序章的六个小节，包含字幕、旁白、对白与选项。未加入快照未提供的图片、题记或叙述，也不以参考 PDF 补写缺文。源快照标记为备用或日志的行不属于阅读路径；场景分段按目录顺序连续接入。]
#v(3mm, weak: false)
#text(size: 8pt)[《重返未来：1999》剧情文本、角色名称及相关权利归原权利人所有。本文件为玩家整理的非官方、非商业私人阅读版本。整理与排版不代表拥有原文版权。本次生成日期：2026-09-27。]
#pagebreak()
'''


def group_sections(entries):
    """Only adjacent scene parts may merge; trails keep their catalogue position."""
    groups = OrderedDict()
    previous_tag = None
    for entry in entries:
        tag = entry['tag']
        is_trail = entry['id'].startswith('json_trail_')
        if not groups or is_trail or tag != previous_tag:
            groups[str(len(groups))] = []
        groups[next(reversed(groups))].append(entry)
        previous_tag = None if is_trail else tag
    assert [e['id'] for members in groups.values() for e in members] == [e['id'] for e in entries]
    return groups


def build(entries=None, title=None, output=None, render=True):
    helper = load_helper()
    standalone = entries is None
    if standalone:
        entries = json.loads((ASSETS / 'directory.json').read_text(encoding='utf-8'))
        entries = [e for e in entries if e.get('categoryTitle') == '主线剧情' and e.get('chapterTitle') == '此即明日']
    groups = group_sections(entries)
    typ, expected, scenes, choices = THEME, [], [], []
    theme_end = THEME.index('#text(size: 25pt')
    typ = THEME[:theme_end]
    title = title or '此即明日'
    typ = typ.replace('title: "此即明日 · 中英双语序章"', 'title: ' + helper.q(title))
    typ += '#text(size: 24pt, weight: "regular")[#text(' + helper.q(title) + ')]\n'
    english_title = entries[0].get('chapterTitle_en', '')
    if english_title:
        typ += '#v(4mm, weak: false)\n#text(font: "Constantia", size: 16pt)[#text(' + helper.q(english_title) + ')]\n'
    typ += r"""
#v(13mm, weak: false)
#text(size: 14pt)[目录 / #text(font: "Constantia", size: 12pt)[Contents]]
#v(6mm, weak: false)
#context {
  for item in query(heading.where(level: 1)) {
    block(breakable: false, above: 0pt, below: 0pt, inset: (left: 5mm))[
      #set text(size: 11pt)
      #set par(leading: 0.65em)
      #box(width: 100%)[#link(item.location())[#item.body#if item.supplement != "" [#h(1.7mm)#text(font: "Constantia", size: 10pt)[/]#h(1.7mm)#text(font: "Constantia", size: 10pt, lang: "en", hyphenate: false)[#item.supplement]]#h(3mm)#box(width: 1fr)[#repeat(gap: 1.8mm)[#text(font: "Constantia", size: 7pt)[.]]]#h(3mm)#box(width: 9mm)[#align(right)[#text(font: "Constantia")[#counter(page).at(item.location()).first()]]]]]
      #v(4.2mm, weak: false)
    ]
  }
}
#pagebreak()
"""
    section_openers = []
    branch_pairs = []
    types = Counter()
    for tag, members in groups.items():
        first = members[0]
        complete = len(members) == sum(e['tag'] == first['tag'] for e in entries)
        title_zh, title_en = first['title_zh'], first['title_en']
        if complete:
            title_zh = re.sub(r'\s*·\s*\d+/\d+$', '', title_zh)
            title_en = re.sub(r'\s*·\s*\d+/\d+$', '', title_en)
        section_code = f'\n#section({helper.q(title_zh)}, {helper.q(title_en)}, {helper.q(first["tag"])})\n'
        rendered = []
        for entry in members:
            path = ASSETS / (entry['id'] + '.json')
            scene = json.loads(path.read_text(encoding='utf-8'))
            items = walk(scene)
            event_ids = []
            indent = 0
            def content(value):
                return '[' + '\n#linebreak()\n'.join(rich_text(line) for line in clean(value).split('\n')) + ']'
            def append_pair(pair_id, zh, en, kind, zw='', ew=''):
                long = len(plain(en)) / 85 + len(plain(zh)) / 42 + en.count('\n') + zh.count('\n') > 26
                expected.append({'id': pair_id, 'en': plain(en), 'zh': plain(zh), 'long': long})
                code = '\n#pair(' + ', '.join((helper.q(zw), content(zh), helper.q(ew), content(en), helper.q(kind))) + f', indent: {indent}mm, long: {str(long).lower()})\n'
                rendered.append((code, pair_id))
                branch_pairs.append({'id': pair_id, 'indentMm': indent})
            for kind, payload in items:
                if kind == 'reference':
                    rendered.append(('\n#path-label("返回前述话题选项 / Return to the topic choices above")\n', None))
                    continue
                if kind == 'branch':
                    indent = 4 * (payload.count('.') + 1)
                    rendered.append((f'\n#path-label({helper.q("分支 " + payload + " / Branch " + payload)}, indent: {indent - 4}mm)\n', None))
                    continue
                if kind == 'join':
                    indent = 4 * (payload.count('.') + 1) if payload else 0
                    rendered.append((f'\n#path-label("共同接续 / Shared continuation", indent: {indent}mm)\n', None))
                    continue
                row = payload
                event_ids.append(row['id'])
                types[row['type']] += 1
                assert row['type'] in ('dialogue', 'narration', 'screen', 'card', 'choice')
                if row['type'] == 'choice':
                    options = row['options']
                    rendered.append((f'\n#path-label("选项 / Choices", indent: {indent}mm)\n', None))
                    for i, option in enumerate(options, 1):
                        en = f'{i}. {option.get("en") or "[English text unavailable]"}'
                        zh = f'{i}. {option.get("zh") or "〔中文缺文〕"}'
                        append_pair(option['id'], zh, en, 'option')
                        if (option.get('runtime') or {}).get('type') == 'continue':
                            rendered.append(('\n#path-label("返回前述话题选项 / Return to the topic choices above")\n', None))
                    choices.extend(o['id'] for o in options)
                else:
                    en, zh = row.get('en') or '[English text unavailable]', row.get('zh') or '〔中文缺文〕'
                    append_pair(row['id'], zh, en, row['type'], row.get('spk_zh', ''), row.get('spk_en', ''))
            scenes.append({'id': scene['id'], 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                           'tag': tag, 'events': event_ids,
                           'archivedNotInReadingPath': [e['id'] for e in scene['lines'] if e['type'] == 'archived']})
        # The same opener rule applies to every section, independent of wording:
        # keep the compact title group with at least two full bilingual pairs.
        opener_end, opener_ids = 0, []
        for opener_end, (_, pair_id) in enumerate(rendered, 1):
            if pair_id:
                opener_ids.append(pair_id)
            if len(opener_ids) >= 2:
                break
        long_ids = {e['id'] for e in expected if e['long']}
        if any(i in long_ids for i in opener_ids):
            opener_end, opener_ids = 0, []
        section_openers.append({'tag': tag, 'title': title_zh, 'pairs': opener_ids})
        typ += '\n#block(breakable: false, above: 11mm, below: 4mm, sticky: true)[\n' + section_code
        typ += ''.join(code for code, _ in rendered[:opener_end]) + ']\n'
        typ += ''.join(code for code, _ in rendered[opener_end:])
    stem = Path(output) if output else OUT / 'rain-archive-prologue-zh-en-v2'
    stem.parent.mkdir(parents=True, exist_ok=True)
    stem.with_suffix('.typ').write_text(typ, encoding='utf-8')
    subprocess.run([typst_executable(), 'compile', str(stem.with_suffix('.typ')), str(stem.with_suffix('.pdf'))], check=True)
    doc = pymupdf.open(stem.with_suffix('.pdf'))
    normalize = lambda value: re.sub(r'\s+', '', value)
    page_texts = [normalize(p.get_text(clip=pymupdf.Rect(0, 0, p.rect.width, p.rect.height - 60))) for p in doc]
    all_text = ''.join(page_texts)
    cursor, event_pages = 0, {}
    for event in expected:
        common = set(range(len(doc)))
        for lang in ('en', 'zh'):
            target = normalize(event[lang])
            found = all_text.find(target, cursor)
            assert found >= 0, f'Missing/out-of-order text: {event["id"]} {lang}'
            cursor = found + len(target)
            common &= {i for i, value in enumerate(page_texts) if target in value}
        assert common or event['long'], f'Bilingual pair split: {event["id"]}'
        event_pages[event['id']] = sorted(i + 1 for i in common)
    toc = doc.get_toc()
    assert len(toc) == len(groups), f'Unexpected bookmarks: {toc}'
    links = [link for page in list(doc)[:toc[0][2] - 1] for link in page.get_links()]
    unique_links = {}
    for link in links:
        unique_links.setdefault((link['page'], round(link['to'].y, 1)), link)
    links = list(unique_links.values())
    assert len(links) == len(groups), f'Missing TOC links: {len(links)}/{len(groups)}'
    assert [link['page'] + 1 for link in links] == [item[2] for item in toc], 'TOC links/bookmarks differ'
    assert all(0 <= link['page'] < len(doc) for link in links)
    for opener, bookmark in zip(section_openers, toc):
        assert all(bookmark[2] in event_pages[event_id] for event_id in opener['pairs']), f'Title separated from opening text: {opener["tag"]}'
    for i, page in enumerate(doc):
        assert '\ufffd' not in page.get_text()
        page_lines = [line for block in page.get_text('dict')['blocks'] for line in block.get('lines', [])]
        for n, line in enumerate(page_lines):
            for other in page_lines[n + 1:]:
                intersection = pymupdf.Rect(line['bbox']) & pymupdf.Rect(other['bbox'])
                assert not (intersection.height > 1 and intersection.width > 15), f'Overlapping text lines on page {i + 1}'
        for block in page.get_text('dict')['blocks']:
            for line in block.get('lines', []):
                for span in line['spans']:
                    assert span['color'] == 0
                    assert page.rect.contains(pymupdf.Rect(span['bbox'])), span['text']
        if render:
            page.get_pixmap(matrix=pymupdf.Matrix(1.4, 1.4)).save(stem.parent / f'page-{i + 1:02}.png')
    report = {'date': '2026-09-27', 'environment': 'Windows; delivered snapshot; raw resources not needed',
              'directorySha256': hashlib.sha256((ASSETS / 'directory.json').read_bytes()).hexdigest(),
              'sections': len(groups), 'scenes': scenes, 'eventCounts': dict(types), 'events': sum(types.values()), 'textPairs': len(expected),
              'optionIds': choices, 'pages': len(doc), 'bookmarks': toc, 'eventPages': event_pages,
              'longPairIds': [e['id'] for e in expected if e['long']], 'sectionOpeners': section_openers, 'branchPairIndents': branch_pairs,
              'checks': ['all delivered non-archived events covered once', 'graph reading order',
                         'English then Chinese text retained in order', 'short pairs on one page; long documents flow English then Chinese',
                         'all TOC links and bookmarks match', 'title with first two complete pairs',
                         'black text, page bounds and no overlapping text lines'],
              'pagination': 'natural; no sentence-specific groups', 'scope': 'delivered chapter snapshot; private local export'}
    stem.with_suffix('.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    if standalone:
        print(json.dumps({k: report[k] for k in ('sections', 'events', 'eventCounts', 'pages', 'bookmarks')}, ensure_ascii=False, indent=2))
    doc.close()
    return report


if __name__ == '__main__':
    build()
