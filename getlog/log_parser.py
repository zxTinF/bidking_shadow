# -*- coding: utf-8 -*-
"""
日志文件解析

提供：
  - extract_event  : 从单行日志提取事件类型和 JSON 数据
  - iter_log_lines : 逐行迭代日志文件，支持实时 tail 模式
"""

import json
import re
import time
from typing import Generator, Optional, Tuple


_NOTIFY_RE = re.compile(
    r'\[Network\] OnHanderNotify \S+ : \((\S+)\)(\{.*\})\s*$'
)


def extract_event(line: str) -> Optional[Tuple[str, dict]]:
    """
    从 [Network] OnHanderNotify 格式的日志行中提取事件。

    Returns:
        (event_type, data) 元组，如 ('S2C_33_game_start_notify', {...})
        若格式不匹配或 JSON 解析失败则返回 None。
    """
    m = _NOTIFY_RE.search(line)
    if not m:
        return None
    event_type = m.group(1)
    try:
        return event_type, json.loads(m.group(2))
    except json.JSONDecodeError:
        return None


def iter_log_lines(path: str, tail: bool = False) -> Generator[Optional[str], None, None]:
    """
    逐行迭代日志文件。

    Args:
        path : 日志文件路径
        tail : True 时到达文件末尾后持续轮询（实时监听）；
               False 时到达末尾即停止。

    Yields:
        str  : 读到的文本行（含换行符）
        None : 到达文件末尾的信号（每次 EOF 均发出一次，供上层判断追赶结束）

    实时模式下每次 EOF 后等待 0.5s 再次尝试，直到 KeyboardInterrupt。
    """
    with open(path, 'r', encoding='utf-8', errors='replace') as f:
        while True:
            line = f.readline()
            if line:
                yield line
            else:
                yield None          # EOF 信号：通知上层追赶阶段结束
                if tail:
                    time.sleep(0.5)
                else:
                    break
