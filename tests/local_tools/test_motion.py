import unittest, json, tempfile
from pathlib import Path
from unittest.mock import patch
from organizer.local_tools import smart_match

class ProtectedNameTests(unittest.TestCase):
    def test_existing_name_and_ambiguous_time_not_auto(self):
        with tempfile.TemporaryDirectory(prefix='motion-test-') as directory:
            directory = Path(directory)
            video = directory/'video'
            script = directory/'script'
            video.mkdir(); script.mkdir()
            for name in ('known.mp4', 'unknown.mp4'):
                (video/name).touch()
            data = {'actions': [{'at': 0, 'pos': 0}, {'at': 1000, 'pos': 100}], 'metadata': {'duration': 60}}
            (script/'known.funscript').write_text(json.dumps(data))
            data['extra'] = 'x'*2000
            (script/'other.funscript').write_text(json.dumps(data))
            with patch.object(smart_match, 'get_video_durations', return_value={str(p): 60 for p in video.iterdir()}):
                rows = smart_match.match_files(str(video), str(script), 95, 80, False, True)
            known = next(r for r in rows if r['video'] == 'known.mp4')
            self.assertEqual(known['funscript'], 'known.funscript')
            self.assertEqual(known['status'], '已同名（保留）')
            self.assertTrue(all(r['status'] != '高置信' for r in rows))
