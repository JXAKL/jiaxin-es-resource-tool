"""Bounded batch evidence, always derived from independently verified works."""
import re
from collections import defaultdict
from dataclasses import replace

from .evidence import words
from .model import select_match, title_core
from .names import CJK, clean_download_name


def credible_rival(candidate, candidates):
    return any(other.author.casefold() != candidate.author.casefold() and not other.conflict
               and (max(other.title_score, other.attachment_score) >= max(.90, candidate.title_score - .08)
                    or (other.verified and other.eligible and other.score >= candidate.score - .08))
               for other in candidates)


def author_support(matches):
    topics, names = defaultdict(set), defaultdict(set)
    for match in matches:
        if not match.selected or match.error or match.manual:
            continue
        best = next((c for c in match.candidates if c.author == match.author), None)
        if (not best or not best.verified or not best.eligible or best.conflict or best.author_bonus
                or credible_rival(best, match.candidates)):
            continue
        author = best.author.casefold()
        topics[author].add(best.topic_id)
        names[author].add(title_core(clean_download_name(match.group.title)))
    return {author: min(len(ids), len(names[author])) for author, ids in topics.items()}


def weighted_match(group, candidates, support, threshold):
    # A generic short title cannot acquire evidence simply because an author is common.
    names = [clean_download_name(n) for n in [group.title] + group.hints]
    distinctive = any(len([w for w in words(name) if not w.isdigit()]) >= 2
                      or re.search(rf'[{CJK}]{{6,}}', name) for name in names)
    adjusted = []
    for candidate in candidates:
        count = support.get(candidate.author.casefold(), 0)
        if (count >= 3 and distinctive and not candidate.verified and not candidate.conflict
                and candidate.title_score >= .95 and not credible_rival(candidate, candidates)):
            bonus = min(.22, .08 + .02 * count)
            score = min(.97, candidate.score + bonus)
            note = f'作者累计证据：{count} 部独立作品已核实，加权 +{(score - candidate.score) * 100:.1f} 分；此帖未单独读取'
            candidate = replace(candidate, score=score, author_support=count,
                                author_bonus=score - candidate.score,
                                reason=candidate.reason + '；' + note,
                                evidence=[*candidate.evidence, note])
        adjusted.append(candidate)
    adjusted.sort(key=lambda c: (-c.score, c.author.casefold(), c.topic_id))
    return select_match(group, adjusted, threshold)
