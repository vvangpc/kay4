"""Transactional temporary key mapping with a durable restoration journal.

Committed data is a record of prior writes, never hardware readback.
"""
import copy
import json
from pathlib import Path
from protocol import devices, write_key, encode_key
from profiles import save_json

MARKERS = ('F13', 'F14', 'F15', 'F16')
MARKER_VKEYS = {0x7C + i: i for i in range(4)}


def target_path():
    matches = devices()
    if len(matches) != 1:
        raise RuntimeError('请连接一台目标小键盘。')
    return matches[0]['path']


class Locator:
    def __init__(self, folder, writer=None, target=None):
        self.folder = Path(folder)
        self.journal_path = self.folder / 'locate-recovery.json'
        self.committed_path = self.folder / 'committed.json'
        self.writer = writer or write_key
        self.target = target or target_path
        self.active = False
        self.journal = None
        self.error = ''
        if self.journal_path.exists():
            try:
                self.journal = json.loads(self.journal_path.read_text(encoding='utf-8'))
                self._validate(self.journal)
            except Exception as error:
                self.error = f'恢复记录损坏，请保留记录并联系维护：{error}'

    @staticmethod
    def _validate(record):
        if not isinstance(record.get('target'), str) or not record['target']:
            raise ValueError('缺少目标设备')
        bytes.fromhex(record['target'])
        keys = record.get('keys')
        if not isinstance(keys, dict) or set(keys) != {'1', '2', '3', '4'}:
            raise ValueError('需要完整四键配置')
        for key, steps in keys.items():
            encode_key(int(key), steps)

    @property
    def pending(self):
        return self.journal_path.exists()

    def committed(self):
        if not self.committed_path.exists():
            return None
        data = json.loads(self.committed_path.read_text(encoding='utf-8'))
        self._validate(data)
        return data

    def can_start(self, target):
        if self.pending or self.error:
            return False
        try:
            data = self.committed()
            return bool(data and data['target'] == target.hex())
        except (OSError, ValueError, KeyError, TypeError):
            return False

    def remember(self, target, plan):
        if self.pending:
            raise RuntimeError('请先恢复正式配置。')
        try:
            data = json.loads(self.committed_path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            data = {}
        if data.get('target') != target.hex():
            data = {'target': target.hex(), 'keys': {}}
        for key, steps in plan:
            encode_key(key, steps)
            data['keys'][str(key)] = copy.deepcopy(steps)
        try:
            save_json(self.committed_path, data)
        except OSError:
            self.error = '正式配置记录保存失败，请解决存储问题后重新写入四键。'
            raise

    def start(self):
        if self.pending:
            raise RuntimeError('存在未完成恢复，请先恢复正式配置。')
        target = self.target()
        data = self.committed()
        if not data or data['target'] != target.hex():
            raise RuntimeError('请先写入完整四键正式方案，再开启定位。')
        self._validate(data)
        self.journal = dict(copy.deepcopy(data), state='preparing', temporary_sent=[])
        # Durable record must exist before the first temporary device write.
        save_json(self.journal_path, self.journal)
        try:
            for i, marker in enumerate(MARKERS, 1):
                self.writer(i, [{'shortcut': marker, 'delay_ms': 100}], expected_path=target)
                self.journal['temporary_sent'].append(i)
                save_json(self.journal_path, self.journal)
            self.journal['state'] = 'active'
            save_json(self.journal_path, self.journal)
            self.active = True
        except Exception as error:
            try:
                self.restore()
            except Exception as restore_error:
                raise RuntimeError(f'定位开启失败：{error}；恢复未完成：{restore_error}') from error
            raise RuntimeError(f'定位开启失败，原配置已恢复：{error}') from error

    def invalidate(self, target, key):
        """A failed transfer may have reached firmware; do not trust old state."""
        try:
            data = json.loads(self.committed_path.read_text(encoding='utf-8'))
            if data.get('target') == target.hex():
                data['keys'].pop(str(key), None)
                save_json(self.committed_path, data)
        except Exception:
            self.error = '配置状态不确定，请重新写入完整四键正式方案。'

    def restore(self):
        self.active = False
        if not self.pending:
            return
        if self.error:
            raise RuntimeError(self.error)
        if self.journal is None:
            self.journal = json.loads(self.journal_path.read_text(encoding='utf-8'))
        self._validate(self.journal)
        target = bytes.fromhex(self.journal['target'])
        if self.target() != target:
            raise RuntimeError('请把原小键盘接回原 USB 接口后恢复。')
        self.journal['state'] = 'restoring'
        save_json(self.journal_path, self.journal)
        errors = []
        for key, steps in self.journal['keys'].items():
            try:
                self.writer(int(key), steps, expected_path=target)
            except Exception as error:
                errors.append(f'按键{key}: {error}')
        if errors:
            self.journal['state'] = 'restore_failed'
            self.journal['errors'] = errors
            save_json(self.journal_path, self.journal)
            raise RuntimeError('；'.join(errors))
        # Keep the journal until formal state is durably saved, too.
        save_json(self.committed_path, {'target': self.journal['target'], 'keys': self.journal['keys']})
        self.journal_path.unlink()
        self.journal = None
