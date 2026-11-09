import sys
import os
import json
import copy
from pathlib import Path
from datetime import datetime

ROOT = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).parent
if not getattr(sys, 'frozen', False):
    sys.path.insert(0, str(ROOT / '.deps'))
from PySide6.QtCore import Qt, QThread, Signal, QTimer, QSize, QEvent, QLockFile
from PySide6.QtGui import QFont, QIcon, QPixmap, QPainter, QColor
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QGridLayout, QLabel, QPushButton, QLineEdit, QStackedWidget, QListWidget,
    QListWidgetItem, QAbstractItemView, QSpinBox, QFileDialog, QMessageBox, QDialog, QCheckBox)
from protocol import devices, write_key, encode_key
from profiles import defaults, load_profile, write_plan, sequence, save_json
from capture import CaptureDialog
from locating import Locator, MARKER_VKEYS, target_path
from raw_keyboard import KeyboardReader


def button(text, callback, primary=False):
    b = QPushButton(text)
    if primary:
        b.setObjectName('primary')
    b.clicked.connect(callback)
    return b


def icon():
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor('#3466df'))
    painter.drawRoundedRect(0, 0, 64, 64, 14, 14)
    painter.setBrush(QColor('white'))
    for x in (14, 36):
        for y in (14, 36):
            painter.drawRoundedRect(x, y, 14, 14, 3, 3)
    painter.end()
    return QIcon(pixmap)


class Writer(QThread):
    result = Signal(object, str)

    def __init__(self, plan, folder, locator=None):
        super().__init__()
        self.plan = copy.deepcopy(plan)
        self.folder = folder
        self.locator = locator

    def run(self):
        sent = []
        path = self.folder / (datetime.now().strftime('%Y%m%d-%H%M%S-%f') + '.json')
        record = {'plan': self.plan, 'sent': sent, 'state': 'pending'}
        writing_key = None
        target = None
        try:
            save_json(path, record)
            if self.locator and self.locator.pending:
                self.locator.restore()
            target = self.locator.target() if self.locator else None
            for key, steps in self.plan:
                writing_key = key
                if self.locator:
                    write_key(key, steps, expected_path=target)
                else:
                    write_key(key, steps)
                sent.append(key)
                if self.locator:
                    self.locator.remember(target, [(key, steps)])
                writing_key = None
                save_json(path, record)
            record['state'] = 'sent_unverified'
            save_json(path, record)
            self.result.emit(sent, '')
        except Exception as error:
            if self.locator and writing_key is not None:
                self.locator.invalidate(target, writing_key)
            record.update(state='incomplete', error=str(error))
            try:
                save_json(path, record)
            except OSError:
                pass
            self.result.emit(sent, str(error))


class LocationWorker(QThread):
    result = Signal(str)

    def __init__(self, locator, enable):
        super().__init__()
        self.locator, self.enable = locator, enable

    def run(self):
        try:
            if self.enable:
                self.locator.start()
            else:
                self.locator.restore()
            self.result.emit('')
        except Exception as error:
            self.result.emit(str(error))


class StepRow(QWidget):
    changed = Signal()
    removed = Signal(object)

    def __init__(self, step):
        super().__init__()
        row = QHBoxLayout(self)
        row.setContentsMargins(10, 6, 10, 6)
        grip = QLabel('⠿')
        grip.setToolTip('拖动此处调整顺序')
        grip.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        row.addWidget(grip)
        self.edit = QLineEdit(step['shortcut'])
        self.edit.setPlaceholderText('例如 Ctrl+V')
        self.edit.setMaxLength(100)
        self.edit.textChanged.connect(self.changed)
        row.addWidget(self.edit, 1)
        row.addWidget(button('录入', self.capture))
        self.delay = QSpinBox()
        self.delay.setRange(0, 10000)
        self.delay.setSuffix(' ms')
        self.delay.setValue(step['delay_ms'])
        self.delay.setFixedWidth(116)
        self.delay.valueChanged.connect(self.changed)
        row.addWidget(self.delay)
        remove = button('×', lambda: self.removed.emit(self))
        remove.setFixedWidth(36)
        remove.setToolTip('删除步骤')
        row.addWidget(remove)

    def capture(self):
        dialog = CaptureDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.steps:
            self.edit.setText(dialog.steps[0]['shortcut'])
        dialog.deleteLater()

    def value(self):
        return {'shortcut': self.edit.text().strip(), 'delay_ms': self.delay.value()}


class Window(QMainWindow):
    def __init__(self, data_dir=None):
        super().__init__()
        self.data_dir = Path(data_dir) if data_dir else Path(os.environ.get('LOCALAPPDATA', str(ROOT))) / 'Key4'
        self.keys = defaults()
        self.index = 0
        self.loading = False
        self.worker = None
        self.sent = {}
        self.connected = False
        self.locator = Locator(self.data_dir)
        self.reader = None
        self.held_markers = set()
        self.exit_requested = False
        self.restore_on_idle = False
        self.location_error = ''
        self.load_error = ''
        last = self.data_dir / 'last-session.json'
        if last.exists():
            try:
                self.keys = load_profile(json.loads(last.read_text(encoding='utf-8')))
            except (ValueError, OSError, KeyError, TypeError) as error:
                self.load_error = f'上次方案读取失败，已使用默认值：{error}'
        self.setWindowTitle('Key4 · 四键设置')
        self.setWindowIcon(icon())
        self.resize(1080, 730)
        self.setMinimumSize(960, 660)
        center = QWidget()
        self.setCentralWidget(center)
        outer = QVBoxLayout(center)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        header = QWidget()
        header.setObjectName('header')
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(28, 18, 28, 18)
        self.connection = QLabel('检测设备…')
        header_layout.addWidget(self.connection)
        separator = QLabel('│')
        separator.setObjectName('muted')
        header_layout.addWidget(separator)
        self.locate_toggle = QCheckBox('按键定位')
        self.locate_toggle.setEnabled(False)
        self.locate_toggle.setToolTip('开启时临时绑定四个定位键；关闭或退出时恢复已保存的正式配置。')
        self.locate_toggle.toggled.connect(self.toggle_location)
        header_layout.addWidget(self.locate_toggle)
        self.locate_state = QLabel('')
        self.locate_state.setObjectName('muted')
        header_layout.addWidget(self.locate_state)
        self.recover_button = button('恢复正式功能', lambda: self.toggle_location(False))
        self.recover_button.hide()
        header_layout.addWidget(self.recover_button)
        header_layout.addStretch()
        outer.addWidget(header)
        body = QWidget()
        columns = QHBoxLayout(body)
        columns.setContentsMargins(0, 0, 0, 0)
        columns.setSpacing(0)
        sidebar = QWidget()
        sidebar.setObjectName('sidebar')
        sidebar.setFixedWidth(340)
        left = QVBoxLayout(sidebar)
        left.setContentsMargins(24, 28, 24, 24)
        left.setSpacing(22)
        left.addSpacing(10)
        grid = QGridLayout()
        grid.setSpacing(12)
        self.cards = []
        for i in range(4):
            b = button('', lambda checked=False, n=i: self.select(n))
            b.setObjectName('keycard')
            b.setCheckable(True)
            b.setMinimumHeight(148)
            grid.addWidget(b, i // 2, i % 2)
            self.cards.append(b)
        left.addLayout(grid)
        left.addStretch()
        left.addWidget(button('恢复默认方案', self.reset))
        left_bottom = QHBoxLayout()
        left_bottom.addWidget(button('打开方案', self.open_profile))
        left_bottom.addWidget(button('保存方案', self.save_profile))
        left.addLayout(left_bottom)
        columns.addWidget(sidebar)
        editor = QWidget()
        right = QVBoxLayout(editor)
        right.setContentsMargins(28, 28, 28, 24)
        right.setSpacing(20)
        top = QHBoxLayout()
        self.heading = QLabel()
        self.heading.setObjectName('heading')
        top.addWidget(self.heading)
        self.badge = QLabel('未写入')
        self.badge.setObjectName('badge')
        top.addWidget(self.badge)
        top.addStretch()
        right.addLayout(top)
        tabs = QHBoxLayout()
        tabs.setSpacing(0)
        self.tabs = []
        for mode, text in [('shortcut', '快捷键'), ('macro', '宏')]:
            b = button(text, lambda checked=False, m=mode: self.set_mode(m))
            b.setCheckable(True)
            b.setFixedWidth(120)
            tabs.addWidget(b)
            self.tabs.append(b)
        tabs.addStretch()
        right.addLayout(tabs)
        right.addWidget(QLabel('名称（可选）'))
        self.name = QLineEdit()
        self.name.setMaxLength(80)
        self.name.setPlaceholderText('为这个按键起一个名字')
        self.name.textChanged.connect(self.changed)
        right.addWidget(self.name)
        self.pages = QStackedWidget()
        quick_page = QWidget()
        quick = QVBoxLayout(quick_page)
        quick.setContentsMargins(0, 0, 0, 0)
        quick.setSpacing(14)
        quick.addWidget(QLabel('快捷键'))
        field = QHBoxLayout()
        self.shortcut = QLineEdit()
        self.shortcut.setMaxLength(100)
        self.shortcut.setPlaceholderText('手动填写，或点击右侧录入')
        self.shortcut.textChanged.connect(self.changed)
        field.addWidget(self.shortcut, 1)
        field.addWidget(button('录入', self.capture_shortcut))
        quick.addLayout(field)
        self.hint = QLabel('')
        self.hint.setObjectName('muted')
        self.hint.setWordWrap(True)
        quick.addWidget(self.hint)
        quick.addSpacing(12)
        quick.addWidget(QLabel('常用按键'))
        common = QHBoxLayout()
        for text in ['Backspace', 'Delete', 'Enter']:
            common.addWidget(button(text, lambda checked=False, s=text: self.shortcut.setText(s)))
        common.addStretch()
        quick.addLayout(common)
        quick.addStretch()
        self.pages.addWidget(quick_page)
        macro_page = QWidget()
        macro = QVBoxLayout(macro_page)
        macro.setContentsMargins(0, 0, 0, 0)
        macro.setSpacing(12)
        macro_top = QHBoxLayout()
        macro_top.addWidget(QLabel('步骤'))
        self.count = QLabel()
        macro_top.addWidget(self.count)
        macro_top.addStretch()
        self.record_button = button('● 录制宏', self.record_macro)
        macro_top.addWidget(self.record_button)
        macro.addLayout(macro_top)
        self.steps = QListWidget()
        self.steps.setObjectName('steps')
        self.steps.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.steps.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.steps.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.steps.model().rowsMoved.connect(self.changed)
        macro.addWidget(self.steps, 1)
        actions = QHBoxLayout()
        actions.addWidget(button('＋ 添加步骤', self.add_step))
        actions.addWidget(button('↑', lambda: self.move(-1)))
        actions.addWidget(button('↓', lambda: self.move(1)))
        actions.addStretch()
        macro.addLayout(actions)
        self.pages.addWidget(macro_page)
        right.addWidget(self.pages, 1)
        self.status = QLabel(self.load_error or '默认方案尚未写入键盘。')
        self.status.setObjectName('muted')
        self.status.setWordWrap(True)
        self.status.setMinimumHeight(36)
        right.addWidget(self.status)
        bottom = QHBoxLayout()
        bottom.addWidget(button('帮助', self.help))
        bottom.addStretch()
        self.write_button = button('写入当前键', lambda: self.write(False), True)
        self.all_button = button('写入全部', lambda: self.write(True))
        bottom.addWidget(self.all_button)
        bottom.addWidget(self.write_button)
        right.addLayout(bottom)
        columns.addWidget(editor, 1)
        outer.addWidget(body, 1)
        self.autosave = QTimer(self)
        self.autosave.setSingleShot(True)
        self.autosave.timeout.connect(self.save_session)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh_device)
        self.timer.start(2000)
        self.select(0)
        self.refresh_device()
        QApplication.instance().installEventFilter(self)
        QApplication.instance().applicationStateChanged.connect(self.application_state_changed)
        if self.locator.pending:
            QTimer.singleShot(200, lambda: self.toggle_location(False))

    def profile(self):
        return {'version': 2, 'keys': copy.deepcopy(self.keys)}

    def save_session(self):
        try:
            save_json(self.data_dir / 'last-session.json', self.profile())
            return True
        except OSError as error:
            self.status.setText(f'方案暂存失败，请另存方案：{error}')
            return False

    def changed(self, *args):
        if self.loading:
            return
        key = self.keys[self.index]
        key['name'] = self.name.text()
        key['shortcut'] = self.shortcut.text().strip()
        key['steps'] = [self.steps.itemWidget(self.steps.item(i)).value() for i in range(self.steps.count())]
        self.update_state()
        self.autosave.start(500)

    def set_mode(self, mode):
        self.keys[self.index]['mode'] = mode
        self.select(self.index)
        self.autosave.start(500)

    def select(self, index):
        self.loading = True
        self.index = index
        key = self.keys[index]
        self.heading.setText(f'按键 {index+1}')
        self.write_button.setText(f'写入按键 {index+1}')
        self.name.setText(key['name'])
        self.shortcut.setText(key['shortcut'])
        self.steps.clear()
        for step in key['steps']:
            self.append_step(step)
        self.pages.setCurrentIndex(int(key['mode'] == 'macro'))
        self.tabs[0].setChecked(key['mode'] == 'shortcut')
        self.tabs[1].setChecked(key['mode'] == 'macro')
        self.hint.setText('唤起程序时，请使用该程序提供的全局快捷键。' if index == 3 else '支持普通按键以及 Ctrl、Shift、Alt、Win 组合。')
        self.loading = False
        self.update_state()

    def update_state(self):
        for i, key in enumerate(self.keys):
            label = key['name'] or (key['shortcut'] if key['mode'] == 'shortcut' else f"{len(key['steps'])} 步宏") or '自定义'
            self.cards[i].setText(f'{i+1}\n{label[:16]}')
            self.cards[i].setToolTip(label)
            self.cards[i].setChecked(i == self.index)
        try:
            seq = sequence(self.keys[self.index])
            fingerprint = encode_key(self.index+1, seq) if seq else None
        except ValueError:
            fingerprint = None
        self.badge.setText('已发送' if fingerprint and self.sent.get(self.index+1) == fingerprint else '未写入')
        self.count.setText(f'{self.steps.count()} / 12')
        self.record_button.setEnabled(self.steps.count() < 12)
        self.write_button.setEnabled(self.connected and fingerprint is not None)
        self.all_button.setEnabled(self.connected)
        self.locate_toggle.blockSignals(True)
        self.locate_toggle.setChecked(self.locator.active)
        self.locate_toggle.blockSignals(False)
        self.locate_toggle.setEnabled(self.connected and not (self.locator.pending and not self.locator.active))
        self.recover_button.setVisible(self.locator.pending and not self.locator.active)
        self.locate_state.setText('定位中' if self.locator.active else '需恢复' if self.locator.pending else '')
        if self.locator.pending and not self.locator.active:
            self.write_button.setEnabled(False)
            self.all_button.setEnabled(False)
        QApplication.instance().setProperty('key4Locating', self.locator.pending)

    def refresh_device(self):
        if self.worker and self.worker.isRunning():
            return
        was_connected = self.connected
        try:
            matches = devices()
            self.connected = len(matches) == 1
            self.connection.setText('● 已连接' if self.connected else ('未连接' if not matches else '请只连接一台'))
            if not self.connected:
                self.sent.clear()
        except Exception as error:
            self.connected = False
            self.connection.setText('检测失败')
            self.connection.setToolTip(str(error))
        self.update_state()
        if self.locator.active and not self.connected:
            self.toggle_location(False)
        elif self.locator.pending and not self.locator.active and self.connected and not was_connected:
            QTimer.singleShot(0, lambda: self.toggle_location(False))

    def toggle_location(self, enable):
        if self.worker and self.worker.isRunning():
            if not enable:
                self.restore_on_idle = True
            return
        if enable:
            try:
                if not self.locator.can_start(self.locator.target()):
                    raise ValueError('请先写入完整四键正式方案，才能保存恢复依据并开启定位。')
                if self.reader is None:
                    self.reader = KeyboardReader(int(self.winId()))
            except Exception as error:
                self.status.setText(str(error))
                self.update_state()
                return
        elif not self.locator.pending:
            self.update_state()
            return
        self.location_error = ''
        self.status.setText('正在临时绑定定位键…' if enable else '正在恢复正式功能…')
        self.centralWidget().setEnabled(False)
        self.worker = LocationWorker(self.locator, enable)
        self.worker.result.connect(self.location_result)
        self.worker.finished.connect(self.location_finished)
        self.worker.start()

    def location_result(self, error):
        self.location_error = error
        if error:
            self.status.setText(error)
        elif self.locator.active:
            self.status.setText('按小键盘即可选中对应键；切换应用会自动恢复正式功能。')
        else:
            self.status.setText('正式功能已恢复。未写入的编辑内容仍保留。')

    def location_finished(self):
        self.centralWidget().setEnabled(True)
        if not self.locator.active:
            self.close_reader()
            self.restore_on_idle = False
        self.update_state()
        if self.location_error:
            self.exit_requested = False
            self.restore_on_idle = False
            return
        if self.locator.active and (self.restore_on_idle or self.exit_requested or QApplication.applicationState() != Qt.ApplicationState.ApplicationActive):
            self.restore_on_idle = False
            QTimer.singleShot(0, lambda: self.toggle_location(False))
        elif self.exit_requested:
            QTimer.singleShot(0, self.close)

    def close_reader(self):
        if self.reader:
            self.reader.close()
            self.reader = None
        self.held_markers.clear()

    def application_state_changed(self, state):
        if state != Qt.ApplicationState.ApplicationActive:
            if self.worker and self.worker.isRunning():
                self.restore_on_idle = True
            elif self.locator.active:
                self.toggle_location(False)

    def eventFilter(self, obj, event):
        if self.locator.pending and event.type() in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease, QEvent.Type.ShortcutOverride):
            if int(Qt.Key.Key_F13) <= event.key() <= int(Qt.Key.Key_F16):
                event.accept()
                return True
        return False

    def handle_location_input(self, event):
        vk = event['vkey']
        if event['released']:
            self.held_markers.discard(vk)
            return
        if not self.locator.active or (self.worker and self.worker.isRunning()):
            return
        if QApplication.activeModalWidget() is not None:
            return
        if vk in MARKER_VKEYS and vk not in self.held_markers:
            self.held_markers.add(vk)
            self.select(MARKER_VKEYS[vk])

    def nativeEvent(self, event_type, message):
        if self.reader:
            try:
                event = self.reader.read(message)
                if event:
                    self.handle_location_input(event)
            except Exception as error:
                self.status.setText(f'定位读取失败：{error}；正在恢复。')
                QTimer.singleShot(0, lambda: self.toggle_location(False))
        return False, 0

    def capture_shortcut(self):
        dialog = CaptureDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.steps:
            self.shortcut.setText(dialog.steps[0]['shortcut'])
        dialog.deleteLater()

    def append_step(self, step):
        item = QListWidgetItem()
        item.setSizeHint(QSize(0, 62))
        self.steps.addItem(item)
        row = StepRow(step)
        row.changed.connect(self.changed)
        row.removed.connect(self.remove_step)
        self.steps.setItemWidget(item, row)

    def add_step(self):
        if self.steps.count() >= 12:
            self.status.setText('每个按键最多支持 12 步。')
            return
        self.append_step({'shortcut': '', 'delay_ms': 100})
        self.changed()
        item = self.steps.item(self.steps.count()-1)
        self.steps.setCurrentItem(item)
        self.steps.itemWidget(item).edit.setFocus()

    def remove_step(self, widget):
        for i in range(self.steps.count()):
            if self.steps.itemWidget(self.steps.item(i)) is widget:
                self.steps.takeItem(i)
                widget.deleteLater()
                self.changed()
                return

    def move(self, delta):
        i = self.steps.currentRow()
        j = i + delta
        if i < 0 or not 0 <= j < self.steps.count():
            return
        values = self.keys[self.index]['steps']
        values[i], values[j] = values[j], values[i]
        self.select(self.index)
        self.steps.setCurrentRow(j)
        self.autosave.start(500)

    def record_macro(self):
        dialog = CaptureDialog(self, macro=True, capacity=12-self.steps.count())
        if dialog.exec() == QDialog.DialogCode.Accepted:
            for step in dialog.steps:
                self.append_step(step)
            self.changed()
        dialog.deleteLater()

    def reset(self):
        answer = QMessageBox.question(self, '恢复默认方案', '恢复为 Backspace、Delete、Enter，第四键留空？\n仅修改编辑内容，不写入键盘。')
        if answer == QMessageBox.StandardButton.Yes:
            self.keys = defaults()
            self.select(0)
            self.save_session()

    def open_profile(self):
        path, _ = QFileDialog.getOpenFileName(self, '打开方案', str(ROOT), 'Key4 方案 (*.json)')
        if not path:
            return
        try:
            candidate = load_profile(json.loads(Path(path).read_text(encoding='utf-8')))
            save_json(self.data_dir / 'previous-session.json', self.profile())
            self.keys = candidate
            self.select(self.index)
            self.save_session()
            self.status.setText('方案已打开，尚未写入键盘。')
        except (OSError, ValueError, TypeError, KeyError) as error:
            QMessageBox.warning(self, '无法打开', str(error))

    def save_profile(self):
        path, _ = QFileDialog.getSaveFileName(self, '保存方案', str(ROOT / '我的方案.json'), 'Key4 方案 (*.json)')
        if path:
            try:
                save_json(path, self.profile())
                self.status.setText('方案已保存到电脑。')
            except OSError as error:
                QMessageBox.warning(self, '无法保存', str(error))

    def write(self, all_keys=False):
        if self.worker and self.worker.isRunning():
            return
        try:
            plan, skipped = write_plan(self.keys, None if all_keys else self.index)
        except ValueError as error:
            QMessageBox.warning(self, '请完善设置', str(error))
            return
        self.pending_plan = plan
        self.skipped = skipped
        self.save_session()
        self.status.setText('正在写入…')
        self.centralWidget().setEnabled(False)
        self.worker = Writer(plan, self.data_dir / 'writes', self.locator)
        self.worker.result.connect(self.write_done)
        self.worker.finished.connect(self.write_finished)
        self.worker.start()

    def write_done(self, sent, error):
        for key, steps in self.pending_plan:
            if key in sent:
                self.sent[key] = encode_key(key, steps)
        if error:
            self.status.setText(f'写入未完成：{error}。已发送：{sent or "无"}；请检查实际键位后重试。')
        else:
            text = '已发送按键 ' + '、'.join(map(str, sent)) + '，请按键验证。'
            if self.skipped:
                text += '未设置的键 ' + '、'.join(map(str, self.skipped)) + ' 已跳过。'
            self.status.setText(text)

    def write_finished(self):
        self.centralWidget().setEnabled(True)
        if not self.locator.active:
            self.close_reader()
        self.update_state()
        self.restore_on_idle = False
        if self.exit_requested and not self.locator.pending:
            QTimer.singleShot(0, self.close)

    def help(self):
        QMessageBox.information(self, '使用说明',
            '1. 用鼠标选择左侧按键，编辑快捷键或宏。\n'
            '2. 可录入或手动填写组合键，例如 Ctrl+Alt+P。\n'
            '3. 点击写入后生效；第四键留空时会跳过，不会清空旧配置。\n\n'
            '宏最多 12 步，每步一个普通键加修饰键，延时 0–10000 毫秒。\n'
            '录制仅在录制窗口内进行；系统快捷键请手动填写。\n'
            '本程序不读取键盘现有配置；方案和上次编辑内容保存在电脑。\n'
            '定位会临时改为 F13–F16；关闭、退出、切换应用时恢复正式功能。\n'
            '录入或录制时不自动切换；点击写入会先退出定位再应用修改。\n'
            '请先写入四键正式方案建立恢复记录。若断连，接回原 USB 口重试恢复。\n'
            '异常退出时无法立即恢复；下次启动会根据记录自动尝试恢复。\n'
            '“已发送”表示传输完成，需实测按键顺序、执行效果及拔插后保存。\n'
            '程序不后台常驻；第四键使用目标程序自己的全局快捷键。')

    def closeEvent(self, event):
        if self.worker and self.worker.isRunning():
            self.exit_requested = True
            event.ignore()
            return
        if self.locator.pending:
            self.exit_requested = True
            event.ignore()
            self.toggle_location(False)
            return
        self.autosave.stop()
        if not self.save_session():
            if QMessageBox.question(self, '暂存失败', '未能保存本地方案，仍要退出吗？') != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
        self.close_reader()
        QApplication.instance().removeEventFilter(self)
        event.accept()


STYLE = '''
QWidget { background: #ffffff; color: #202633; font-size: 14px; font-family: "Microsoft YaHei UI"; }
QWidget#sidebar, QWidget#sidebar QLabel { background: #fafbfe; }
QWidget#header { border-bottom: 1px solid #dce1eb; }
QLabel#heading { font-size: 24px; font-weight: 600; }
QLabel#badge { color: #956020; background: #fff0d9; border-radius: 5px; padding: 5px 9px; }
QLabel#muted { color: #727b8b; font-size: 12px; }
QPushButton { background: white; border: 1px solid #d7dce6; border-radius: 7px; padding: 9px 12px; }
QPushButton:hover { border-color: #7e9be5; background: #f3f6ff; }
QPushButton:checked { color: #234fc1; background: #eaf0ff; border-color: #4e78dd; }
QPushButton#keycard { font-size: 20px; padding: 16px 4px; text-align: center; }
QPushButton#keycard:checked { border: 2px solid #4e78dd; }
QPushButton#primary { background: #3466df; color: white; border: 1px solid #3466df; }
QPushButton:disabled, QPushButton#primary:disabled { background: #edf0f5; color: #9aa2b0; border-color: #e0e5ed; }
QLineEdit, QSpinBox { background: white; border: 1px solid #d7dce6; border-radius: 6px; padding: 10px; selection-background-color: #d4e2ff; selection-color: #202633; }
QLineEdit:focus, QSpinBox:focus { border-color: #4e78dd; }
QListWidget { border: 1px solid #dce1eb; border-radius: 7px; background: #fff; }
QListWidget::item:selected { background: #edf3ff; }
QListWidget QWidget { background: transparent; }
QCheckBox { spacing: 12px; }
QCheckBox::indicator { width: 36px; height: 20px; border-radius: 10px; border: 1px solid #cbd1dc; background: #e7eaf0; }
QCheckBox::indicator:checked { background: #3466df; border: 1px solid #3466df; }
'''


def main():
    app = QApplication(sys.argv)
    app.setFont(QFont('Microsoft YaHei UI', 10))
    app.setStyle('Fusion')
    app.setStyleSheet(STYLE)
    smoke = '--smoke-test' in sys.argv
    hardware_test = '--locate-integration-test' in sys.argv
    folder = Path(os.environ.get('KEY4_SMOKE_DIR', str(ROOT / 'tmp')))
    data_dir = folder / 'session' if smoke or hardware_test else Path(os.environ.get('LOCALAPPDATA', str(ROOT))) / 'Key4'
    data_dir.mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(data_dir / 'app.lock'))
    lock.setStaleLockTime(0)
    if not lock.tryLock(0):
        QMessageBox.information(None, 'Key4', 'Key4 已在运行，请使用已打开的窗口。')
        return 0
    window = Window(data_dir)
    window.show()
    if hardware_test:
        from integration_check import run_check
        run_check(window, folder)
    if smoke:
        def check():
            folder.mkdir(parents=True, exist_ok=True)
            window.select(3)
            window.grab().save(str(folder / 'shortcut.png'))
            window.keys[3] = dict(name='粘贴后回车', mode='macro', shortcut='', steps=[
                dict(shortcut='Ctrl+V', delay_ms=200), dict(shortcut='Enter', delay_ms=100)])
            window.select(3)
            QApplication.processEvents()
            window.grab().save(str(folder / 'macro.png'))
            save_json(folder / 'smoke.json', {'device': window.connection.text(),
                      'defaults': defaults(), 'macro_rows': window.steps.count(), 'frozen': getattr(sys, 'frozen', False)})
            window.keys = defaults()
            window.close()
        QTimer.singleShot(1200, check)
    try:
        return app.exec()
    finally:
        if window.locator.pending and not (window.worker and window.worker.isRunning()):
            try:
                window.locator.restore()
            except Exception:
                # Durable journal is retained for retry on the next launch.
                pass
        lock.unlock()


if __name__ == '__main__':
    sys.exit(main())
