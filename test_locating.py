import sys
import os
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock
sys.path.insert(0, str(Path(__file__).parent / '.deps'))
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
from locating import Locator, MARKERS
from profiles import save_json

FORMAL = {str(i): [{'shortcut': k, 'delay_ms': 100}] for i, k in enumerate(('Backspace','Delete','Enter','Enter'),1)}


class TransactionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).parent/'tmp')
        self.folder = Path(self.temp.name)
        save_json(self.folder/'committed.json', {'target': b'device'.hex(), 'keys': FORMAL})
        self.calls = []

    def tearDown(self):
        self.temp.cleanup()

    def locator(self, writer=None):
        return Locator(self.folder, writer=writer or (lambda key, steps, **kw:self.calls.append((key,steps))), target=lambda:b'device')

    def test_journal_before_first_write_and_exact_restore(self):
        def write(key, steps, **kwargs):
            journal=json.loads((self.folder/'locate-recovery.json').read_text(encoding='utf-8'))
            self.assertEqual(journal['keys'],FORMAL)
            self.calls.append((key,steps))
        loc=self.locator(write)
        loc.start()
        self.assertTrue(loc.active)
        self.assertEqual([x[1][0]['shortcut'] for x in self.calls],list(MARKERS))
        loc.restore()
        self.assertFalse(loc.pending)
        self.assertEqual([x[1][0]['shortcut'] for x in self.calls[4:]],['Backspace','Delete','Enter','Enter'])

    def test_partial_start_rolls_back_all_keys(self):
        def write(key, steps, **kw):
            if steps[0]['shortcut']=='F14': raise OSError('unplugged')
            self.calls.append((key,steps))
        loc=self.locator(write)
        with self.assertRaisesRegex(RuntimeError,'原配置已恢复'): loc.start()
        self.assertFalse(loc.pending)
        self.assertEqual([x[1][0]['shortcut'] for x in self.calls[-4:]],['Backspace','Delete','Enter','Enter'])

    def test_crash_recovery_does_not_need_memory_state(self):
        loc=self.locator(); loc.start()
        recovered=self.locator()
        self.assertFalse(recovered.active)
        self.assertTrue(recovered.pending)
        recovered.restore()
        self.assertFalse(recovered.pending)

    def test_failed_restore_retains_journal_and_retry(self):
        loc=self.locator(); loc.start()
        loc.writer=MagicMock(side_effect=OSError('disconnect'))
        with self.assertRaises(RuntimeError): loc.restore()
        self.assertTrue(loc.pending)
        self.assertFalse(loc.active)
        loc.writer=lambda *a,**kw:None
        loc.restore()
        self.assertFalse(loc.pending)

    def test_wrong_device_and_unknown_key_prevent_overwrite(self):
        loc=self.locator(); loc.start(); loc.target=lambda:b'other'
        with self.assertRaisesRegex(RuntimeError,'原 USB'): loc.restore()
        self.assertTrue(loc.pending)
        loc.target=lambda:b'device'; loc.restore()
        loc.invalidate(b'device',4)
        self.assertFalse(loc.can_start(b'device'))
        with self.assertRaises(ValueError): loc.start()

    def test_failed_journal_save_never_touches_hardware(self):
        loc=self.locator()
        with patch('locating.save_json',side_effect=OSError('disk full')):
            with self.assertRaises(OSError): loc.start()
        self.assertEqual(self.calls,[])


class UIIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app=QApplication.instance() or QApplication([])

    def test_selection_freezes_in_capture_and_marker_not_recorded(self):
        from app import Window
        from capture import CaptureDialog
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent/'tmp') as folder, patch('app.devices',return_value=[{}]):
            window=Window(folder)
            window.locator.active=True
            window.handle_location_input({'vkey':0x7E,'released':False})
            self.assertEqual(window.index,2)
            window.handle_location_input({'vkey':0x7E,'released':True})
            dialog=CaptureDialog(window,macro=True)
            dialog.setModal(True); dialog.show(); self.app.processEvents()
            self.app.setProperty('key4Locating',True)
            window.handle_location_input({'vkey':0x7C,'released':False})
            self.assertEqual(window.index,2)
            QTest.keyClick(dialog,Qt.Key.Key_F13)
            self.assertEqual(dialog.steps,[])
            QTest.keyClick(dialog,Qt.Key.Key_C,Qt.KeyboardModifier.ControlModifier)
            self.assertEqual(dialog.steps[0]['shortcut'],'Ctrl+C')
            dialog.reject()
            window.handle_location_input({'vkey':0x7C,'released':False})
            self.assertEqual(window.index,0)
            window.locator.active=False; window.close()
            self.app.setProperty('key4Locating',False)

    def test_normal_write_exits_locating_and_updates_only_written_key(self):
        from app import Writer
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent/'tmp') as folder:
            folder=Path(folder)
            save_json(folder/'committed.json',{'target':b'device'.hex(),'keys':FORMAL})
            calls=[]
            loc=Locator(folder,writer=lambda *a,**kw:calls.append(a),target=lambda:b'device')
            loc.start()
            with patch('app.write_key') as writer:
                worker=Writer([(4,[{'shortcut':'Ctrl+P','delay_ms':100}])],folder/'writes',loc)
                worker.run()
                writer.assert_called_once()
            self.assertFalse(loc.pending)
            self.assertEqual(loc.committed()['keys']['4'][0]['shortcut'],'Ctrl+P')
            self.assertEqual(loc.committed()['keys']['1'],FORMAL['1'])


if __name__=='__main__': unittest.main()
