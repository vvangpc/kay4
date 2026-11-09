"""Editable PC-side profiles. These are not hardware readback."""
import copy
import json
import os
from pathlib import Path
from protocol import encode_key


def defaults():
    return [dict(name='', mode='shortcut', shortcut=key, steps=[])
            for key in ('Backspace', 'Delete', 'Enter', '')]


def sequence(config):
    if config['mode'] == 'shortcut':
        return ([{'shortcut': config['shortcut'], 'delay_ms': 100}]
                if config['shortcut'].strip() else None)
    if not config['steps']:
        raise ValueError('宏还没有步骤。')
    return copy.deepcopy(config['steps'])


def load_profile(data):
    if not isinstance(data, dict) or data.get('version') not in (1, 2):
        raise ValueError('不是有效的 Key4 方案。')
    keys = data.get('keys')
    if not isinstance(keys, list) or len(keys) != 4:
        raise ValueError('方案必须包含四个按键。')
    if data['version'] == 1:
        keys = [dict(name='', mode='macro' if k and len(k) > 1 else 'shortcut',
                     shortcut=k[0]['shortcut'] if k and len(k) == 1 else '', steps=k or [])
                for k in keys]
    result = []
    for i, key in enumerate(keys):
        if not isinstance(key, dict) or key.get('mode') not in ('shortcut', 'macro'):
            raise ValueError('按键类型无效。')
        if any(not isinstance(key.get(s), str) for s in ('name', 'shortcut')):
            raise ValueError('按键名称或快捷键格式无效。')
        if len(key['name']) > 80 or len(key['shortcut']) > 100:
            raise ValueError('按键名称或快捷键过长。')
        # Incomplete editable text is preserved; write_plan validates before any I/O.
        steps = key.get('steps')
        if not isinstance(steps, list) or len(steps) > 12:
            raise ValueError('每个宏最多 12 步。')
        for step in steps:
            if not isinstance(step, dict) or not isinstance(step.get('shortcut'), str):
                raise ValueError('宏步骤格式无效。')
            if len(step['shortcut']) > 100:
                raise ValueError('快捷键过长。')
            encode_key(i+1, [dict(step, shortcut='A')])
        result.append(copy.deepcopy(key))
    return result


def write_plan(keys, selected=None):
    plan = []
    skipped = []
    for i in range(4) if selected is None else [selected]:
        steps = sequence(keys[i])
        if steps is None:
            skipped.append(i+1)
            continue
        try:
            encode_key(i+1, steps)
        except ValueError as error:
            raise ValueError(f'按键 {i+1}：{error}') from error
        plan.append((i+1, steps))
    if not plan:
        raise ValueError('请先设置快捷键或宏。')
    return plan, skipped


def save_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    with temp.open('w', encoding='utf-8') as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    temp.replace(path)
