"""Reading-path tests for PDF export; rendering is checked on the generated book."""
import importlib.util
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location('prologue', Path(__file__).with_name('build-prologue-pdf.py'))
pdf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pdf)


def scene(edges, choices=None, reversed_lines=False):
    choices = choices or {}
    lines, nodes = [], []
    for node_id, targets in edges.items():
        event = {'id': str(node_id), 'nodeId': node_id, 'type': 'dialogue', 'zh': str(node_id), 'en': str(node_id)}
        if node_id in choices:
            event.update(type='choice', options=[{'target': t} for t in choices[node_id]])
        lines.append(event)
        nodes.append({'id': node_id, 'events': [str(node_id)], 'edges': [{'target': t} for t in targets]})
    return {'entryNodeId': next(iter(edges)), 'nodes': nodes, 'lines': list(reversed(lines)) if reversed_lines else lines}


def ids(items):
    return [payload['nodeId'] for kind, payload in items if kind == 'event']


class ProloguePathTests(unittest.TestCase):
    def test_trails_do_not_move_behind_merged_story_parts(self):
        entries = [{'id': 'part1', 'tag': '01'}, {'id': 'json_trail_a', 'tag': '小径'},
                   {'id': 'part2', 'tag': '01'}, {'id': 'part3', 'tag': '01'},
                   {'id': 'json_trail_b', 'tag': '小径'}]
        groups = list(pdf.group_sections(entries).values())
        self.assertEqual([[e['id'] for e in g] for g in groups],
                         [['part1'], ['json_trail_a'], ['part2', 'part3'], ['json_trail_b']])

    def test_trail_menu_exit_is_not_an_extra_option(self):
        data = scene({1: [2, 3, 4], 2: [4], 3: [4], 4: []}, {1: [2, 3]})
        data['trailOf'] = 'test-parent'
        self.assertEqual(ids(pdf.walk(data)), [1, 2, 3, 4])

    def test_delivered_nested_trail_and_topic_menu_cover_every_event_once(self):
        import json
        for sid in ('json_trail_1020801', 'json_story_step_101338'):
            data = json.loads((pdf.ASSETS / (sid + '.json')).read_text(encoding='utf-8'))
            event_ids = [e['id'] for kind, e in pdf.walk(data) if kind == 'event']
            self.assertEqual(len(event_ids), len(set(event_ids)))
            self.assertEqual(set(event_ids), {e['id'] for e in data['lines'] if e['type'] != 'archived'})

    def test_math_positioning_preserves_radical_and_literal_angle_text(self):
        self.assertEqual(pdf.plain('√<voffset=-3><space=9>̅<space=-13>2'), '√2')
        self.assertEqual(pdf.plain('<温迪><color=red>你好</color>'), '<温迪>你好')

    def test_source_array_order_does_not_drive_reading(self):
        self.assertEqual(ids(pdf.walk(scene({1: [2], 2: [3], 3: []}, reversed_lines=True))), [1, 2, 3])

    def test_all_branches_precede_the_common_tail(self):
        walked = pdf.walk(scene({1: [2, 3], 2: [4], 3: [4], 4: []}, {1: [2, 3]}))
        self.assertEqual(ids(walked), [1, 2, 3, 4])
        self.assertEqual([k for k, _ in walked].count('join'), 1)

    def test_nested_branches_resume_at_each_common_tail(self):
        walked = pdf.walk(scene({1: [2, 3], 2: [4, 5], 4: [6], 5: [6], 6: [7], 3: [8], 8: [7], 7: []}, {1: [2, 3], 2: [4, 5]}))
        self.assertEqual(ids(walked), [1, 2, 4, 5, 6, 3, 8, 7])
        self.assertEqual([v for k, v in walked if k == 'branch'], ['1', '1.1', '1.2', '2'])
        self.assertEqual([v for k, v in walked if k == 'join'], ['1', ''])

    def test_unverified_or_unreachable_text_fails_instead_of_disappearing(self):
        with self.assertRaises(AssertionError):
            pdf.walk(scene({1: [2, 3], 2: [], 3: []}))
        with self.assertRaises(AssertionError):
            pdf.walk(scene({1: [], 2: []}))


if __name__ == '__main__':
    unittest.main()
