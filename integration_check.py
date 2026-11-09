"""Opt-in hardware acceptance test for the production window and workers."""
from collections import Counter
from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QApplication
from locating import MARKER_VKEYS
from raw_keyboard import KeyboardReader
from profiles import save_json


def run_check(window, folder):
    state = {'stage': 'opening', 'seen': [], 'formal': {}, 'events': []}
    original = window.handle_location_input
    formal = Counter()

    def save():
        save_json(folder / 'integration.json', state)

    def handle(event):
        original(event)
        if event['released']:
            return
        state['events'].append(dict(event, stage=state['stage']))
        if state['stage'] == 'locating' and event['vkey'] in MARKER_VKEYS:
            index = MARKER_VKEYS[event['vkey']]
            if window.index != index:
                state['error'] = 'selection mismatch'
                window.close()
                return
            state['seen'] = sorted(set(state['seen'] + [index]))
            print('LOCATED '+str(index+1), flush=True)
            if len(state['seen']) == 4:
                window.grab().save(str(folder / 'locating.png'))
                state['stage'] = 'stopping'
                QTimer.singleShot(250, lambda: window.toggle_location(False))
        elif state['stage'] == 'verifying' and event['vkey'] in (8, 46, 13):
            formal[event['vkey']] += 1
            state['formal'] = dict(formal)
            print('FORMAL '+str(event['vkey']), flush=True)
            if formal[8] >= 1 and formal[46] >= 1 and formal[13] >= 2:
                state['stage'] = 'exit_start'
                window.close_reader()
                QTimer.singleShot(100, lambda: window.toggle_location(True))
        save()

    window.handle_location_input = handle
    timer = QTimer(window)

    def tick():
        busy = window.worker and window.worker.isRunning()
        if busy:
            return
        if window.location_error:
            state['error'] = window.location_error
            save()
            timer.stop()
            window.close()
            return
        if state['stage'] == 'opening' and window.locator.active:
            state['stage'] = 'locating'
            window.status.setText('实机验收：请保持本窗口在前台，将四个实体键各按一次。')
            print('READY: press four pad keys once, keep this window foreground', flush=True)
        elif state['stage'] == 'stopping' and not window.locator.pending:
            window.reader = KeyboardReader(int(window.winId()))
            state['stage'] = 'verifying'
            window.status.setText('正式功能已恢复：请再将四个实体键各按一次，确认 Backspace / Delete / Enter / Enter。')
            print('RESTORED: press four pad keys again to verify formal functions', flush=True)
        elif state['stage'] == 'exit_start' and window.locator.active:
            state['stage'] = 'exit_restore'
            print('Testing exit restoration automatically', flush=True)
            QTimer.singleShot(0, window.close)
        elif state['stage'] == 'locating' and not window.locator.active:
            state['error'] = '定位期间窗口失去前台，正式功能已自动恢复。'
            timer.stop()
            window.close()
        save()

    def finished():
        state['recovery_pending'] = window.locator.pending
        state['state'] = ('complete' if len(state['seen']) == 4 and formal[8] >= 1
                          and formal[46] >= 1 and formal[13] >= 2 and not window.locator.pending
                          and state['stage'] == 'exit_restore' else 'incomplete')
        save()
        print('RESULT '+state['state'], flush=True)

    def start():
        window.raise_()
        window.activateWindow()
        window.toggle_location(True)

    timer.timeout.connect(tick)
    timer.start(100)
    QApplication.instance().aboutToQuit.connect(finished)
    QTimer.singleShot(700, start)
    QTimer.singleShot(180000, window.close)
    save()
