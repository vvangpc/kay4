"""HX configuration protocol, recovered from the supplied HxTool.exe.

Only single-key configuration is used. No firmware or batch erase commands.
"""
import time

VID, PID, INTERFACE = 0x413D, 0x553A, 2
MAX_STEPS = 12
KEYS = {chr(65 + i): 4 + i for i in range(26)}
KEYS.update({str(i): 29 + i for i in range(1, 10)})
KEYS['0'] = 39
KEYS.update(dict(zip(
    ['Enter', 'Esc', 'Backspace', 'Tab', 'Space', '-', '=', '[', ']', '\\'],
    range(40, 50))))
KEYS.update(dict(zip([';', "'", '`', ',', '.', '/'], range(51, 57))))
KEYS['CapsLock'] = 57
KEYS.update({f'F{i}': 57 + i for i in range(1, 13)})
KEYS.update({f'F{i}': 91 + i for i in range(13, 25)})
KEYS.update(dict(zip(['PrintScreen', 'ScrollLock', 'Pause', 'Insert', 'Home',
    'PageUp', 'Delete', 'End', 'PageDown', 'Right', 'Left', 'Down', 'Up', 'NumLock'], range(70, 84))))
MODIFIERS = {'Ctrl': 1, 'Shift': 2, 'Alt': 4, 'Win': 8}
ALIASES = {'Return': 'Enter', 'Escape': 'Esc', 'Del': 'Delete', 'Ins': 'Insert',
           'PgUp': 'PageUp', 'PgDown': 'PageDown', 'Meta': 'Win', 'Print': 'PrintScreen'}


def parse_shortcut(text):
    if not isinstance(text, str) or not text.strip():
        raise ValueError('请先录入快捷键。')
    text = text.strip()
    # Qt can report shifted punctuation as the resulting character.
    shifted = dict(zip('!@#$%^&*()_+{}|:"<>?~', '1234567890-=[]\\;\',./`'))
    implicit_shift = text[-1] in shifted
    if implicit_shift:
        text = text[:-1] + shifted[text[-1]]
    parts = text.split('+')
    parts = [ALIASES.get(p.strip(), p.strip()) for p in parts]
    key = parts[-1]
    if len(key) == 1 and key.isalpha():
        key = key.upper()
    mods = parts[:-1]
    if implicit_shift and 'Shift' not in mods:
        mods.append('Shift')
    if key not in KEYS or any(m not in MODIFIERS for m in mods) or len(set(mods)) != len(mods):
        raise ValueError(f'不支持的按键组合：{text}。每步支持修饰键加一个普通按键。')
    return sum(MODIFIERS[m] for m in mods), KEYS[key]


def crc8(data):
    crc = 0
    for value in data:
        crc ^= value
        for _ in range(8):
            crc = ((crc << 1) ^ (7 if crc & 128 else 0)) & 255
    return crc


def encode_key(key_id, steps):
    if type(key_id) is not int or not 1 <= key_id <= 4:
        raise ValueError('按键编号必须为 1–4。')
    if not isinstance(steps, list) or len(steps) > MAX_STEPS:
        raise ValueError('每个按键最多支持 12 步。')
    packet = bytearray(64)
    packet[1:4] = bytes([0x10, key_id, len(steps)])
    for i, step in enumerate(steps):
        if not isinstance(step, dict):
            raise ValueError('宏步骤格式不正确。')
        delay = step.get('delay_ms')
        if type(delay) is not int or not 0 <= delay <= 10000:
            raise ValueError('延时必须为 0–10000 毫秒的整数。')
        modifier, key = parse_shortcut(step.get('shortcut'))
        offset = 4 + i * 4
        packet[offset:offset+4] = bytes([modifier, key, delay >> 8, delay & 255])
    packet[63] = crc8(packet[:63])
    return bytes(packet)


def devices():
    import hid
    return [d for d in hid.enumerate(VID, PID) if d['interface_number'] == INTERFACE]


def write_key(key_id, steps, expected_path=None):
    packet = encode_key(key_id, steps)
    import hid
    matches = devices()
    if len(matches) != 1:
        raise RuntimeError('未找到小键盘，或连接了多台同型号设备。请只连接一台。')
    if expected_path is not None and matches[0]['path'] != expected_path:
        raise RuntimeError('键盘接口已改变，请将原键盘连接回原来的 USB 接口。')
    device = hid.device()
    try:
        device.open_path(matches[0]['path'])
        count = device.write(packet)
        if count not in (64, 65):
            raise RuntimeError(f'数据发送不完整：{count} 字节。')
        # The supplied utility waits 0.5 seconds; it does not validate an ACK.
        time.sleep(0.5)
    finally:
        device.close()
