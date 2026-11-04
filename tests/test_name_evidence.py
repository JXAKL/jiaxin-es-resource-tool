"""Acceptance and counterexamples for high single-channel name evidence."""
from dataclasses import replace
import unittest

from organizer.evidence import candidate_for
from organizer.model import FileGroup, Topic, TopicIndex, match_group


def post(title, *, body='', filenames=(), identity=1, author='Alpha', verified=True):
    return Topic(identity, title, author, '', body=body, filenames=list(filenames), details_loaded=verified)


class WholeNameTests(unittest.TestCase):
    def check_selected(self, name, topic, threshold=.95):
        result = match_group(FileGroup('g', name), [topic], threshold)
        self.assertTrue(result.selected, result)
        self.assertFalse(result.candidates[0].conflict)
        return result.candidates[0]

    def test_verified_title_alone_is_independent_evidence(self):
        candidate = self.check_selected('Aurora Harbor Midnight', post('Aurora Harbor Midnight', body='Download below.'))
        self.assertGreaterEqual(candidate.title_score, .95)
        self.assertLess(candidate.body_score, .5)

    def test_many_external_tags_do_not_dilute_title(self):
        self.check_selected('Aurora Harbor Midnight', post(
            '[Creator] [CS-PAID-0008] Aurora Harbor Midnight [multi-axis] [Patreon requested] [New update]'))

    def test_field_reordering_keeps_all_name_tokens(self):
        candidate = self.check_selected('SkyArtist Aurora Harbor Midnight', post('Aurora Harbor Midnight by SkyArtist'))
        self.assertGreaterEqual(candidate.title_score, .95)

    def test_complete_body_name_can_be_reordered_in_one_passage(self):
        candidate = self.check_selected('SkyArtist Aurora Harbor Midnight',
                                        post('Latest upload', body='Aurora Harbor Midnight by SkyArtist'))
        self.assertGreaterEqual(candidate.body_score, .95)

    def test_same_words_without_the_local_common_word_are_not_strong_evidence(self):
        topic = post('New work', body='SkyArtist Aurora Harbor')
        result = candidate_for(FileGroup('g', 'SkyArtist Aurora Harbor Dance'), topic)
        self.assertLess(result.body_score, .95)
        self.assertFalse(result.eligible)

    def test_scattered_body_keywords_are_not_whole_name_evidence(self):
        topic = post('General discussion', body='Aurora one two three four five six seven Harbor\n'
                     'Midnight is in an unrelated paragraph by SkyArtist')
        self.assertFalse(match_group(FileGroup('g', 'SkyArtist Aurora Harbor Midnight'), [topic]).selected)

    def test_short_common_fragments_do_not_gain_independent_evidence(self):
        for name in ('Dance', '12345', 'Blue Archive', 'Aurora'):
            with self.subTest(name=name):
                # Blue Archive has enough letters to be a title, but a longer
                # local name cannot be resolved by that shared fragment alone.
                local = name + ' Kisaki Midnight' if name == 'Blue Archive' else name
                self.assertFalse(match_group(FileGroup('g', local), [post(name)]).selected)

    def test_full_attachment_survives_collection_count_and_creator_digits(self):
        for name, title in [('SkyArtist Aurora 01', 'Collection of 159 scripts'),
                            ('(500units) Aurora Harbor by ty98', 'Aurora Harbor by ty98'),
                            ('(1)青空放課後のタイム', '[作者] シリーズ2 01青空放課後のタイム')]:
            with self.subTest(name=name):
                self.check_selected(name, post(title, filenames=[name + '.funscript']))

    def test_title_episode_conflict_still_blocks_even_exact_attachment(self):
        for title in ('Aurora Harbor Episode 02', '[Creator] Aurora Harbor Episode 02 [Free]'):
            result = match_group(FileGroup('g', 'Aurora Harbor Episode 01'),
                                 [post(title, body='Aurora Harbor Episode 01', filenames=['Aurora Harbor Episode 01.funscript'])])
            self.assertFalse(result.selected)
            self.assertTrue(result.candidates[0].conflict)

    def test_leading_zero_numbers_are_equivalent(self):
        self.check_selected('Aurora Harbor Episode 01', post('Aurora Harbor Episode 1'))

    def test_episode_and_series_number_do_not_cancel_real_conflict(self):
        result = candidate_for(FileGroup('g', 'Aurora Harbor part 1'), post('Aurora Harbor part 2 volume 1'))
        self.assertTrue(result.conflict)

    def test_same_episode_with_additional_volume_is_not_a_conflict(self):
        self.check_selected('Aurora Harbor Part 1', post('Aurora Harbor Part 1 Volume 2'))

    def test_artist_field_with_inserted_series_is_also_a_rival(self):
        name = '(SkyArtist) Midnight'
        first = post('(SkyArtist) Midnight [multi-axis]')
        second = post('(SkyArtist) Aurora Harbor Midnight', author='Beta', identity=2)
        result = match_group(FileGroup('g', name), [first, second])
        self.assertEqual(result.status, '多作者待核对')
        self.assertTrue(all(c.title_score >= .95 for c in result.candidates))

    def test_long_complete_body_name_is_not_cut_by_window_boundary(self):
        name = 'SkyArtist Aurora Harbor Midnight Festival Northern Lights Winter Performance'
        result = self.check_selected(name, post('Archive', body='Original source video name: ' + name + '\nDownload below.'))
        self.assertIn('完整名称', result.evidence[-1])

    def test_body_explanation_comes_from_the_full_name_passage(self):
        name = 'SkyArtist Aurora Harbor Dance'
        topic = post('Upload', body='SkyArtist Aurora Harbor unrelated\nOriginal title: ' + name)
        result = candidate_for(FileGroup('g', name), topic)
        self.assertIn('dance', result.evidence[0].lower())

    def test_one_hint_can_retrieve_decorated_title_without_other_hint_grams(self):
        name = 'Aurora Harbor Midnight'
        correct = post('[Creator] ' + name + ' [multi-axis] [Patreon new update]', identity=100)
        near = [post(name + ' Extra', identity=n) for n in range(1, 80)]
        group = FileGroup('g', 'Another Unrelated Video Filename', hints=[name])
        self.assertIn(correct, TopicIndex(near + [correct]).shortlist(group))

    def test_unlabelled_base_cannot_choose_sequel_or_edition(self):
        for extra in ('2', 'Part 2', 'Extended', 'Remix', 'Preview', 'Compilation'):
            with self.subTest(extra=extra):
                result = candidate_for(FileGroup('g', 'Aurora Harbor Midnight'), post('Aurora Harbor Midnight ' + extra))
                self.assertLess(result.title_score, .95)
                self.assertFalse(match_group(FileGroup('g', 'Aurora Harbor Midnight'),
                                            [post('Aurora Harbor Midnight ' + extra)], .95).selected)

    def test_names_must_match_whole_words_and_all_creator_qualifiers(self):
        for name, title in [('Aurora Harbor', 'SuperAurora Harboring'),
                            ('CreatorOne Aurora Midnight', 'CreatorTwo Aurora Midnight')]:
            self.assertLess(candidate_for(FileGroup('g', name), post(title)).title_score, .95)

    def test_two_authors_with_same_whole_name_remain_for_review(self):
        topic = post('Aurora Harbor Midnight')
        result = match_group(FileGroup('g', topic.title), [topic, replace(topic, id=2, author='Beta')])
        self.assertFalse(result.selected)
        self.assertEqual(result.status, '多作者待核对')

    def test_user_threshold_is_not_bypassed(self):
        result = match_group(FileGroup('g', 'Aurora Harbor Midnight'), [post('Aurora Harbor Midnight')], .99)
        self.assertFalse(result.selected)

    def test_unverified_title_requires_hydration_or_existing_author_support(self):
        result = match_group(FileGroup('g', 'Aurora Harbor Midnight'), [post('Aurora Harbor Midnight', verified=False)])
        self.assertFalse(result.selected)
        self.assertFalse(result.candidates[0].verified)

    def test_decorated_title_retained_outside_fuzzy_budget_and_updated(self):
        name = 'Aurora Harbor Midnight'
        near = [post(name + ' Extra', identity=n, verified=False) for n in range(1, 80)]
        correct = post('[Creator] [CS-PAID-0008] ' + name + ' [multi-axis] [Patreon exclusive new update]', identity=100)
        group = FileGroup('g', name)
        index = TopicIndex(near + [correct])
        self.assertIn(correct, index.shortlist(group))
        changed = replace(correct, title='No longer the same work')
        all_topics = {t.id: t for t in near + [changed]}
        index.update(all_topics, {100})
        self.assertEqual(index.shortlist(group), TopicIndex(list(all_topics.values())).shortlist(group))


if __name__ == '__main__':
    unittest.main()
