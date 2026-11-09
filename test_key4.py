import os
import sys
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock
sys.path.insert(0, str(Path(__file__).parent / '.deps'))
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
from protocol import crc8, encode_key, parse_shortcut, write_key
from profiles import defaults, load_profile, write_plan

class ProtocolTests(unittest.TestCase):
    def test_crc_and_wire(self):
        self.assertEqual(crc8(b'123456789'), 0xF4)
        p = encode_key(3, [{'shortcut':'Ctrl+Shift+C','delay_ms':1000}, {'shortcut':'Meta+R','delay_ms':50}])
        self.assertEqual(p[:12], bytes.fromhex('00 10 03 02 03 06 03 e8 08 15 00 32'))
        self.assertEqual(len(p),64)
        self.assertEqual(p[63],crc8(p[:63]))
        self.assertEqual(parse_shortcut('Ctrl++'),(3,46))

    def test_defaults_and_skip(self):
        plan, skipped = write_plan(defaults())
        self.assertEqual([p[1][0]['shortcut'] for p in plan], ['Backspace','Delete','Enter'])
        self.assertEqual(skipped,[4])
        with self.assertRaises(ValueError): write_plan(defaults(),3)

    def test_invalid_before_io(self):
        with patch('protocol.devices') as d:
            for steps in [[{'shortcut':'Ctrl+Bad','delay_ms':100}], [{'shortcut':'A','delay_ms':-1}], [{'shortcut':'A','delay_ms':True}], [{'shortcut':'A','delay_ms':0}]*13]:
                with self.assertRaises(ValueError): write_key(1,steps)
            d.assert_not_called()

    def test_transport_and_failure_close(self):
        d = MagicMock()
        with patch('protocol.devices',return_value=[{'path':b'target'}]), patch('hid.device',return_value=d), patch('protocol.time.sleep'):
            d.write.return_value = 64
            write_key(4,[{'shortcut':'Alt+F4','delay_ms':0}])
            self.assertEqual(d.write.call_args.args[0][2],4)
            d.write.return_value=10
            with self.assertRaises(RuntimeError): write_key(1,[])
        self.assertEqual(d.close.call_count,2)

    def test_profile_draft_and_limits(self):
        keys=defaults(); keys[3]['shortcut']='Ctrl+'
        self.assertEqual(load_profile({'version':2,'keys':keys}),keys)
        with self.assertRaises(ValueError): write_plan(keys)
        keys[3]['steps']=[{'shortcut':'A','delay_ms':100}]*13
        with self.assertRaises(ValueError): load_profile({'version':2,'keys':keys})

class EditorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app=QApplication.instance() or QApplication([])

    def test_edit_mode_reorder_and_reload(self):
        from app import Window
        from PySide6.QtCore import QModelIndex
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent/'tmp') as folder, patch('app.devices',return_value=[{}]):
            w=Window(folder)
            w.select(3); w.shortcut.setText('Ctrl+Alt+P'); w.name.setText('唤起')
            w.set_mode('macro'); w.add_step()
            w.steps.itemWidget(w.steps.item(0)).edit.setText('Ctrl+V')
            w.add_step(); w.steps.itemWidget(w.steps.item(1)).edit.setText('Enter')
            w.steps.setCurrentRow(1); w.move(-1)
            self.assertEqual(w.keys[3]['steps'][0]['shortcut'],'Enter')
            # Qt internal move is the same model operation used for drag reorder.
            moved=w.steps.model().moveRows(QModelIndex(),0,1,QModelIndex(),2)
            self.assertTrue(moved)
            self.assertEqual(w.keys[3]['steps'][0]['shortcut'],'Ctrl+V')
            w.set_mode('shortcut')
            self.assertEqual(w.shortcut.text(),'Ctrl+Alt+P')
            w.select(0); self.assertEqual(w.shortcut.text(),'Backspace')
            w.close()
            w2=Window(folder)
            self.assertEqual(w2.keys[3]['name'],'唤起')
            self.assertEqual(len(w2.keys[3]['steps']),2)
            w2.close()

    def test_recording_enter_backspace_and_capacity(self):
        from capture import CaptureDialog
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        d=CaptureDialog(macro=True,capacity=3)
        d.setModal(True); d.show(); d.activateWindow(); self.app.processEvents()
        QTest.keyClick(d,Qt.Key.Key_C,Qt.KeyboardModifier.ControlModifier)
        QTest.keyClick(d,Qt.Key.Key_Backspace)
        QTest.keyClick(d,Qt.Key.Key_Return)
        QTest.keyClick(d,Qt.Key.Key_A)
        self.assertEqual([s['shortcut'] for s in d.steps],['Ctrl+C','Backspace','Return'])
        self.assertTrue(d.isVisible())
        d.accept(); self.assertFalse(d.isVisible())
        d.deleteLater()

    def test_partial_write_reports_exact_keys(self):
        from app import Writer
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent/'tmp') as folder:
            plan,_=write_plan(defaults())
            worker=Writer(plan,Path(folder))
            results=[]; worker.result.connect(lambda sent,error:results.append((sent,error)))
            with patch('app.write_key',side_effect=[None,RuntimeError('unplugged')]) as io:
                worker.run()
            self.assertEqual(io.call_count,2)
            self.assertEqual(results[0][0],[1])
            self.assertIn('unplugged',results[0][1])
            record=json.loads(next(Path(folder).glob('*.json')).read_text(encoding='utf-8'))
            self.assertEqual(record['state'],'incomplete')

if __name__=='__main__': unittest.main()
