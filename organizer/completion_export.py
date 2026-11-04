"""Download-ready annotated TXT exports, separate from the full report."""
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

from .collector import atomic_text
from .downloads.model import normalize_url
from .resource_urls import resource_url as normalize
from .moves import safe_author
from .resource_info import RESOURCE_LABELS, filter_report_resources


def line(value):
    return re.sub(r'\s+', ' ', str(value or '')).strip()


def export_download_text(state, folder):
    state = filter_report_resources(state, missing_only=True)
    options = {**dict(enabled=True, scope='all', include_review=False), **state.get('download_export', {})}
    if options['scope'] not in {'all', 'video', 'script'}:
        raise ValueError('下载链接范围无效')
    folder = Path(folder)
    manifest = folder / '下载链接导出清单.json'
    previous = []
    try:
        previous = json.loads(manifest.read_text(encoding='utf-8')).get('files', [])
    except (ValueError, OSError):
        pass
    groups = {}
    if options['enabled']:
        for row in state['rows']:
            if options['scope'] != 'all' and row['missing'] != options['scope']:
                continue
            for candidate in row['candidates']:
                if candidate.get('read_error') or (not candidate['confirmed'] and not options['include_review']):
                    continue
                for resource in candidate['resources']:
                    if resource['kind'] not in {row['missing'], 'shared', 'unknown'}:
                        continue
                    raw = normalize(resource['url'])
                    if not raw:
                        continue
                    supported = normalize_url(raw) if resource['kind'] != 'purchase' else None
                    url = supported or raw
                    domain = (urlsplit(url).hostname or '').lower().removeprefix('www.')
                    category = '下载用链接' if supported else '手动获取链接'
                    records = groups.setdefault((category, domain), {})
                    record = records.setdefault(url, dict(url=url, sources=[], seen=set()))
                    source = dict(title=row['title'], missing=row['missing_label'],
                                  confirmed=candidate['confirmed'], post=candidate['url'],
                                  payment=candidate['payment']['tag'], kind=RESOURCE_LABELS[resource['kind']])
                    identity = tuple(source.values())
                    if identity not in record['seen']:
                        record['sources'].append(source)
                        record['seen'].add(identity)
    files, counts = [], {'download': 0, 'manual': 0}
    scope = {'all':'全部缺失组', 'video':'仅缺视频', 'script':'仅缺脚本'}[options['scope']]
    for (category, domain), records in sorted(groups.items()):
        relative = category + '/' + safe_author(domain) + '.txt'
        files.append(relative)
        is_download = category == '下载用链接'
        counts['download' if is_download else 'manual'] += len(records)
        header = [f'# 资源补齐 · {category}', f"# 作者：{line(state['author'])}",
                  f'# 域名：{domain}；去重链接：{len(records)}；范围：{scope}',
                  f"# 结果状态：{line(state['status'])}",
                  '# 用法：在“资源下载”点击“导入链接/采集”选择本文件，再检查队列并开始下载。' if is_download
                  else '# 用法：本文件为已识别的文件/网盘/视频资源，下载模块暂不支持，请在浏览器中获取。',
                  '# 以 # 开头的是说明，下载模块会忽略。每条资源链接独占一行，参数与密钥完整保留。',
                  '# 包含待核对候选，请核实对应关系后下载。' if options['include_review'] else '# 仅包含达到匹配阈值的对应帖子资源。',
                  '# 网盘或合集是帖子级链接，具体文件仍需核对；付费标签不表示已购买或保证可下载。', '']
        for record in records.values():
            for source in record['sources']:
                header += [f"# {line(source['missing'])} · {line(source['title'])} · {'对应' if source['confirmed'] else '待核对'} · {line(source['payment'])} · {line(source['kind'])}",
                           '# 来源帖子：' + line(source['post'])]
            header += [record['url'], '']
        atomic_text(folder / relative, '\n'.join(header))
    # Re-export updates only files listed in our own manifest. User files stay.
    if isinstance(previous, list):
        for name in previous:
            if not isinstance(name, str) or name in files:
                continue
            parts = name.split('/')
            if len(parts) != 2 or parts[0] not in {'下载用链接','手动获取链接'} or Path(parts[1]).name != parts[1] or '\\' in name or Path(parts[1]).suffix != '.txt':
                continue
            path = folder / name
            if path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(folder.resolve()):
                path.unlink()
    result = dict(options=options, files=files, **counts)
    atomic_text(manifest, json.dumps(result, ensure_ascii=False, indent=2))
    return result
