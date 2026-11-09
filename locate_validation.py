"""Authorized temporary A/B/C/D hardware test, always restore formal keys.

This is a validation tool, not the production locator. No global suppression.
"""
import sys
import json
import time
from pathlib import Path
from collections import Counter
from datetime import datetime

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT / '.deps'))
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication, QMainWindow, QWidget, QVBoxLayout, QGridLayout, QLabel, QPushButton
from protocol import write_key
from profiles import save_json
from raw_keyboard import KeyboardReader

FORMAL = ['Backspace', 'Delete', 'Enter', 'Enter']
TEST = ['A', 'B', 'C', 'D']


class Validation(QMainWindow):
    def __init__(self):
        super().__init__()
        self.path = ROOT / 'tmp' / ('locate-' + datetime.now().strftime('%Y%m%d-%H%M%S') + '.json')
        self.log = dict(formal=FORMAL, test=TEST, state='prepared', test_sent=[], restored=[], events=[], seen=[])
        self.phase = 'prepared'
        self.seen = set()
        self.formal_count = Counter()
        self.held = set()
        self.reader = None
        self.setWindowTitle('Key4 · 临时按键定位验证')
        self.resize(640, 520)
        center = QWidget()
        self.setCentralWidget(center)
        layout = QVBoxLayout(center)
        layout.setContentsMargins(24, 24, 24, 24)
        self.title = QLabel('准备测试…')
        self.title.setStyleSheet('font-size: 22px; font-weight: bold')
        layout.addWidget(self.title)
        self.info = QLabel('只在本窗口内按小键盘；无需区分实体键的排列。')
        self.info.setWordWrap(True)
        layout.addWidget(self.info)
        grid = QGridLayout()
        self.tiles = []
        for i, letter in enumerate(TEST):
            label = QLabel(f'测试键 {letter}')
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setMinimumHeight(130)
            label.setStyleSheet('background:#f2f4f8; border:1px solid #cbd3e0; border-radius:10px; font-size:22px;')
            self.tiles.append(label)
            grid.addWidget(label, i//2, i%2)
        layout.addLayout(grid)
        self.exit_button = QPushButton('结束测试并恢复正式功能')
        self.exit_button.clicked.connect(self.close)
        layout.addWidget(self.exit_button)
        self.timeout = QTimer(self)
        self.timeout.setSingleShot(True)
        self.timeout.timeout.connect(self.close)
        self.timeout.start(180000)
        self.flush()

    def flush(self):
        save_json(self.path, self.log)

    def start(self):
        try:
            self.reader = KeyboardReader(int(self.winId()))
            self.phase = 'writing_tests'
            self.log['state'] = self.phase
            self.flush()
            for i, letter in enumerate(TEST, 1):
                write_key(i, [dict(shortcut=letter, delay_ms=100)])
                self.log['test_sent'].append(i)
                self.flush()
            self.phase = 'test'
            self.log['state'] = self.phase
            self.flush()
            self.title.setText('请依次按一下四个实体键')
            self.info.setText('随意选择顺序，每键按一次、间隔约一秒。对应格子会亮起；四键齐全后自动恢复。')
            self.raise_()
            self.activateWindow()
            print('READY: A B C D written; focus test window and press all four keys', flush=True)
        except Exception as error:
            self.log['error'] = str(error)
            print('START ERROR: '+str(error), flush=True)
            self.close()

    def restore(self):
        if len(self.log['restored']) == 4:
            return True
        self.phase = 'restoring'
        self.log['state'] = self.phase
        self.flush()
        self.title.setText('正在恢复正式功能…')
        errors = []
        for i, shortcut in enumerate(FORMAL, 1):
            if i in self.log['restored']:
                continue
            for attempt in range(2):
                try:
                    write_key(i, [dict(shortcut=shortcut, delay_ms=100)])
                    self.log['restored'].append(i)
                    self.flush()
                    break
                except Exception as error:
                    if attempt == 1:
                        errors.append(f'{i}: {error}')
                    time.sleep(.2)
        self.log['restore_errors'] = errors
        self.log['state'] = 'restored_unverified' if not errors else 'restore_failed'
        self.flush()
        print('RESTORE '+json.dumps(self.log['restored'])+' ERRORS '+json.dumps(errors), flush=True)
        return not errors

    def begin_verify(self):
        if self.phase != 'test':
            return
        if not self.restore():
            self.title.setText('恢复未完成，请保持连接')
            self.info.setText('请通知聊天中的助手处理，不要继续测试。')
            return
        self.phase = 'verify'
        self.held.clear()
        self.title.setText('已恢复，请再各按一次四个实体键')
        self.info.setText('现在应输出 Backspace、Delete、Enter、Enter。本窗口只显示检测结果，不编辑任何内容。')
        for label, text in zip(self.tiles, FORMAL):
            label.setText(text)
            label.setStyleSheet('background:#f2f4f8; border:1px solid #cbd3e0; border-radius:10px; font-size:22px;')
        self.timeout.start(120000)
        print('VERIFY: press all four keys again after automatic restoration', flush=True)

    def nativeEvent(self, event_type, message):
        if self.reader:
            try:
                event = self.reader.read(message)
                if event:
                    self.log['events'].append(dict(event, phase=self.phase, time=time.time()))
                    vk = event['vkey']
                    if event['released']:
                        self.held.discard(vk)
                    elif vk not in self.held:
                        self.held.add(vk)
                        if self.phase == 'test' and 65 <= vk <= 68:
                            index = vk-65
                            self.seen.add(index)
                            self.log['seen'] = sorted(self.seen)
                            self.tiles[index].setStyleSheet('background:#e4edff; border:3px solid #3466df; border-radius:10px; font-size:22px;')
                            self.tiles[index].setText(f'已定位 {chr(vk)}')
                            print('LOCATED '+chr(vk), flush=True)
                            if len(self.seen) == 4:
                                QTimer.singleShot(600, self.begin_verify)
                        elif self.phase == 'verify' and vk in (8,46,13):
                            self.formal_count[vk] += 1
                            index = 0 if vk == 8 else 1 if vk == 46 else min(3, 1+self.formal_count[13])
                            self.tiles[index].setStyleSheet('background:#e1f3e8; border:2px solid #38955d; border-radius:10px; font-size:22px;')
                            self.log['formal_counts'] = dict(self.formal_count)
                            print('FORMAL '+str(vk), flush=True)
                            if self.formal_count[8]>=1 and self.formal_count[46]>=1 and self.formal_count[13]>=2:
                                self.phase = 'complete'
                                self.log['state'] = 'complete'
                                self.title.setText('四键定位与正式功能恢复均已验证')
                                self.info.setText('测试结束，窗口即将关闭。')
                                QTimer.singleShot(1800, self.close)
                    self.flush()
            except Exception as error:
                print('INPUT ERROR '+str(error), flush=True)
                self.log['input_error'] = str(error)
        return False, 0

    def keyPressEvent(self, event):
        event.accept()

    def keyReleaseEvent(self, event):
        event.accept()

    def closeEvent(self, event):
        self.timeout.stop()
        if self.log['state'] != 'prepared':
            self.restore()
        if self.reader:
            self.reader.close()
            self.reader = None
        self.flush()
        event.accept()


if __name__ == '__main__':
    app = QApplication(sys.argv)
    app.setStyleSheet('QWidget{font-family:"Microsoft YaHei UI";font-size:15px;} QPushButton{padding:12px;}')
    window = Validation()
    window.show()
    QTimer.singleShot(500, window.start)
    try:
        app.exec()
    finally:
        if window.log['state'] != 'prepared':
            window.restore()
        print('RESULT '+str(window.path), flush=True)
