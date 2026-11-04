"""First-post resource context and explainable payment labels. No link visits."""
import re
from html.parser import HTMLParser
from urllib.parse import urlsplit
from .resource_urls import (PURCHASE_HOSTS, resource_url, address_kind, text_urls, BASE)

RESOURCE_LABELS = {'script': '脚本', 'video': '视频', 'shared': '视频/脚本',
                   'unknown': '类型待核对', 'purchase': '购买/订阅入口'}
SCRIPT = re.compile(r'\bfunscripts?\b|\bscripts?\b|脚本', re.I)
VIDEO = re.compile(r'\bvideos?\b|\bmovies?\b|\bclips?\b|视频|影片', re.I)
PAID = re.compile(r'\bpaid\b|\bpurchase\b|\bfor sale\b|\bbuy\b|\brequires? (?:a )?subscription\b|\bsubscription[- ]only\b|\bpatreon[- ]exclusive\b|\bnot free\b|付费|付費|收费|收費|购买|購買|会员专享|有料', re.I)
FREE = re.compile(r'\bfree\b|\bno charge\b|\bnot paid\b|免费|免費|無料', re.I)


def purchase_host(url):
    host = (urlsplit(resource_url(url) or '').hostname or '').casefold()
    return any(host == name or host.endswith('.' + name) for name in PURCHASE_HOSTS)


class ResourceParser(HTMLParser):
    def __init__(self, base):
        super().__init__(convert_charrefs=True)
        self.base, self.ignored, self.parts, self.anchors, self.result = base, [], [], [], []
        self.anchor = None

    def flush(self):
        context = re.sub(r'\s+', ' ', ''.join(self.parts)).strip()[:800]
        for row in self.anchors:
            self.result.append(dict(url=row['url'], label=''.join(row['parts']).strip()[:300], context=context))
        self.parts, self.anchors = [], []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        void = tag in {'br', 'hr', 'img', 'input', 'meta', 'link', 'source', 'wbr'}
        if self.ignored or tag in {'blockquote', 'script', 'style'} or (tag == 'aside' and 'quote' in attrs.get('class', '').split()):
            if not void:
                self.ignored.append(tag)
            return
        if tag in {'p', 'div', 'li', 'br', 'h1', 'h2', 'h3', 'h4', 'tr'}:
            self.flush()
        if tag == 'a':
            link = resource_url(attrs.get('href') or '', self.base)
            if link:
                self.anchor = dict(url=link, parts=[])
                self.anchors.append(self.anchor)

    def handle_endtag(self, tag):
        if self.ignored:
            if tag in self.ignored:
                del self.ignored[len(self.ignored) - self.ignored[::-1].index(tag) - 1:]
            return
        if tag == 'a':
            self.anchor = None
        if tag in {'p', 'div', 'li', 'h1', 'h2', 'h3', 'h4', 'tr'}:
            self.flush()

    def handle_data(self, text):
        if self.ignored:
            return
        self.parts.append(text)
        if self.anchor is not None:
            self.anchor['parts'].append(text)
        else:
            for url in text_urls(text):
                self.anchors.append(dict(url=url, parts=[url]))


def extract_resources(cooked, base):
    parser = ResourceParser(base)
    parser.feed(cooked)
    parser.flush()
    seen, result = set(), []
    for row in parser.result:
        identity = (row['url'], row['label'], row['context'])
        if identity not in seen:
            seen.add(identity)
            result.append(row)
    return result


def resource_kind(row):
    label = row.get('label', '')
    address = address_kind(row['url'], label)
    if address in {'script','video'}:return address
    if not address:return None
    # Link labels are more specific than a paragraph containing several links.
    for text in (label, row.get('context', '')):
        s, v = bool(SCRIPT.search(text)), bool(VIDEO.search(text))
        if s or v:
            return 'shared' if s and v else 'script' if s else 'video'
    return 'unknown'


def resource_links(topic):
    rows = list(topic.resource_links)
    known = {r['url'] for r in rows}
    rows += [dict(url=url, label='', context='') for url in topic.links if url not in known]
    rows += [dict(url=url,label='',context='') for url in text_urls(topic.body)]
    return filter_resource_rows(rows, topic.url or BASE)


def filter_resource_rows(rows, base=BASE):
    """Revalidate old cache/report rows too; an old 'script' kind is not proof."""
    result = {}
    for row in rows:
        if not isinstance(row,dict):continue
        url = resource_url(row.get('url', ''),base)
        if not url:continue
        row=dict(row, label=row.get('label') if isinstance(row.get('label'),str) else '',
                 context=row.get('context') if isinstance(row.get('context'),str) else '')
        kind = resource_kind(dict(row, url=url))
        if not kind:continue
        # Earlier versions may only have saved a side label for a valid share.
        if kind=='unknown' and row.get('kind') in {'script','video','shared'}:kind=row['kind']
        old=result.get(url)
        if old:
            kinds={old['kind'],kind}-{'unknown'}
            old['kind']='shared' if 'shared' in kinds or {'script','video'}<=kinds else next(iter(kinds),'unknown')
        else:result[url]=dict(row,url=url,kind=kind)
    return list(result.values())


def filter_report_resources(state, *, missing_only=False):
    """A copy, so exports cannot alter the user's candidate/selection decisions."""
    rows=[];removed=0;kept=0
    for row in state['rows']:
        candidates=[]
        for candidate in row['candidates']:
            resources=filter_resource_rows(candidate.get('resources',[]),candidate.get('url') or BASE)
            if missing_only:
                resources=[r for r in resources if r['kind'] in {row['missing'],'shared','unknown'}]
            removed+=max(0,len(candidate.get('resources',[]))-len(resources));kept+=len(resources)
            candidates.append(dict(candidate,resources=resources))
        rows.append(dict(row,candidates=candidates))
    return dict(state,rows=rows,resource_filter=dict(version='2.1.15',kept=kept,removed=removed))


def payment_info(topic, category_name=''):
    found = {'script': set(), 'video': set(), 'post': set()}
    evidence = []
    fields = [('分类', category_name or topic.category_name), ('标题', topic.title)]
    fields += [('标签', tag) for tag in topic.tags]
    fields += [('首帖', line.strip()) for line in re.split(r'[\n.!?。！？;；]+', topic.body) if line.strip()]
    for source, text in fields:
        # Restrict body claims to explicit resource statements. A creator's
        # generic donation/signature link is not proof that this work is paid.
        normalized = text.replace('_', ' ') if source in {'分类', '标签'} else text
        lowered = re.sub(r'\bnot paid\b|不收费|不收費|无需付费|無需付費', ' free ', normalized, flags=re.I)
        lowered = re.sub(r'\bnot free\b|非免费|非免費', ' paid ', lowered, flags=re.I)
        paid, free = bool(PAID.search(lowered)), bool(FREE.search(lowered))
        sides = [side for side, pattern in (('script', SCRIPT), ('video', VIDEO)) if pattern.search(text)]
        if not (paid or free) or (source == '首帖' and not sides):
            continue
        # Split contrasting clauses so "free video, paid script" is not both.
        clauses = re.split(r'[,，]|\bbut\b|\bwhile\b|但是|但', lowered, flags=re.I)
        if paid and free:
            clauses = [part for clause in clauses for part in re.split(r'\band\b|而|、', clause, flags=re.I)]
        for clause in clauses:
            targets = [side for side, pattern in (('script', SCRIPT), ('video', VIDEO)) if pattern.search(clause)]
            if not targets and len(clauses) == 1:
                targets = ['post']
            for kind, pattern in (('付费', PAID), ('免费', FREE)):
                if pattern.search(clause):
                    for target in targets:
                        found[target].add(kind)
        evidence.append(f'{source}：{text[:300]}')
    def label(values):
        return '混合/需核实' if len(values) > 1 else next(iter(values)) if values else '未知'
    sides = {key: label(values) for key, values in found.items()}
    if found['script'] == {'付费'} and found['video'] == {'付费'}:
        tag = '视频与脚本付费'
    elif any('付费' in values for values in found.values()):
        tag = '付费（含混合证据）' if any(len(v) > 1 for v in found.values()) else '付费'
    elif any(purchase_host(url) for url in topic.links) or any(purchase_host(r['url']) for r in topic.resource_links):
        tag = '含购买/订阅入口，付费范围待核实'
    elif any('免费' in values for values in found.values()):
        tag = '含免费说明'
    else:
        tag = '付费状态未知'
    return dict(tag=tag, **sides, evidence=list(dict.fromkeys(evidence)))
