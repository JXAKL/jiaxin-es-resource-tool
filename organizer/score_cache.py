"""Disposable local evidence cache; never stores or changes classification actions."""
import hashlib
import json
import sqlite3
from dataclasses import asdict
from pathlib import Path

from .model import Candidate

SCORE_VERSION = 'evidence-2.1.14-version-axis-v1'


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


class ScoreCache:
    def __init__(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=5)
        self.db.execute('CREATE TABLE IF NOT EXISTS scores (group_hash TEXT, topic_id INTEGER, topic_hash TEXT, '
                        'version TEXT, data TEXT, PRIMARY KEY(group_hash, topic_id))')
        self.pending = []

    @staticmethod
    def group_key(group):
        return fingerprint((group.title, group.hints, group.topic_ids))

    def load(self, group, hashes):
        out = {}
        for identity, topic_hash, data in self.db.execute(
                'SELECT topic_id, topic_hash, data FROM scores WHERE group_hash=? AND version=?',
                (self.group_key(group), SCORE_VERSION)):
            if hashes.get(identity) != topic_hash:
                continue
            try:
                candidate = Candidate(**json.loads(data))
                if candidate.topic_id == identity:
                    out[identity] = candidate
            except (ValueError, TypeError):
                continue
        return out

    def store(self, group, candidates, hashes):
        key = self.group_key(group)
        for candidate in candidates:
            self.pending.append((key, candidate.topic_id, hashes[candidate.topic_id], SCORE_VERSION,
                                 json.dumps(asdict(candidate), ensure_ascii=False)))
        if len(self.pending) >= 256:
            self.flush()

    def flush(self):
        if self.pending:
            self.db.executemany('INSERT OR REPLACE INTO scores VALUES (?, ?, ?, ?, ?)', self.pending)
            self.db.commit()
            self.pending.clear()

    def close(self):
        try:
            self.flush()
        finally:
            self.db.close()
