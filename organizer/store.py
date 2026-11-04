from __future__ import annotations

import csv
import json
import sqlite3
import time
import hashlib
import re
from urllib.parse import urlparse
from dataclasses import asdict
from pathlib import Path

from .model import Topic


def csv_cell(value):
    text = str(value if value is not None else '')
    return "'" + text if text.startswith(('=', '+', '-', '@', '\t', '\r')) else text


class Store:
    def __init__(self, path: Path):
        self.topic_revision = 0
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS topics (id INTEGER PRIMARY KEY, author TEXT NOT NULL, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS authors (name TEXT PRIMARY KEY COLLATE NOCASE, offset INTEGER DEFAULT 0,
                complete INTEGER DEFAULT 0, updated REAL DEFAULT 0, error TEXT DEFAULT '');
            CREATE TABLE IF NOT EXISTS searches (query TEXT PRIMARY KEY, data TEXT NOT NULL, updated REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS profiles (username TEXT PRIMARY KEY COLLATE NOCASE, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS attempts (group_key TEXT PRIMARY KEY, updated REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS topic_resources (id INTEGER PRIMARY KEY, topic_digest TEXT NOT NULL, data TEXT NOT NULL);
        ''')

    def close(self):
        self.db.close()

    def revision_token(self):
        # data_version also observes imports/writes from another open tool window.
        return self.topic_revision, self.db.execute('PRAGMA data_version').fetchone()[0]

    def upsert(self, topic: Topic):
        fields = asdict(topic)
        resource = {key: fields.pop(key) for key in ('category_name', 'resource_links', 'resource_details_loaded')}
        data = json.dumps(fields, ensure_ascii=False)
        digest = hashlib.sha256(data.encode('utf-8')).hexdigest()
        resource_data = json.dumps(resource, ensure_ascii=False)
        old = self.db.execute('SELECT data FROM topics WHERE id=?', (topic.id,)).fetchone()
        old_resource = self.db.execute('SELECT topic_digest, data FROM topic_resources WHERE id=?', (topic.id,)).fetchone()
        if old and old[0] == data and old_resource == (digest, resource_data):
            return
        # Keep the original topic JSON shape readable by already-delivered
        # versions; new resource context lives in its own optional table.
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO topics VALUES (?, ?, ?)', (topic.id, topic.author, data))
            self.db.execute('INSERT OR REPLACE INTO topic_resources VALUES (?, ?, ?)', (topic.id, digest, resource_data))
        self.topic_revision += 1

    @staticmethod
    def _topic(row):
        data = json.loads(row[0])
        if row[1] and hashlib.sha256(row[0].encode('utf-8')).hexdigest() == row[1]:
            data.update(json.loads(row[2]))
        return Topic(**data)

    def topics(self) -> list[Topic]:
        return [self._topic(row) for row in self.db.execute('SELECT t.data, r.topic_digest, r.data FROM topics t LEFT JOIN topic_resources r ON t.id=r.id ORDER BY t.id')]

    def topic(self, topic_id: int) -> Topic | None:
        row = self.db.execute('SELECT t.data, r.topic_digest, r.data FROM topics t LEFT JOIN topic_resources r ON t.id=r.id WHERE t.id=?', (topic_id,)).fetchone()
        return self._topic(row) if row else None

    def author(self, name: str):
        row = self.db.execute('SELECT offset, complete, updated, error FROM authors WHERE name=?', (name,)).fetchone()
        return dict(zip(('offset', 'complete', 'updated', 'error'), row)) if row else None

    def author_progress(self, name: str, offset: int, complete: bool = False, error: str = ''):
        self.db.execute('INSERT OR REPLACE INTO authors VALUES (?, ?, ?, ?, ?)',
                        (name, offset, int(complete), time.time(), error))
        self.db.commit()

    def save_profile(self, username: str, data: dict):
        user = data.get('user', {})
        public = {key: user.get(key) for key in ('id', 'username', 'name', 'created_at')}
        public['profile_url'] = f'https://discuss.eroscripts.com/u/{username}/activity/topics'
        public['topic_count'] = (data.get('user_stat') or {}).get('topic_count')
        self.db.execute('INSERT OR REPLACE INTO profiles VALUES (?, ?)', (username, json.dumps(public, ensure_ascii=False)))
        self.db.commit()

    def search(self, query: str):
        row = self.db.execute('SELECT data, updated FROM searches WHERE query=?', (query,)).fetchone()
        return json.loads(row[0]) if row and time.time() - row[1] < 86400 else None

    def save_search(self, query: str, data):
        self.db.execute('INSERT OR REPLACE INTO searches VALUES (?, ?, ?)',
                        (query, json.dumps(data, ensure_ascii=False), time.time()))
        self.db.commit()

    def attempted(self, key: str) -> float:
        row = self.db.execute('SELECT updated FROM attempts WHERE group_key=?', (key,)).fetchone()
        return row[0] if row else 0.

    def mark_attempted(self, key: str):
        self.db.execute('INSERT OR REPLACE INTO attempts VALUES (?, ?)', (key, time.time()))
        self.db.commit()

    def import_file(self, path: Path, fallback_author='') -> int:
        data = json.loads(path.read_text(encoding='utf-8-sig'))
        rows = data.get('topics', []) if isinstance(data, dict) else data
        if not isinstance(rows, list):
            raise ValueError('索引应是主题数组，或带 topics 数组的 JSON 对象')
        topics = []
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError('索引记录必须是 JSON 对象')
            title, author, url = row.get('title'), row.get('author') or fallback_author, row.get('url')
            if not all(isinstance(v, str) and v.strip() for v in (title, author, url)):
                raise ValueError('索引记录缺少标题、作者或帖子地址')
            parsed = urlparse(url)
            if parsed.scheme != 'https' or parsed.netloc.lower() != 'discuss.eroscripts.com' or not parsed.path.startswith('/t/'):
                raise ValueError('索引帖子地址必须来自 EroScripts')
            numbers = re.findall(r'/(\d+)(?=/|$)', parsed.path)
            identity = int(numbers[0]) if numbers else -int(hashlib.sha256((author.casefold() + '\n' + url).encode()).hexdigest()[:14], 16)
            title = re.sub(r'\s+-\s+Scripts\s*/\s*(?:Free|Paid) Scripts\s*$', '', title, flags=re.I)
            topics.append(Topic(identity, title, author.strip(), url, row.get('created_at') or row.get('date') or '',
                row.get('updated_at') or '', row.get('category_id'), row.get('tags') or [], row.get('filenames') or [],
                row.get('views') or 0, row.get('posts_count') or 0,
                str(row.get('body') or '')[:24000], row.get('source_ids') or [], row.get('details_loaded') is True,
                [str(v) for v in row.get('links', [])] if isinstance(row.get('links'), list) else [], row.get('links_loaded') is True))
        # Validate the whole file before changing the cache.
        with self.db:
            for topic in topics:
                data = asdict(topic)
                for key in ('category_name', 'resource_links', 'resource_details_loaded'):
                    data.pop(key, None)
                self.db.execute('INSERT OR REPLACE INTO topics VALUES (?, ?, ?)',
                                (topic.id, topic.author, json.dumps(data, ensure_ascii=False)))
                self.db.execute('DELETE FROM topic_resources WHERE id=?', (topic.id,))
        self.topic_revision += 1
        return len(topics)

    def export(self, folder: Path):
        folder.mkdir(parents=True, exist_ok=True)
        topics = self.topics()
        authors = [dict(zip(('name', 'offset', 'complete', 'updated', 'error'), row))
                   for row in self.db.execute('SELECT * FROM authors ORDER BY name')]
        profiles = [json.loads(row[0]) for row in self.db.execute('SELECT data FROM profiles')]
        (folder / '作者帖子索引.json').write_text(json.dumps(
            {'version': 1, 'authors': authors, 'profiles': profiles, 'topics': [asdict(t) for t in topics]}, ensure_ascii=False, indent=2), encoding='utf-8')
        with (folder / '作者帖子索引.csv').open('w', encoding='utf-8-sig', newline='') as file:
            writer = csv.writer(file)
            writer.writerow(['作者', '帖子ID', '标题', '地址', '发布日期', '更新日期', '分类ID', '标签', '附件文件名', '浏览数', '帖子数', '首帖正文', '来源编号', '已读取首帖'])
            for t in topics:
                writer.writerow([csv_cell(v) for v in (t.author, t.id, t.title, t.url, t.created_at,
                    t.updated_at, t.category_id, ', '.join(t.tags), ' | '.join(t.filenames), t.views, t.posts_count,
                    t.body, ' | '.join(t.source_ids), t.details_loaded)])
