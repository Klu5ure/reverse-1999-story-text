"""Plain-text archive rendering: ordering, markup stripping and language gaps."""
import importlib.util
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location('build_text_archive', ROOT / 'scripts/build-text-archive.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


bta = _load()


def scene(nodes, lines, entry=1):
    return {'entryNodeId': entry, 'nodes': nodes, 'lines': lines}


def node(node_id, events, edges):
    return {'id': node_id, 'events': events, 'edges': [{'target': t} for t in edges]}


def line(line_id, node_id, text, kind='dialogue', **extra):
    return {'id': line_id, 'nodeId': node_id, 'type': kind, 'zh': text, **extra}


class CleanTextTests(unittest.TestCase):
    def test_strips_only_whitelisted_markup(self):
        self.assertEqual(bta.clean_text('<size=38>你好</size>'), '你好')
        self.assertEqual(bta.clean_text('<marktop=アルカニスト>神秘学家</marktop>'), '神秘学家')
        self.assertEqual(bta.clean_text('<color=#000000>x</color>'), 'x')

    def test_keeps_literal_angle_bracket_text(self):
        # These look like tags but are story text; an unanchored strip would eat them.
        # None of them is a valid tag shape, so they also need no escaping.
        for literal in ('<보안 가이드>', '<嗯？>', '<Oh?>', '<삼연귀소>'):
            self.assertEqual(bta.clean_text(literal), literal)

    def test_escapes_only_tokens_a_renderer_would_swallow(self):
        # Markdown treats these as raw HTML and drops them, so escape the brackets.
        for source in ('<UTTU>', '<So the platonic year>', '<ERROR>', '<What an unusual girl.>'):
            self.assertEqual(bta.clean_text(source), source.replace('<', '&lt;').replace('>', '&gt;'))
        # Not valid tag shapes: leave them readable in the raw text.
        for literal in ('<嗯？>', '<보안 가이드>', '<Oh?>'):
            self.assertEqual(bta.clean_text(literal), literal)

    def test_converts_emphasis_markup(self):
        self.assertEqual(bta.clean_text('<i>a</i><b>b</b><s>c</s>'), '*a***b**~~c~~')

    def test_document_tokens_and_blank_line_collapse(self):
        self.assertEqual(bta.clean_text('甲{p}乙'), '甲\n\n乙')
        # `{img}` carries no words, matching the reader's handling.
        self.assertEqual(bta.clean_text('见报{img}'), '见报')
        self.assertEqual(bta.clean_text('甲\n\n\n\n乙'), '甲\n\n乙')

    def test_layout_and_effect_tags_are_removed(self):
        # The reader's own cleaner now strips these too, so they must not appear.
        self.assertEqual(bta.clean_text('<nobr>７０</nobr>年代'), '７０年代')
        self.assertEqual(bta.clean_text('<glitch>触发事件之厅</glitch>可是'), '触发事件之厅可是')
        self.assertEqual(bta.clean_text('<brace>平衡傘</brace>版本'), '平衡傘版本')
        self.assertEqual(bta.clean_text('<notShowInLog>距离暴雨</notShowInLog>'), '距离暴雨')
        self.assertEqual(bta.clean_text('a<sub>2</sub>'), 'a2')

    def test_line_break_becomes_a_newline(self):
        self.assertEqual(bta.clean_text('标题<br/>正文'), '标题\n正文')
        self.assertEqual(bta.clean_text('甲<br>乙'), '甲\n乙')

    def test_no_trailing_whitespace_and_trimmed_edges(self):
        self.assertEqual(bta.clean_text('  甲  \n\n  乙  \n'), '甲\n\n乙')

    def test_leading_indent_is_removed_to_avoid_markdown_code_blocks(self):
        # Four or more leading spaces would render as a code block.
        self.assertEqual(bta.clean_text('                    <align="left">诗句'), '诗句')
        self.assertEqual(bta.clean_text('甲\n    乙'), '甲\n乙')


class WalkTests(unittest.TestCase):
    def test_follows_graph_edges_not_array_order(self):
        # `lines` is stored reversed, which is what the delivered assets often look like.
        lines = [
            line('c', 3, '第三句'),
            line('b', 2, '第二句'),
            line('a', 1, '第一句'),
        ]
        walked = bta.walk_scene(scene([node(1, ['a'], [2]), node(2, ['b'], [3]), node(3, ['c'], [])], lines))
        self.assertEqual([payload for kind, payload in walked if kind == 'line'], [2, 1, 0])

    def test_descends_into_every_option_and_labels_branches(self):
        lines = [
            line('q', 1, ''),
            {
                'id': 'q', 'nodeId': 1, 'type': 'choice', 'zh': '',
                'options': [
                    {'id': 'o1', 'zh': '甲', 'target': 2},
                    {'id': 'o2', 'zh': '乙', 'target': 3},
                ],
            },
            line('a', 2, '甲的内容'),
            line('b', 3, '乙的内容'),
        ]
        walked = bta.walk_scene(scene([node(1, ['q'], [2, 3]), node(2, ['a'], []), node(3, ['b'], [])], lines))
        kinds = [kind for kind, _ in walked]
        self.assertIn('options', kinds)
        self.assertEqual(kinds.count('branch'), 2)
        emitted = {payload for kind, payload in walked if kind == 'line'}
        self.assertEqual(emitted, {1, 2, 3})

    def test_nested_options_are_numbered_by_depth(self):
        # A choice inside a branch must not read as a sibling of that branch.
        lines = [
            {'id': 'q1', 'nodeId': 1, 'type': 'choice', 'zh': '',
             'options': [{'id': 'a', 'zh': '甲', 'target': 2}, {'id': 'b', 'zh': '乙', 'target': 3}]},
            {'id': 'q2', 'nodeId': 2, 'type': 'choice', 'zh': '',
             'options': [{'id': 'c', 'zh': '丙', 'target': 4}, {'id': 'd', 'zh': '丁', 'target': 5}]},
            line('x', 3, '乙的内容'),
            line('y', 4, '丙的内容'),
            line('z', 5, '丁的内容'),
        ]
        nodes = [node(1, ['q1'], [2, 3]), node(2, ['q2'], [4, 5]), node(3, ['x'], []), node(4, ['y'], []), node(5, ['z'], [])]
        walked = bta.walk_scene(scene(nodes, lines))
        labels = [payload[0] for kind, payload in walked if kind == 'branch']
        self.assertEqual(labels, ['1', '1.1', '1.2', '2'])

    def test_merged_tail_is_emitted_once(self):
        lines = [line('a', 2, '甲'), line('b', 3, '乙'), line('tail', 4, '共同的收尾')]
        nodes = [node(1, [], [2, 3]), node(2, ['a'], [4]), node(3, ['b'], [4]), node(4, ['tail'], [])]
        nodes[0]['events'] = ['choice']
        lines.insert(0, {
            'id': 'choice', 'nodeId': 1, 'type': 'choice', 'zh': '',
            'options': [{'id': 'o1', 'zh': '甲', 'target': 2}, {'id': 'o2', 'zh': '乙', 'target': 3}],
        })
        walked = bta.walk_scene(scene(nodes, lines))
        emitted = [payload for kind, payload in walked if kind == 'line']
        self.assertEqual(emitted.count(3), 1, 'shared tail must not repeat per branch')

    def test_stops_at_unverified_multi_edge_branch(self):
        lines = [line('a', 1, '甲'), line('b', 2, '乙')]
        walked = bta.walk_scene(scene([node(1, ['a'], [2, 3]), node(2, ['b'], []), node(3, [], [])], lines))
        self.assertEqual([payload for kind, payload in walked if kind == 'line'], [0])

    def test_loop_does_not_hang(self):
        lines = [line('a', 1, '甲'), line('b', 2, '乙')]
        walked = bta.walk_scene(scene([node(1, ['a'], [2]), node(2, ['b'], [1])], lines))
        self.assertEqual(len([1 for kind, _ in walked if kind == 'line']), 2)


class RenderTests(unittest.TestCase):
    def test_missing_language_is_marked_not_substituted(self):
        stats = bta.Counter(placeholder=True)
        rows = bta.render_line(line('a', 1, '中文', en='', spk_zh='甲', spk_en=''), 'en', stats)
        self.assertEqual(rows, [f'*{bta.MISSING_MARKER["en"]}*'])
        self.assertEqual(stats['missing'], 1)

    def test_placeholder_can_be_disabled(self):
        stats = bta.Counter(placeholder=False)
        self.assertEqual(bta.render_line(line('a', 1, '中文', en=''), 'en', stats), [])

    def test_speaker_and_card_rendering(self):
        stats = bta.Counter(placeholder=True)
        rows = bta.render_line(line('a', 1, '你好', spk_zh='星锑'), 'zh-CN', stats)
        self.assertEqual(rows, ['**星锑**：你好'])
        rows = bta.render_line(line('c', 1, '1966英国伦敦', kind='card', en='1966 London'), 'en', stats)
        self.assertEqual(rows, ['*[image: 1966 London]*'])

    def test_card_marker_uses_the_reader_language_brackets(self):
        stats = bta.Counter(placeholder=True)
        rows = bta.render_line(line('c', 1, '场景', kind='card', zh='场景'), 'zh-CN', stats)
        self.assertEqual(rows, ['*〔图：场景〕*'])

    def test_card_without_a_caption_keeps_its_label(self):
        stats = bta.Counter(placeholder=True)
        rows = bta.render_line(line('c', 1, '1966英国伦敦', kind='card'), 'ko', stats)
        self.assertEqual(rows, [f'*[이미지: {bta.MISSING_MARKER["ko"]}]*'])

    def test_card_empty_in_every_language_is_skipped(self):
        stats = bta.Counter(placeholder=True)
        self.assertEqual(bta.render_line(line('c', 1, '', kind='card'), 'zh-CN', stats), [])
        self.assertEqual(stats['missing'], 0)


class UntranslatedSectionTests(unittest.TestCase):
    """A whole missing section gets one notice, not a marker on every line."""

    def section(self, lines, nodes, lang='en'):
        stats = bta.Counter(placeholder=True)
        entry = {'sectionNumber': 1, 'tag': 'X-01', 'title_zh': '甲', 'title_en': 'A'}
        heading, body, info = bta.render_section(entry, scene(nodes, lines), lang, stats)
        return heading, body, info, stats

    def test_untranslated_prose_collapses_to_one_notice(self):
        lines = [line('a', 1, '第一句'), line('b', 2, '第二句'), line('c', 3, '第三句')]
        nodes = [node(1, ['a'], [2]), node(2, ['b'], [3]), node(3, ['c'], [])]
        _h, body, info, stats = self.section(lines, nodes)
        self.assertTrue(info['collapsed'])
        self.assertEqual(body, [f'*{bta.NOTICE_SECTION["en"]}*'])
        self.assertEqual(stats['missing'], 3)
        self.assertEqual(stats['collapsedSections'], 1)

    def test_caption_alone_does_not_count_as_translated_prose(self):
        # The card has English, the dialogue does not: still untranslated.
        lines = [
            line('c', 1, '会场', kind='card', en='Meeting Room'),
            line('a', 2, '中文台词'),
        ]
        nodes = [node(1, ['c'], [2]), node(2, ['a'], [])]
        _h, body, info, _stats = self.section(lines, nodes)
        self.assertTrue(info['collapsed'])
        self.assertEqual(info['translatedProse'], 0)

    def test_partial_translation_keeps_per_line_markers(self):
        lines = [line('a', 1, '第一句', en='First'), line('b', 2, '第二句')]
        nodes = [node(1, ['a'], [2]), node(2, ['b'], [])]
        _h, body, info, stats = self.section(lines, nodes)
        self.assertFalse(info['collapsed'])
        self.assertIn(f'*{bta.MISSING_MARKER["en"]}*', body)
        self.assertEqual(stats['collapsedSections'], 0)

    def test_chinese_archive_never_collapses(self):
        lines = [line('a', 1, '第一句'), line('b', 2, '第二句')]
        nodes = [node(1, ['a'], [2]), node(2, ['b'], [])]
        _h, body, info, _stats = self.section(lines, nodes, lang='zh-CN')
        self.assertFalse(info['collapsed'])
        self.assertEqual(body, ['第一句', '第二句'])


class LocalisationTests(unittest.TestCase):
    """The wrappers around the story text must be in the reader's language."""

    #: Wrapper wording that must never appear in a non-Chinese language folder.
    CHINESE_WRAPPERS = ("上一章", "下一章", "目录", " 节", "章 ·", "本索引由", "阅读语言", "本节无可读文本")

    def entry(self, scene_id, tag, title_zh, **extra):
        return {"id": scene_id, "tag": tag, "title_zh": title_zh, **extra}

    def chapters(self):
        common = dict(
            chapterTitle="甲章", chapterTitle_en="Alpha", chapterTitle_ja="甲章", chapterTitle_ko="갑장",
            categoryTitle="主线剧情", categoryTitle_en="Main Story", categoryTitle_ja="メインストーリー",
            categoryTitle_ko="메인 스토리",
        )
        entries = [
            self.entry("a", "1ST-01", "第一节", title_en="First", title_ja="第一節", title_ko="첫 절", **common),
            self.entry("b", "小径", "一条小径", title_en="A Trail", title_ja="小道", title_ko="오솔길", **common),
        ]
        return bta.build_structure(entries)

    def test_navigation_is_translated(self):
        self.assertEqual(bta.nav_bar("en", "a.md", "i.md", "b.md"), "[← Previous chapter](a.md) · [Contents](i.md) · [Next chapter →](b.md)")
        self.assertEqual(bta.nav_bar("ja", None, "i.md", None), "← 前の章 · [目次](i.md) · 次の章 →")

    def test_trail_tag_is_translated(self):
        chapters = self.chapters()
        for lang, expected in (("en", "Trail"), ("ja", "小道"), ("ko", "오솔길"), ("zh-TW", "小徑")):
            heading, _body, _info = bta.render_section(chapters[0]["members"][1], scene([], []), lang, bta.Counter(placeholder=True))
            self.assertIn(f"· {expected} ·", heading, f"{lang} trail label")

    def test_index_has_no_chinese_wrappers(self):
        chapters = self.chapters()
        for lang in ("en", "ja", "ko"):
            bta.prepare_language(chapters, lang)
            index = bta.build_index(chapters, lang)
            for wrapper in self.CHINESE_WRAPPERS:
                self.assertNotIn(wrapper, index, f"{lang} index leaked {wrapper!r}")

    def test_category_uses_the_catalogue_translation(self):
        chapters = self.chapters()
        members = chapters[0]["members"]
        self.assertEqual(bta.category_label("主线剧情", "en", members), "Main Story")
        self.assertEqual(bta.category_label("主线剧情", "ja", members), "メインストーリー")

    def test_category_falls_back_to_the_editorial_label(self):
        # No member carries a `categoryTitle_*` row for these, so the script's own label is used.
        self.assertEqual(bta.category_label("玩法故事", "en", []), "Gameplay Stories")
        self.assertEqual(bta.category_label("玩法故事", "ko", []), "게임플레이 스토리")


class LinkSyntaxTests(unittest.TestCase):
    """GitHub only treats a bare destination as a link if it has no whitespace."""

    def test_spaces_are_percent_encoded(self):
        self.assertEqual(bta.link_path("mainline/001-This Is Tomorrow.md"), "mainline/001-This%20Is%20Tomorrow.md")
        self.assertEqual(bta.link_path("a b/c d.md"), "a%20b/c%20d.md")

    def test_parentheses_and_angles_are_encoded(self):
        self.assertEqual(bta.link_path("a(1).md"), "a%281%29.md")
        self.assertEqual(bta.link_path("a<b>.md"), "a%3Cb%3E.md")

    def test_cjk_and_middle_dot_are_left_readable(self):
        # These do not break parsing, so the raw Markdown stays legible.
        self.assertEqual(bta.link_path("mainline/002-在我们的时代里.md"), "mainline/002-在我们的时代里.md")
        self.assertEqual(bta.link_path("extras/001-序章 · 补充片段.md"), "extras/001-序章%20·%20补充片段.md")

    def test_navigation_links_are_encoded(self):
        nav = bta.nav_bar("en", "002-In Our Time.md", "../README.md", None)
        self.assertIn("(002-In%20Our%20Time.md)", nav)
        self.assertNotIn("(002-In Our Time.md)", nav)

    def test_index_links_are_all_parseable(self):
        chapters = bta.build_structure([
            {
                "id": "a", "tag": "1ST-01", "title_zh": "第一节", "title_en": "First Section",
                "chapterTitle": "甲章", "chapterTitle_en": "Alpha Chapter",
                "categoryTitle": "主线剧情", "categoryTitle_en": "Main Story",
            },
        ])
        bta.prepare_language(chapters, "en")
        index = bta.build_index(chapters, "en")
        for dest in re.findall(r"\]\(([^)]*)\)", index):
            self.assertIsNone(re.search(r"[\s()<>]", dest), f"unparseable destination: {dest!r}")


class ParagraphTests(unittest.TestCase):
    """Consecutive lines must stay separate once Markdown renders them."""

    def render(self, lines, nodes, lang='zh-CN'):
        entry = {'sectionNumber': 1, 'tag': 'X-01', 'title_zh': '甲', 'title_en': 'A'}
        return bta.render_section(entry, scene(nodes, lines), lang, bta.Counter(placeholder=True))[1]

    def test_each_spoken_line_is_its_own_paragraph(self):
        lines = [line('a', 1, '第一句', spk_zh='甲'), line('b', 2, '第二句', spk_zh='甲')]
        nodes = [node(1, ['a'], [2]), node(2, ['b'], [])]
        self.assertEqual(self.render(lines, nodes), ['**甲**：第一句', '**甲**：第二句'])

    def test_multi_line_text_splits_into_paragraphs(self):
        # A poem or newspaper block keeps its lines apart instead of running on.
        lines = [line('a', 1, '第一行\n第二行\n第三行')]
        self.assertEqual(self.render(lines, [node(1, ['a'], [])]), ['第一行', '第二行', '第三行'])

    def options_scene(self):
        lines = [
            {'id': 'q', 'nodeId': 1, 'type': 'choice', 'zh': '',
             'options': [{'id': 'o1', 'zh': '甲', 'target': 2}, {'id': 'o2', 'zh': '乙', 'target': 3}]},
            line('x', 2, '甲的内容'),
            line('y', 3, '乙的内容'),
        ]
        nodes = [node(1, ['q'], [2, 3]), node(2, ['x'], []), node(3, ['y'], [])]
        return lines, nodes

    def test_options_stay_one_list(self):
        lines, nodes = self.options_scene()
        self.assertIn('- 甲\n- 乙', self.render(lines, nodes), 'choices must stay a single list')

    def test_branch_marker_is_its_own_paragraph(self):
        lines, nodes = self.options_scene()
        self.assertIn('**〔分支 1：甲〕**', self.render(lines, nodes))


class StructureTests(unittest.TestCase):
    def test_groups_by_category_and_keeps_catalogue_index(self):
        entries = [
            {'id': 'a', 'categoryTitle': '主线剧情', 'chapterTitle': '甲章', 'tag': '1ST-01'},
            {'id': 'b', 'categoryTitle': '活动剧情', 'chapterTitle': '乙章', 'tag': 'EV-01'},
            {'id': 'c', 'categoryTitle': '主线剧情', 'chapterTitle': '甲章', 'tag': '1ST-02'},
        ]
        chapters = bta.build_structure(entries)
        self.assertEqual([c['category'] for c in chapters], ['主线剧情', '活动剧情'])
        self.assertEqual([m['id'] for m in chapters[0]['members']], ['a', 'c'])
        self.assertEqual([m['sectionNumber'] for m in chapters[0]['members']], [1, 2])
        self.assertEqual([m['catalogueIndex'] for m in chapters[0]['members']], [0, 2])

    def test_unknown_category_is_rejected(self):
        with self.assertRaises(SystemExit):
            bta.build_structure([{'id': 'a', 'categoryTitle': '不存在', 'chapterTitle': '甲'}])

    def test_file_names_survive_embedded_newlines(self):
        self.assertEqual(bta.safe_name('Doomsday &\nThe Days After'), 'Doomsday & The Days After')
        self.assertEqual(bta.safe_name('a/b:c*d?e"f<g>h|i'), 'a_b_c_d_e_f_g_h_i')
        self.assertEqual(bta.safe_name('   '), 'chapter')


if __name__ == '__main__':
    unittest.main()
