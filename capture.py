"""Foreground-only keyboard capture: no global hooks or input suppression."""
import time
from PySide6.QtCore import Qt, QEvent
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QApplication, QDialog, QVBoxLayout, QLabel, QPushButton, QHBoxLayout, QListWidget
from protocol import parse_shortcut

MODIFIER_KEYS = {Qt.Key.Key_Control, Qt.Key.Key_Shift, Qt.Key.Key_Alt, Qt.Key.Key_Meta,
                 Qt.Key.Key_AltGr, Qt.Key.Key_unknown}


class CaptureDialog(QDialog):
    def __init__(self, parent=None, macro=False, capacity=12):
        super().__init__(parent)
        self.macro = macro
        self.capacity = capacity
        self.steps = []
        self.last_time = None
        self.setWindowTitle('录制宏' if macro else '录入快捷键')
        self.resize(460, 340 if macro else 240)
        layout = QVBoxLayout(self)
        layout.setSpacing(16)
        self.label = QLabel('请按下按键或组合键')
        layout.addWidget(self.label)
        hint = QLabel('仅在此窗口录入。系统快捷键可能切走窗口，可取消后手动填写。')
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.list = QListWidget()
        layout.addWidget(self.list)
        buttons = QHBoxLayout()
        cancel = QPushButton('取消')
        cancel.setAutoDefault(False)
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        self.finish_button = QPushButton('完成')
        self.finish_button.setAutoDefault(False)
        self.finish_button.setEnabled(False)
        self.finish_button.clicked.connect(self.accept)
        buttons.addWidget(self.finish_button)
        layout.addLayout(buttons)
        QApplication.instance().installEventFilter(self)

    def eventFilter(self, obj, event):
        if not self.isVisible() or QApplication.activeModalWidget() is not self:
            return False
        if event.type() == QEvent.Type.ShortcutOverride:
            event.accept()
            return True
        if event.type() in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
            if QApplication.instance().property('key4Locating') and int(Qt.Key.Key_F13) <= event.key() <= int(Qt.Key.Key_F16):
                return True
            if event.type() == QEvent.Type.KeyPress and not event.isAutoRepeat() and event.key() not in MODIFIER_KEYS:
                text = QKeySequence(event.keyCombination()).toString(QKeySequence.SequenceFormat.PortableText)
                try:
                    parse_shortcut(text)
                except ValueError as error:
                    self.label.setText(str(error))
                    return True
                if len(self.steps) >= self.capacity:
                    self.label.setText('已达到步骤上限，请点击完成。')
                    return True
                now = time.monotonic()
                if self.macro:
                    if self.steps:
                        self.steps[-1]['delay_ms'] = min(10000, max(0, round((now-self.last_time)*1000)))
                    self.steps.append({'shortcut': text, 'delay_ms': 100})
                else:
                    self.steps = [{'shortcut': text, 'delay_ms': 100}]
                self.last_time = now
                self.list.clear()
                self.list.addItems([f"{i+1}.  {s['shortcut']}    ·    {s['delay_ms']} ms" for i, s in enumerate(self.steps)])
                self.finish_button.setEnabled(True)
                self.label.setText(f'已录入 {len(self.steps)} 步' if self.macro else text)
            return True
        return False

    def done(self, result):
        QApplication.instance().removeEventFilter(self)
        super().done(result)
