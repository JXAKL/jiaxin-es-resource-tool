import random
import unittest
from dataclasses import replace
from unittest.mock import Mock, patch
from organizer.model import FileGroup, Topic, TopicIndex
from organizer.matching import MatchingSession


class RetrievalInvalidationTests(unittest.TestCase):
    def test_body_update_keeps_unrelated_shortlist_cached(self):
        topics=[Topic(1,'Moonlit Harbor','A','',body='Moonlit Harbor Extra'),
                Topic(2,'Zebra Quantum','B','',body='Zebra Quantum Workshop')]
        index=TopicIndex(topics)
        groups=[FileGroup('a','Moonlit Harbor Extra'),FileGroup('b','Zebra Quantum Workshop')]
        for group in groups: index.shortlist(group)
        key=groups[1].title,(),()
        cached=index._shortlist_cache[key]
        new={t.id:t for t in [replace(topics[0],body='Moonlit Harbor Another'),topics[1]]}
        index.update(new,{1})
        self.assertIs(index._shortlist_cache[key],cached)
        self.assertEqual(index.shortlist(groups[1]),TopicIndex(list(new.values())).shortlist(groups[1]))

    def test_retrieval_and_scores_use_fresh_evidence_without_evicting_title_lookup(self):
        group=FileGroup('a','Moonlit Harbor')
        topic=Topic(1,'Moonlit Harbor','A','')
        index=TopicIndex([topic]); index.shortlist(group)
        cached=index._shortlist_cache[(group.title,(),())]
        new=replace(topic,details_loaded=True)
        index.update({1:new},{1})
        self.assertIs(index._shortlist_cache[(group.title,(),())],cached)
        self.assertIs(index.shortlist(group)[0],new)

    def test_many_incremental_mutations_equal_full_rebuild(self):
        rng=random.Random(9411)
        words=['quartz','harbor','midnight','zephyr','lotus','violet','dawn','cloud','aurora','quantum']
        def phrase(): return ' '.join(rng.sample(words,3))
        topics={i:Topic(i,phrase(),str(i%4),'',body=phrase()+'\n'+phrase(),source_ids=[f'fixture{i}']) for i in range(1,81)}
        groups=[FileGroup(str(i),phrase(),hints=[phrase()] if i%3==0 else [],topic_ids=[i] if i%7==0 else []) for i in range(1,61)]
        groups.extend([FileGroup('unicode','星空の舞踏会'),FileGroup('id','video fixture11')])
        index=TopicIndex(list(topics.values()))
        for step in range(30):
            for group in groups: index.shortlist(group)
            identity=rng.choice(list(topics)); old=topics[identity]
            mode=step%6
            if mode==0: topics[identity]=replace(old,body=phrase()+'\n星空の舞踏会')
            elif mode==1: topics[identity]=replace(old,title=phrase())
            elif mode==2: topics[identity]=replace(old,filenames=[phrase()+'.funscript'])
            elif mode==3: topics[identity]=replace(old,source_ids=['fixture11'])
            elif mode==4: del topics[identity]
            else:
                identity=1000+step; topics[identity]=Topic(identity,phrase(),'New','',body=phrase())
            index.update(dict(topics),{identity})
            rebuilt=TopicIndex(list(topics.values()))
            for group in groups:
                self.assertEqual([t.id for t in index.shortlist(group)],[t.id for t in rebuilt.shortlist(group)],(step,group.key))

    def test_idle_workers_released_without_losing_results(self):
        matcher=MatchingSession(workers=4)
        group=FileGroup('a','Moonlit Harbor')
        matcher.update([Topic(1,'Moonlit Harbor','A','',body='Moonlit Harbor',details_loaded=True)])
        expected=matcher.refresh([group]); pool=Mock(); cancel=Mock()
        matcher._pool,matcher._cancel=pool,cancel
        matcher._pool_workers=4; matcher._last_parallel=0
        with patch('organizer.matching.time.monotonic',return_value=60):
            matcher._score_jobs([],lambda:None,lambda *_:None)
        pool.shutdown.assert_called_once_with(wait=True,cancel_futures=True)
        self.assertIsNone(matcher._pool)
        self.assertEqual(matcher.refresh([group]),expected)


if __name__=='__main__': unittest.main()
