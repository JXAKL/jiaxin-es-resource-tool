"""Portable reports and download-ready domain lists for comprehensive organizing."""
import csv
import html
import io
import json
from pathlib import Path
from urllib.parse import urlsplit

from .collector import atomic_text
from .completion_export import line
from .downloads.model import normalize_url
from .resource_urls import resource_url as normalize
from .moves import safe_author
from .store import csv_cell
from .resource_info import RESOURCE_LABELS, filter_report_resources
from .resource_variants import version_summary


def export_report(state):
    state=filter_report_resources(state)
    folder=Path(state['report_folder'])
    lines=[f"作者：{state['author']}",f"状态：{state['status']}",f"输出目录：{state['output']}",
           '日期：帖子首次发布日期，统一按北京时间（UTC+08:00）划分年份和月份；不使用文件修改时间。',
           '只整理预览中勾选的本地文件；付费信息分别标记脚本和视频，未知不等于免费。',
           '第三方与合集链接属于帖子级线索；报告不下载或购买资源。',
           '配套按作品判断，保留全部差分版本和版本内轴文件；不同剪辑不保证播放时序相同。', '来源目录：']
    lines += [f"{s['path']} | {s['mode']} | {s['pairing']}"+(' | 以此为主，关联：'+s['partner'] if s.get('partner') else '') for s in state['sources']]
    buffer=io.StringIO(newline='');writer=csv.writer(buffer)
    writer.writerow(['本地名称','来源目录','配套情况','状态','已勾选','目标目录','所选帖子',
                     '帖子日期','匹配分','脚本付费','视频付费','本地文件','执行记录','关联目录','配套依据'])
    domains={}
    for row in state['rows']:
        chosen=next((c for c in row['candidates'] if c['id']==row['chosen']),None)
        lines += ['',f"【{row['completeness']}】{row['title']}",
                  f"状态：{row['status']} | {'勾选' if row['selected'] else '未勾选'} | {'手动核对' if row['manual'] else '自动匹配'}",
                  '目标：'+row['target'], '冲突：'+row.get('collision',''),
                  version_summary(row.get('versions', [])), '本地文件：']
        lines += [f['path'] for f in row['files']]
        if row.get('linked_source'):
            lines += ['配套目录：'+row['linked_source'],row.get('pairing_note','')]
        for c in row['candidates']:
            p=c['payment']
            lines += [f"{'所选帖' if c is chosen else '其他候选'} · {c['score']:.1f} 分 · {c['title']}",c['url'],
                      f"首次发布：{c['created_at'] or '未知'} | 北京时间：{c['date_local'] or '未知'} | 更新：{c['updated_at'] or '未知'}",
                      f"脚本：{p['script']}；视频：{p['video']}；标签：{p['tag']}",
                      c['reason'], *c['evidence'], *p['evidence'], '帖子内文件名：', *c['filenames']]
            if c['read_error']: lines.append('读取失败：'+c['read_error'])
            for resource in c['resources']:
                lines.append(RESOURCE_LABELS[resource['kind']]+' | '+resource.get('label','')+' | '+resource['url'])
                if c is not chosen: continue
                raw=normalize(resource['url'])
                if not raw: continue
                supported=normalize_url(raw) if resource['kind']!='purchase' else None
                url=supported or raw
                domain=(urlsplit(url).hostname or '').lower().removeprefix('www.')
                if not domain: continue
                category='下载用链接' if supported else '手动获取链接'
                domains.setdefault((category,domain),{})[url]=f"{row['title']} · {RESOURCE_LABELS[resource['kind']]} · {p['tag']} · {c['url']}"
        for operation in row['operations']:
            lines.append(f"执行 {operation['mode']} | {operation['status']} | {operation['source']} → {operation['target']} | SHA256 {operation['sha256']}")
        writer.writerow([csv_cell(v) for v in [row['title'],row['source'],row['completeness'],row['status'],
            row['selected'],row['target'], chosen['url'] if chosen else '',chosen['created_at'] if chosen else '',
            chosen['score'] if chosen else '',chosen['payment']['script'] if chosen else '',
            chosen['payment']['video'] if chosen else '', ' | '.join(f['path'] for f in row['files']),
            ' | '.join(f"{o['mode']} {o['status']} {o['target']}" for o in row['operations']),row.get('linked_source',''),row.get('pairing_note','')]])
    if state['failed']:
        lines += ['', '采集失败项（保留待重试）：', json.dumps(state['failed'],ensure_ascii=False,indent=2)]
    # This folder belongs to one analysis run; only remove files in our manifest.
    manifest=folder/'链接清单.json'
    try: previous=json.loads(manifest.read_text(encoding='utf-8'))
    except (ValueError,OSError): previous=[]
    files=[]
    for (category,domain),records in sorted(domains.items()):
        name=category+'/'+safe_author(domain)+'.txt';files.append(name)
        header=['# 资源综合整理 · '+category,'# 作者：'+line(state['author']),
                '# 仅包含已选择的对应帖子资源；每条链接独占一行，参数和密钥完整保留。',
                '# 在资源下载中导入本 TXT。' if category=='下载用链接' else '# 已识别的文件/网盘/视频资源；下载模块暂不支持，请在浏览器中获取。',
                '# 网盘/合集中的具体文件需核对；付费标签不表示已购买。','']
        for url,description in records.items(): header += ['# '+line(description),url,'']
        atomic_text(folder/name,'\n'.join(header))
    if isinstance(previous,list):
        for name in previous:
            if not isinstance(name,str) or name in files: continue
            parts=name.split('/')
            if len(parts)!=2 or parts[0] not in {'下载用链接','手动获取链接'} or '\\' in name or Path(parts[1]).name!=parts[1]: continue
            path=folder/name
            if path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(folder.resolve()): path.unlink()
    atomic_text(manifest,json.dumps(files,ensure_ascii=False))
    text='\n'.join(lines)
    atomic_text(folder/'资源综合整理报告.txt',text)
    atomic_text(folder/'资源综合整理报告.csv','\ufeff'+buffer.getvalue())
    atomic_text(folder/'资源综合整理报告.json',json.dumps(state,ensure_ascii=False,indent=2))
    atomic_text(folder/'资源综合整理报告.html','<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>资源综合整理报告</title>'
        '<style>body{background:#101113;color:#e6e8eb;font:15px system-ui;max-width:1100px;margin:auto;padding:28px}'
        'pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#1b1e22;border-radius:18px;padding:22px}</style>'
        '<h1>资源综合整理报告</h1><pre>'+html.escape(text)+'</pre></html>')
    return state
