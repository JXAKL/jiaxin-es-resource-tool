import unittest
from organizer.manual_content import MANUALS, manual


class ManualTests(unittest.TestCase):
    def test_all_supported_contexts_have_detailed_local_manuals(self):
        expected = {'author', 'pair', 'scripts', 'videos', 'folders', 'collector', 'search_scraper',
                    'completion', 'resource_organize', 'downloads', 'playback', 'duplicate_organize'}
        self.assertEqual(set(MANUALS), expected)
        for key in expected:
            with self.subTest(key=key):
                title, sections = manual(key)
                self.assertTrue(title)
                self.assertGreaterEqual(len(sections), 5)
                self.assertEqual(len(sections), len({s[0] for s in sections}))
                self.assertIn('与其他模块联动', [s[0] for s in sections])
                self.assertGreater(sum(len(body) for _, body in sections), 650)

    def test_current_features_and_scope_are_explained(self):
        body = lambda key: '\n'.join(text for _, text in manual(key)[1])
        self.assertIn('连同配对脚本一起整理', body('pair'))
        self.assertIn('关闭主开关会同时取消', body('pair'))
        self.assertIn('全部满足', body('search_scraper'))
        self.assertIn('删除本地缓存不会删除黑名单', body('search_scraper'))
        self.assertIn('交换主从', body('resource_organize'))
        self.assertIn('严格小于', body('folders'))
        self.assertNotIn('MEGA 本地下载与网盘保存', body('pair'))
        self.assertNotIn('启动Funscript查重.bat', body('scripts'))


if __name__ == '__main__':
    unittest.main()
