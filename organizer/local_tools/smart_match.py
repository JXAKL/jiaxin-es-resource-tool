from .widgets import ttk
from .surfaces import create_surface
from ..presentation import paint_items, poll_delay
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import csv
import json
import math
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox
from .name_match import name_similarity, normalize_name, profile, compare_profiles, compare_names
from .pairing import match_records, select_confident
from ..model import file_title
from .script_intensity import (grade_rows, grade_batch, apply_relative, DEFAULT_AVG_REFERENCE,
                              DEFAULT_MAX_REFERENCE, DEFAULT_MAX_GAP, RELATIVE_VERSION)


VIDEO_EXTENSIONS = {
    ".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm", ".m4v", ".ts"
}

NAME_WEIGHT = 0.40
DURATION_WEIGHT = 0.60


def collect_files(root, extensions):
    if not root or not os.path.isdir(root):
        return []

    result = []
    for dirpath, _dirnames, filenames in os.walk(root):
        for filename in filenames:
            ext = Path(filename).suffix.lower()
            if ext in extensions:
                result.append(os.path.join(dirpath, filename))
    return result


def parse_funscript(path):
    try:
        with open(path, "r", encoding="utf-8-sig") as handle:
            data = json.load(handle)
    except Exception:
        return None

    if not isinstance(data, dict):
        return None
    actions = data.get("actions") or []
    if not isinstance(actions, list):
        return None
    last_at = None
    if actions:
        last_at = max((a.get('at', 0) for a in actions if isinstance(a, dict)
                       and isinstance(a.get('at'), (int, float)) and math.isfinite(a['at'])), default=None)

    metadata = data.get("metadata") or {}
    if not isinstance(metadata, dict):
        metadata = {}
    raw_duration = metadata.get("duration")
    duration = None
    if raw_duration not in (None, ""):
        try:
            duration = float(raw_duration)
            if not math.isfinite(duration) or duration <= 0:
                duration = None
        except (TypeError, ValueError):
            duration = None

    duration_source = 'metadata' if duration is not None else 'unknown'
    if duration is None and last_at is not None:
        try:
            duration = float(last_at) / 1000.0
            duration_source = 'actions'
        except (TypeError, ValueError):
            duration = None

    return {
        "path": path,
        "duration": duration,
        "duration_source": duration_source,
        "last_at": last_at,
        "actions": len(actions),
        "title": metadata.get("title"),
        "original_name": metadata.get('original_name'),
        "author": metadata.get("author"),
        "url": metadata.get("url"),
    }


def get_video_durations(paths):
    if not paths:
        return {}

    powershell = shutil.which("powershell.exe") or shutil.which("pwsh.exe")
    if not powershell:
        powershell = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"

    temp_files = []
    try:
        json_fd, json_path = tempfile.mkstemp(prefix="funscript_paths_", suffix=".json")
        os.close(json_fd)
        temp_files.append(json_path)

        with open(json_path, "w", encoding="utf-8-sig") as handle:
            json.dump(paths, handle, ensure_ascii=False)

        out_fd, out_path = tempfile.mkstemp(prefix="funscript_durations_", suffix=".json")
        os.close(out_fd)
        temp_files.append(out_path)

        ps_script = r'''
param(
    [string]$JsonPath,
    [string]$OutPath
)

$ErrorActionPreference = "SilentlyContinue"
$rawPaths = Get-Content -LiteralPath $JsonPath -Raw -Encoding UTF8 | ConvertFrom-Json
$paths = @()
if ($rawPaths -is [System.Collections.IEnumerable] -and $rawPaths -isnot [string]) {
    foreach ($item in $rawPaths) {
        $paths += [string]$item
    }
}
else {
    $paths += [string]$rawPaths
}
$shell = New-Object -ComObject Shell.Application
$lengthIndex = 27

foreach ($path in $paths) {
    $dir = Split-Path -Parent $path
    if (Test-Path -LiteralPath $dir -PathType Container) {
        $folder = $shell.Namespace($dir)
        if ($folder) {
            for ($i = 0; $i -lt 320; $i++) {
                $name = $folder.GetDetailsOf($null, $i)
                if ($name -and $name -match "Length|Duration|时长") {
                    $lengthIndex = $i
                    break
                }
            }
            break
        }
    }
}

$result = @()
foreach ($path in $paths) {
    $seconds = $null
    try {
        $dir = Split-Path -Parent $path
        $leaf = Split-Path -Leaf $path
        $folder = $shell.Namespace($dir)
        if ($folder) {
            $item = $folder.ParseName($leaf)
            if ($item) {
                $value = $folder.GetDetailsOf($item, $lengthIndex)
                if ($value -and $value -match "^\d{1,3}:\d{2}:\d{2}") {
                    $seconds = [math]::Round([timespan]::Parse($value).TotalSeconds, 3)
                }
            }
        }
    }
    catch {
        $seconds = $null
    }
    $result += [PSCustomObject]@{
        Path     = [string]$path
        Duration = $seconds
    }
}

$result | ConvertTo-Json -Compress | Set-Content -LiteralPath $OutPath -Encoding UTF8
'''

        ps_fd, ps_path = tempfile.mkstemp(prefix="funscript_duration_", suffix=".ps1")
        with os.fdopen(ps_fd, "w", encoding="utf-8-sig") as handle:
            handle.write(ps_script)
        temp_files.append(ps_path)

        creationflags = 0
        if os.name == "nt":
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

        subprocess.run(
            [
                powershell,
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                ps_path,
                "-JsonPath",
                json_path,
                "-OutPath",
                out_path,
            ],
            creationflags=creationflags,
            timeout=600,
        )

        if not os.path.exists(out_path):
            return {}

        with open(out_path, "r", encoding="utf-8-sig") as handle:
            data = json.load(handle)
        if isinstance(data, dict):
            data = [data]

        result = {}
        for item in data:
            if isinstance(item, dict) and "Path" in item:
                result[str(item["Path"])] = item.get("Duration")
        return result
    except Exception:
        return {}
    finally:
        for path in temp_files:
            try:
                os.remove(path)
            except OSError:
                pass


def duration_score(video_duration, script_duration):
    if video_duration is None or script_duration is None:
        return 0.0
    if video_duration <= 0 or script_duration <= 0:
        return 0.0

    difference = abs(video_duration - script_duration)
    tolerance = max(video_duration, script_duration) * 0.05
    if tolerance <= 0:
        return 1.0 if difference < 0.001 else 0.0

    return max(0.0, 1.0 - difference / tolerance)


def match_files(
    video_dir,
    funscript_dir,
    high_threshold,
    low_threshold,
    use_name=True,
    use_duration=True,
):
    if not use_name and not use_duration:
        raise ValueError("至少需要勾选一种匹配方式。")

    video_paths = collect_files(video_dir, VIDEO_EXTENSIONS)
    funscript_paths = collect_files(funscript_dir, {".funscript"})

    videos = []
    durations = get_video_durations(video_paths) if use_duration else {}
    for path in video_paths:
        videos.append({
            "path": path,
            "name": os.path.basename(path),
            "duration": durations.get(path),
            "name_profile": profile(path),
        })

    funscripts = []
    for path in funscript_paths:
        info = parse_funscript(path)
        if info:
            info["name"] = os.path.basename(path)
            info["size"] = os.path.getsize(path)
            info['name_profile'] = profile(path)
            funscripts.append(info)

    return match_records(videos, funscripts, high_threshold, low_threshold, use_name, use_duration)



def write_csv(rows, output_path):
    with open(output_path, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow([
            "funscript",
            "video",
            "文件名相似度%",
            "时长相似度%",
            "综合得分%",
            "视频时长秒",
            "脚本时长秒",
            "状态",
            "运动强度等级", "运动强度分（非匹配分）", "全片平均速度（位置点/秒）", "最大速度（位置点/秒）",
            "动作点数", "有效移动段数", "方向反转次数", "评分时长秒", "评分时长来源",
            "平均速度参考值", "最大速度参考值", "评分版本", "评分说明或错误", "输出路径",
            "有效运动平均速度（位置点/秒）", "有效运动时长秒", "运动时间占比%", "排除时长秒",
            "疑似空白长间隔秒", "疑似空白长间隔数", "长间隔阈值秒（0关闭）",
            "评分模式", "绝对强度分", "绝对强度等级", "平均速度批内百分位", "最大速度批内百分位",
            "批内有效脚本数", "评分批次标识",
            "名称评分依据", "次选脚本", "首选组与次选组分差",
            "时长依据", "可自动勾选", "已勾选", "脚本组文件", "脚本输出路径", "处理错误", "整理提示",
        ])
        for row in rows:
            intensity = row.get('intensity', {})
            writer.writerow([
                row["funscript"],
                row["video"],
                f"{row['name_score']:.1f}",
                f"{row['duration_score']:.1f}",
                f"{row['total']:.1f}",
                "" if row["video_duration"] is None else f"{row['video_duration']:.2f}",
                "" if row["script_duration"] is None else f"{row['script_duration']:.2f}",
                row["status"],
                intensity.get('folder', ''), intensity.get('score', ''),
                intensity.get('average', ''), intensity.get('maximum', ''),
                intensity.get('action_count', ''), intensity.get('movement_count', ''), intensity.get('reversals', ''),
                intensity.get('duration', ''), intensity.get('duration_source', ''),
                intensity.get('avg_reference', ''), intensity.get('max_reference', ''), intensity.get('version', ''),
                intensity.get('error') or intensity.get('notes', ''), row.get('output_path', ''),
                intensity.get('active_average', ''), intensity.get('active_duration', ''),
                intensity['coverage']*100 if 'coverage' in intensity else '', intensity.get('excluded_duration', ''),
                intensity.get('gap_duration', ''), intensity.get('gap_count', ''), intensity.get('max_gap', ''),
                intensity.get('basis', ''), intensity.get('absolute_score', ''), intensity.get('absolute_folder', ''),
                intensity.get('average_percentile', ''), intensity.get('maximum_percentile', ''),
                intensity.get('cohort_count', ''), intensity.get('cohort_id', ''),
                row.get('name_reason', ''), row.get('runner_up', ''), row.get('candidate_margin', ''),
                row.get('duration_reason', ''), bool(row.get('auto_eligible')), bool(row.get('selected')),
                ' | '.join(row.get('script_paths', [])),
                ' | '.join(row.get('output_script_paths', [])), row.get('process_error', ''),
                row.get('output_note', ''),
            ])


def safe_folder_name(name):
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip().rstrip(".")
    return name or "视频"


def unique_subfolder(parent, base_name):
    base_name = safe_folder_name(base_name)
    candidate = os.path.join(parent, base_name)
    counter = 1
    while os.path.exists(candidate):
        candidate = os.path.join(parent, f"{base_name}_{counter}")
        counter += 1
    return candidate


def process_match(row, output_dir, move=False, overwrite=False, classify=False,
                  avg_reference=DEFAULT_AVG_REFERENCE, max_reference=DEFAULT_MAX_REFERENCE,
                  max_gap=DEFAULT_MAX_GAP, cohort=None, include_scripts=False, separate_folders=False,
                  moved_scripts=None):
    video_path = row.get("video_path")
    funscript_path = row.get("funscript_path")
    if not video_path or not funscript_path:
        raise ValueError("匹配结果缺少视频或 funscript 路径。")

    if not output_dir:
        raise ValueError("输出文件夹不能为空。")

    if classify:
        # Re-read immediately before filing, so changed scripts are never graded from stale data.
        from .pair_output import available_script
        refreshed = {**row, 'funscript_path': str(available_script(funscript_path, moved_scripts or {}))}
        grade_rows([refreshed], avg_reference, max_reference, max_gap)
        row['intensity'] = refreshed['intensity']
        if cohort is not None:
            apply_relative(row, cohort, strict=True)
        output_dir = os.path.join(output_dir, row['intensity']['folder'])
    if include_scripts:
        from .pair_output import output_group
        return output_group(row, output_dir, move=move, overwrite=overwrite,
                            separate=separate_folders, moved=moved_scripts)
    os.makedirs(output_dir, exist_ok=True)

    script_stem = file_title(Path(funscript_path))
    video_stem = Path(video_path).stem
    destination_name = script_stem + Path(video_path).suffix

    if row.get("multi_match"):
        parent_dir = os.path.join(output_dir, script_stem)
        destination_dir = unique_subfolder(parent_dir, video_stem)
        destination = os.path.join(destination_dir, destination_name)
    else:
        destination = os.path.join(output_dir, destination_name)
        if os.path.normcase(os.path.abspath(video_path)) == os.path.normcase(os.path.abspath(destination)):
            row['output_path'] = destination
            return destination
        if not overwrite and os.path.exists(destination):
            destination_dir = unique_subfolder(output_dir, video_stem)
            destination = os.path.join(destination_dir, destination_name)

    os.makedirs(os.path.dirname(destination), exist_ok=True)

    if os.path.abspath(video_path) == os.path.abspath(destination):
        row['output_path'] = destination
        return destination

    if move:
        if overwrite and os.path.exists(destination):
            os.remove(destination)
        shutil.move(video_path, destination)
    else:
        shutil.copy2(video_path, destination)

    row['output_path'] = destination
    return destination


class SmartMatcherApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Funscript 智能匹配工具")
        self.root.geometry("1040x680")
        self.root.minsize(900, 600)

        self.video_dir = tk.StringVar()
        self.funscript_dir = tk.StringVar()
        self.output_dir = tk.StringVar()
        self.output_csv = tk.StringVar()
        self.high_threshold = tk.StringVar(value="95")
        self.low_threshold = tk.StringVar(value="80")
        self.operation_mode = tk.StringVar(value="copy")
        self.auto_high = tk.BooleanVar(value=False)
        self.auto_select = tk.BooleanVar(value=True)
        self.overwrite = tk.BooleanVar(value=False)
        self.include_scripts = tk.BooleanVar(value=False)
        self.separate_folders = tk.BooleanVar(value=False)
        self.output_hint = tk.StringVar()
        self.use_name = tk.BooleanVar(value=True)
        self.use_duration = tk.BooleanVar(value=True)
        self.classify_intensity = tk.BooleanVar(value=True)
        self.avg_reference = DEFAULT_AVG_REFERENCE
        self.max_reference = DEFAULT_MAX_REFERENCE
        self.max_gap = DEFAULT_MAX_GAP
        self.relative_mode = True
        self.cohort = None
        self.status = tk.StringVar(value="就绪")
        self.message_queue = queue.Queue()
        self.running = False
        self.rows = []
        self.item_rows = {}

        self.build_ui()
        for variable in (self.include_scripts, self.separate_folders, self.operation_mode, self.overwrite):
            variable.trace_add('write', self.sync_output_options)
        self.sync_output_options()
        self.root.protocol('WM_DELETE_WINDOW', self.close)
        self.root.after(100, self.process_queue)

    def build_ui(self):
        from ..local_ui import build_pair
        build_pair(self)

    def update_summary(self):
        count = sum(bool(row.get('selected')) for row in self.rows)
        mode = f" · 批内相对：{self.cohort['count']} 份脚本" if self.cohort is not None else ' · 绝对强度'
        ready = sum(bool(row.get('auto_eligible')) and row.get('status') != '已处理' for row in self.rows)
        self.selection_summary.set(f'{len(self.rows)} 个候选 · 高置信 {ready} · 已勾选 {count}' + mode)
        self.layout.stats(len(self.rows), count, sum(row.get('status') == '已处理' for row in self.rows))

    def show_details(self, event=None):
        selected = self.tree.selection()
        if selected:
            row = self.item_rows.get(selected[0], {})
            intensity = row.get('intensity', {})
            stats = ''
            if intensity.get('score') is not None:
                stats = (f"\n运动强度：{intensity['score']:.2f} / 100 · {intensity['folder'][3:]}"
                         f" · {intensity.get('basis', '绝对强度')}"
                         + (f"（绝对分 {intensity['absolute_score']:.2f}，本批 {intensity['cohort_count']} 份）"
                            if intensity.get('cohort_count') is not None else '')
                         +
                         f" · 运动段平均 {intensity['active_average']:.2f} / 全片平均 {intensity['average']:.2f}"
                         f" / 最大 {intensity['maximum']:.2f} 位置点/秒"
                         f"\n有效运动 {intensity['active_duration']:.2f} 秒（占 {intensity['coverage']:.1%}）"
                         f" · 动作点 {intensity['action_count']} · 移动段 {intensity['movement_count']}"
                         f" · 反转 {intensity['reversals']}" + (f"\n{intensity['notes']}" if intensity.get('notes') else ''))
            elif intensity.get('error'):
                stats = '\n无法评估：' + intensity['error']
            self.details.set(f"视频：{row.get('video_path', '')}\n脚本：{row.get('funscript_path', '')}"
                             + (f"\n名称：{row['name_reason']}" if row.get('name_reason') else '')
                             + (f"\n时长：{row['duration_reason']}" if row.get('duration_reason') else '')
                             + (f"\n脚本组：{len(row['script_paths'])} 份（多轴一起比较）" if len(row.get('script_paths', [])) > 1 else '')
                             + (f"\n次选：{row['runner_up']}（首选组领先 {row['candidate_margin']:.1f} 分）"
                                if row.get('runner_up') else '')
                             + stats
                             + (f"\n整理到：{row['output_path']}" if row.get('output_path') else '')
                             + (f"\n配套脚本：{' | '.join(row['output_script_paths'])}" if row.get('output_script_paths') else '')
                             + (f"\n提示：{row['output_note']}" if row.get('output_note') else '')
                             + (f"\n错误：{row['process_error']}" if row.get('process_error') else ''))

    def intensity_settings(self):
        if self.running:
            return
        if self.intensity_panel.winfo_manager():
            self.intensity_panel.pack_forget()
        else:
            self.intensity_panel.pack(fill='x', pady=8)

    def choose_video_dir(self):
        path = filedialog.askdirectory(title="请选择视频文件夹")
        if path:
            self.video_dir.set(path)

    def choose_funscript_dir(self):
        path = filedialog.askdirectory(title="请选择 funscript 文件夹")
        if path:
            self.funscript_dir.set(path)

    def choose_output_dir(self):
        path = filedialog.askdirectory(title="请选择输出文件夹")
        if path:
            self.output_dir.set(path)

    def choose_output_csv(self):
        path = filedialog.asksaveasfilename(
            title="保存匹配结果",
            defaultextension=".csv",
            filetypes=[("CSV 文件", "*.csv")],
        )
        if path:
            self.output_csv.set(path)

    def sync_output_options(self, *_):
        enabled = self.include_scripts.get()
        if not enabled and self.separate_folders.get():
            self.separate_folders.set(False)
        self.include_scripts_switch.configure(state='disabled' if self.running else 'normal')
        self.separate_folders_switch.configure(state='normal' if enabled and not self.running else 'disabled')
        action = '移动' if self.operation_mode.get() == 'move' else '复制'
        scope = '视频和整套配对脚本' if enabled else '视频，脚本留在原处'
        location = '；每组放入独立文件夹' if enabled and self.separate_folders.get() else ''
        conflict = '覆盖目标同名文件' if self.overwrite.get() else '同名冲突时整组另建文件夹'
        self.output_hint.set(f'{action}{scope}{location}。{conflict}。')
        self.process_button.configure(text=f'{action}勾选组' if enabled else f'{action}勾选视频')

    def set_buttons(self, running):
        self.running = running
        state = "disabled" if running else "normal"
        self.start_button.configure(state=state)
        self.process_button.configure(state=state)
        self.intensity_button.configure(state=state)
        self.select_high_button.configure(state=state)
        self.auto_select_switch.configure(state=state)
        for control in self.output_controls:
            control.configure(state=state)
        self.sync_output_options()
        if running:
            self.progress.start(12)
        else:
            self.progress.stop()

    def start_analysis(self):
        if self.running:
            return

        video_dir = self.video_dir.get().strip()
        funscript_dir = self.funscript_dir.get().strip()
        output_dir = self.output_dir.get().strip()
        output_csv = self.output_csv.get().strip()

        if not os.path.isdir(video_dir) or not os.path.isdir(funscript_dir):
            messagebox.showwarning("提示", "请选择有效的视频目录和 Funscript 目录。")
            return

        if self.auto_high.get() and not output_dir:
            messagebox.showwarning("提示", "勾选了自动处理高置信度，请先选择输出文件夹。")
            return

        if not self.use_name.get() and not self.use_duration.get():
            messagebox.showwarning("提示", "至少需要勾选一种匹配方式：按名称匹配或按时间轴/时长匹配。")
            return

        try:
            high = float(self.high_threshold.get())
            low = float(self.low_threshold.get())
        except ValueError:
            messagebox.showwarning("提示", "阈值必须是数字。")
            return

        if not (0 <= low <= high <= 100):
            messagebox.showwarning("提示", "阈值范围不正确，需要 0 <= 最低阈值 <= 高置信阈值 <= 100。")
            return

        self.set_buttons(True)
        self.populate_tree([])
        self.status.set("正在分析...")

        thread = threading.Thread(
            target=self.worker,
            args=(
                video_dir,
                funscript_dir,
                output_dir,
                output_csv,
                high,
                low,
                self.use_name.get(),
                self.use_duration.get(),
                self.auto_high.get(),
                self.operation_mode.get() == 'move',
                self.overwrite.get(),
                self.classify_intensity.get(), self.avg_reference, self.max_reference, self.max_gap, self.relative_mode,
                self.auto_select.get(),
                self.include_scripts.get(), self.separate_folders.get(),
            ),
            daemon=True,
        )
        thread.start()

    def worker(self, video_dir, funscript_dir, output_dir, output_csv, high, low, use_name, use_duration,
               auto_high, move, overwrite, classify=False,
               avg_reference=DEFAULT_AVG_REFERENCE, max_reference=DEFAULT_MAX_REFERENCE,
               max_gap=DEFAULT_MAX_GAP, relative=True, auto_select=True,
               include_scripts=False, separate_folders=False):
        try:
            rows = match_files(
                video_dir,
                funscript_dir,
                high,
                low,
                use_name=use_name,
                use_duration=use_duration,
            )
            self.cohort = grade_batch(rows, avg_reference, max_reference, max_gap, relative)
            if auto_select:
                select_confident(rows)

            if auto_high and output_dir:
                moved_scripts = {}
                for row in rows:
                    if row["status"] != "高置信" or not row.get('auto_eligible'):
                        continue
                    try:
                        process_match(row, output_dir, move=move, overwrite=overwrite, classify=classify,
                                      avg_reference=avg_reference, max_reference=max_reference, max_gap=max_gap,
                                      cohort=self.cohort, include_scripts=include_scripts,
                                      separate_folders=separate_folders, moved_scripts=moved_scripts)
                        row["status"] = "已处理"
                    except Exception as exc:
                        row["status"] = "处理失败"
                        row["process_error"] = str(exc)

            report_error = None
            if output_csv:
                try:
                    write_csv(rows, output_csv)
                except Exception as exc:
                    report_error = '分析/文件处理已结束，但报告保存失败：' + str(exc)
            self.message_queue.put(("done", rows))
            if report_error:
                self.message_queue.put(('error', report_error))
        except Exception as exc:
            self.message_queue.put(("error", str(exc)))

    def on_tree_click(self, event):
        if self.running:
            return
        if self.tree.identify_region(event.x, event.y) != "cell":
            return
        if self.tree.identify_column(event.x) != "#1":
            return

        item_id = self.tree.identify_row(event.y)
        if not item_id or item_id not in self.item_rows:
            return

        self.tree.focus(item_id)
        self.tree.selection_set(item_id)
        self.toggle_item(item_id)
        return 'break'

    def toggle_item(self, item_id):
        if self.running or item_id not in self.item_rows:
            return
        row = self.item_rows[item_id]
        if row.get('status') == '已处理':
            self.status.set('这项已处理完成，无需重复处理。')
            return
        row['selected'] = not row.get('selected', False)
        row['selection_touched'] = True
        self.tree.set(item_id, 'selected', '☑' if row['selected'] else '☐')
        self.update_summary()

    def toggle_focused(self, event=None):
        self.toggle_item(self.tree.focus())
        return 'break'

    def on_tree_double_click(self, event):
        if self.tree.identify_region(event.x, event.y) != 'cell':
            return 'break'
        if self.tree.identify_column(event.x) == '#1':
            self.on_tree_click(event)
            return 'break'
        if not self.running:
            item = self.tree.identify_row(event.y)
            if item:
                self.tree.selection_set(item)
                self.open_playback()
        return 'break'

    def toggle_all(self):
        eligible = [r for r in self.rows if r.get('status') != '已处理']
        if eligible and all(r.get('selected') for r in eligible):
            self.select_none()
        else:
            self.select_all()

    def select_all(self):
        if self.running:
            return
        for row in self.rows:
            row['selected'] = row.get('status') != '已处理'
            row['selection_touched'] = True
        for item_id, row in self.item_rows.items():
            if row.get("status") == "已处理":
                continue
            row["selected"] = True
            self.tree.set(item_id, "selected", "☑")
        self.update_summary()

    def select_none(self):
        if self.running:
            return
        for row in self.rows:
            row['selected'] = False
            row['selection_touched'] = True
        for item_id, row in self.item_rows.items():
            row["selected"] = False
            self.tree.set(item_id, "selected", '—' if row.get('status') == '已处理' else '☐')
        self.update_summary()

    def select_high_confidence(self):
        if self.running:
            return
        added = select_confident(self.rows)
        self.populate_tree(self.rows)
        self.status.set(f'新增勾选 {added} 项高置信结果；保留手动选择和取消。尚未处理文件。')

    def process_selected(self):
        if self.running:
            return

        output_dir = self.output_dir.get().strip()
        if not output_dir:
            messagebox.showwarning("提示", "请先选择输出文件夹。")
            return

        rows = [
            row
            for row in self.rows
            if row.get("selected") and row.get("status") != "已处理"
        ]
        if not rows:
            messagebox.showwarning("提示", "请先勾选要处理的项。")
            return

        self.set_buttons(True)
        self.status.set("正在处理选中项...")
        thread = threading.Thread(target=self.process_selected_worker,
                                  args=(rows, output_dir, self.operation_mode.get() == 'move', self.overwrite.get(),
                                        self.classify_intensity.get(), self.avg_reference, self.max_reference,
                                        self.output_csv.get().strip(), self.max_gap,
                                        self.cohort if self.relative_mode else None,
                                        self.include_scripts.get(), self.separate_folders.get()),
                                  daemon=True)
        thread.start()

    def process_selected_worker(self, rows, output_dir, move, overwrite, classify=False,
                                avg_reference=DEFAULT_AVG_REFERENCE, max_reference=DEFAULT_MAX_REFERENCE,
                                output_csv='', max_gap=DEFAULT_MAX_GAP, cohort=None,
                                include_scripts=False, separate_folders=False):
        from .pair_output import relocated_scripts
        moved_scripts = relocated_scripts(self.rows)
        for row in rows:
            try:
                process_match(row, output_dir, move=move, overwrite=overwrite, classify=classify,
                              avg_reference=avg_reference, max_reference=max_reference, max_gap=max_gap,
                              cohort=cohort, include_scripts=include_scripts,
                              separate_folders=separate_folders, moved_scripts=moved_scripts)
                row["status"] = "已处理"
                row["selected"] = False
            except Exception as exc:
                row["status"] = "处理失败"
                row["process_error"] = str(exc)

        report_error = None
        if output_csv:
            try:
                write_csv(self.rows, output_csv)
            except Exception as exc:
                report_error = '文件处理已结束，但报告保存失败：' + str(exc)
        self.message_queue.put(("done", self.rows))
        if report_error:
            self.message_queue.put(('error', report_error))

    def process_queue(self):
        try:
            while True:
                kind, payload = self.message_queue.get_nowait()
                if kind == "done":
                    self.set_buttons(False)
                    self.status.set(f"完成，共 {len(payload)} 条结果")
                    self.populate_tree(payload)
                elif kind == "error":
                    self.set_buttons(False)
                    self.status.set("出错")
                    messagebox.showerror("处理失败", payload)
        except queue.Empty:
            pass

        self.root.after(poll_delay(self.root, self.running), self.process_queue)

    def populate_tree(self, rows):
        self.rows = rows
        self.item_rows = {}
        self.tree.delete(*self.tree.get_children())

        def insert_row(row):
            row.setdefault("selected", False)
            if row.get('status') == '已处理':
                row['selected'] = False
            checkbox = '—' if row.get('status') == '已处理' else ("☑" if row["selected"] else "☐")
            item_id = self.tree.insert(
                "",
                "end",
                values=(
                    checkbox,
                    row["funscript"],
                    row["video"],
                    f"{row['name_score']:.1f}",
                    f"{row['duration_score']:.1f}" if row.get('duration_available', True) else '—',
                    f"{row['total']:.1f}",
                    (f"{row['intensity']['folder'][3:]} · {row['intensity']['score']:.2f}"
                     if row.get('intensity', {}).get('score') is not None
                     else row.get('intensity', {}).get('folder', '尚未评分')[3:]
                     if row.get('intensity') else '尚未评分'),
                    row["status"],
                ),
            )
            self.item_rows[item_id] = row
            tag = 'protected' if row['status'] == '已处理' else (
                'confirmed' if row['status'] == '人工确认' or row.get('auto_eligible') else 'review')
            self.tree.item(item_id, tags=(tag,))
        paint_items(self, self.root, rows, insert_row, self.update_summary)

    def open_output_dir(self):
        output_dir = self.output_dir.get().strip()
        if not output_dir:
            messagebox.showwarning("提示", "请先选择输出文件夹。")
            return
        if not os.path.isdir(output_dir):
            messagebox.showwarning("提示", "输出文件夹不存在。")
            return
        os.startfile(output_dir)


    def open_video_duplicates(self):
        from .video_duplicates.ui import VideoDuplicateWindow
        window = VideoDuplicateWindow(self.root, self.video_dir.get().strip())
        if not hasattr(self, 'duplicate_windows'):
            self.duplicate_windows = []
        self.duplicate_windows.append(window)

    def open_duplicates(self):
        from .funscript_duplicates.ui import DuplicateWindow
        window = DuplicateWindow(self.root, self.funscript_dir.get().strip())
        if not hasattr(self, 'duplicate_windows'):
            self.duplicate_windows = []
        self.duplicate_windows.append(window)

    def open_playback(self):
        if self.running:
            return
        try:
            from .playback_review import PlaybackReview
            selected = self.tree.selection()
            row = self.item_rows.get(selected[0], {}) if selected else {}
            script = next(iter(row.get('output_script_paths', [])), row.get('funscript_path', ''))
            video = row.get('output_path') or row.get('video_path', '')
            window = PlaybackReview(self.root, script, video, self.accept_review)
            if not hasattr(self, 'playback_windows'):
                self.playback_windows = []
            self.playback_windows.append(window)
        except Exception as exc:
            messagebox.showerror('无法打开播放核对', str(exc))

    def accept_review(self, result):
        if self.running:
            messagebox.showinfo('提示', '主窗口任务仍在运行，请完成后再采用对应。')
            return
        video, script = result['video_path'], result['script_path']
        name_evidence = compare_names(video, script)
        # Explicit human selection replaces the suggestion for this video only.
        rows = [r for r in self.rows if os.path.normcase(r['video_path']) != os.path.normcase(video)]
        rows.append({'video': Path(video).name, 'funscript': Path(script).name,
                     'video_path': video, 'funscript_path': script,
                     'script_stem': Path(script).stem, 'video_stem': Path(video).stem,
                     'name_score': name_evidence['score']*100, 'name_reason': name_evidence['reason'],
                     'duration_score': 0, 'total': result.get('score') or 0,
                     'video_duration': result['duration'], 'script_duration': None,
                     'status': '人工确认', 'selected': True, 'multi_match': True})
        self.cohort = grade_batch(rows, self.avg_reference, self.max_reference, self.max_gap, self.relative_mode)
        self.populate_tree(rows)
        self.status.set('已加入人工确认结果；勾选后点击处理选中项')

    def close(self):
        if any(window.is_moving() for window in getattr(self, 'duplicate_windows', [])):
            messagebox.showinfo('整理进行中', '请先在查重整理窗口停止操作，等待安全结束后再关闭。')
            return
        if self.running:
            messagebox.showinfo('正在处理', '请等待当前文件任务完成后再关闭窗口。')
            return
        for playback in getattr(self, 'playback_windows', []):
            if not playback.closed:
                playback.close()
        for window in getattr(self, 'duplicate_windows', []):
            window.close()
        self.root.destroy()


def run_cli(args):
    rows = match_files(
        args.video,
        args.funscript,
        args.high,
        args.low,
        use_name=not args.no_name,
        use_duration=not args.no_duration,
    )
    grade_rows(rows)
    write_csv(rows, args.output)
    print(f"完成，共 {len(rows)} 条结果，已写入：{args.output}")
    for row in rows:
        print(
            f"{row['status']}\t{row['funscript']}\t{row['video']}\t"
            f"{row['total']:.1f}%"
        )


def main():
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    if len(sys.argv) > 1:
        parser = argparse.ArgumentParser(description="Funscript 智能匹配工具")
        parser.add_argument("--video", required=True)
        parser.add_argument("--funscript", required=True)
        parser.add_argument("--output", required=True)
        parser.add_argument("--high", type=float, default=95.0)
        parser.add_argument("--low", type=float, default=80.0)
        parser.add_argument("--no-name", action="store_true")
        parser.add_argument("--no-duration", action="store_true")
        run_cli(parser.parse_args())
    else:
        root = tk.Tk()
        SmartMatcherApp(root)
        root.mainloop()


if __name__ == "__main__":
    main()
