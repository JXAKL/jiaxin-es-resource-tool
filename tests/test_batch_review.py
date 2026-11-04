import unittest
from copy import deepcopy
from organizer.model import Candidate, FileGroup, Match
from organizer.review import can_choose_highest, choose_highest_authors, matches_in_view
from organizer.author_weight import author_support


def candidate(author, score, **kw):
    return Candidate(author, len(author), author, '', score, '', verified=True, eligible=True, **kw)


class BatchReviewTests(unittest.TestCase):
    def match(self, candidates):
        return Match(FileGroup('x', 'Example work'), '多作者待核对', candidates)

    def test_all_candidates_ranked_by_composite_not_input_order(self):
        match = self.match([candidate('Low', .89), candidate('High', .95)])
        result = choose_highest_authors([match], .85)
        self.assertEqual(result['selected'], 1)
        self.assertEqual(match.author, 'High')
        self.assertTrue(match.selected and match.manual)
        self.assertEqual(author_support([match]), {})
        self.assertEqual(choose_highest_authors([match], .85)['selected'], 0)

    def test_equal_authors_do_not_create_false_tie(self):
        match = self.match([candidate('Alpha', .95), candidate('alpha', .95), candidate('Beta', .94)])
        self.assertEqual(choose_highest_authors([match], .85)['selected'], 1)

    def test_tie_below_threshold_and_conflict_remain_reviewable(self):
        rows = [self.match([candidate('A', .95), candidate('B', .95)]),
                self.match([candidate('A', .80), candidate('B', .79)]),
                self.match([candidate('A', .99, conflict=True), candidate('B', .94)])]
        counts = choose_highest_authors(rows, .85)
        self.assertEqual(counts, dict(selected=0, tied=1, below_threshold=1, insufficient=1, preserved=0))
        self.assertTrue(all(not m.selected and not m.author for m in rows))

    def test_manual_and_non_multi_author_rows_untouched(self):
        row = self.match([candidate('A', .95), candidate('B', .94)])
        row.manual, row.author = True, 'Chosen'
        other = self.match(row.candidates)
        other.status = '待核对'
        choose_highest_authors([row, other], .85)
        self.assertEqual(row.author, 'Chosen')
        self.assertFalse(other.author)

    def test_existing_selection_and_assigned_author_never_overwritten(self):
        for selected, author in ((True, 'Chosen'), (True, ''), (False, 'Chosen')):
            with self.subTest(selected=selected, author=author):
                row = self.match([candidate('A', .99), candidate('B', .94)])
                row.selected, row.author = selected, author
                row.selection_note = 'Original decision'
                before = deepcopy(row)
                counts = choose_highest_authors([row], .85)
                self.assertEqual(row, before)
                self.assertEqual(counts['preserved'], 1)
                self.assertFalse(can_choose_highest(row))

    def test_unchecked_manual_decision_is_not_reselected(self):
        row = self.match([candidate('A', .99), candidate('B', .94)])
        choose_highest_authors([row], .85)
        row.selected = False
        before = deepcopy(row)
        self.assertEqual(choose_highest_authors([row], .85)['selected'], 0)
        self.assertEqual(row, before)

    def test_review_count_includes_single_author_and_skip_reasons_balance(self):
        rows = [self.match([candidate('A', .99), candidate('B', .94)]),
                self.match([candidate('A', .96), candidate('B', .96)]),
                self.match([candidate('A', .80)]), self.match([]),
                self.match([candidate('A', .92, conflict=True)]),
                Match(FileGroup('single', 'Single'), '待核对', [candidate('A', .80)])]
        counts = choose_highest_authors(rows, .85)
        self.assertEqual(counts, dict(selected=1, tied=1, below_threshold=1, insufficient=2, preserved=0))
        self.assertEqual(sum('核对' in m.status for m in rows), 5)
        self.assertEqual(sum(m.selected for m in rows), 1)

    def test_batch_scope_uses_same_rows_as_preview(self):
        rows = [Match(FileGroup('ready', 'Ready'), '可归类', [], 'Confirmed', True),
                self.match([candidate('A', .99), candidate('B', .94)]),
                Match(FileGroup('failed', 'Failed'), '搜索失败', [], error='Network error')]
        self.assertEqual([i for i, _ in matches_in_view(rows, '待核对')], [1])
        counts = choose_highest_authors([m for _, m in matches_in_view(rows, '可归类')], .85)
        self.assertEqual(counts['selected'], 0)
        self.assertFalse(rows[1].selected)
        counts = choose_highest_authors([m for _, m in matches_in_view(rows, '待核对')], .85)
        self.assertEqual(counts['selected'], 1)
        self.assertEqual(list(matches_in_view(rows, '待核对')), [])

    def test_failed_and_nonfinite_candidates_do_not_select(self):
        failed = self.match([candidate('A', .99)])
        failed.error = 'Failed verification'
        invalid = self.match([candidate('A', float('nan')), candidate('B', float('inf'))])
        before = deepcopy(failed)
        counts = choose_highest_authors([failed, invalid], .85)
        self.assertEqual(counts['selected'], 0)
        self.assertEqual(counts['insufficient'], 1)
        self.assertEqual(failed, before)
