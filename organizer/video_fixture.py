"""Tiny uncompressed AVI writer used only by the portable self-check."""
import struct


def make_video(path, seconds=8, fps=20, width=160, height=120):
    def chunk(name, payload):
        return name + struct.pack('<I', len(payload)) + payload + (b'\0' if len(payload) % 2 else b'')
    def listing(name, payload):
        return chunk(b'LIST', name + payload)
    frames = fps*seconds
    frame_bytes = width*height*3
    avih = struct.pack('<14I', 1000000//fps, frame_bytes*fps, 0, 0x10, frames, 0, 1, frame_bytes, width, height, 0, 0, 0, 0)
    strh = struct.pack('<4s4sIHHIIIIIIIIhhhh', b'vids', b'DIB ', 0, 0, 0, 0, 1, fps, 0, frames, frame_bytes, 0xffffffff, 0, 0, 0, width, height)
    strf = struct.pack('<IiiHHIIiiII', 40, width, height, 1, 24, 0, frame_bytes, 0, 0, 0, 0)
    hdrl = listing(b'hdrl', chunk(b'avih', avih) + listing(b'strl', chunk(b'strh', strh) + chunk(b'strf', strf)))
    data, index, offset = [], [], 4
    for number in range(frames):
        pixels = bytearray(frame_bytes)
        for y in range(number % 70, number % 70 + 35):
            pixels[(y*width+30)*3:(y*width+70)*3] = bytes((120, 200, 180))*40
        payload = chunk(b'00db', pixels)
        data.append(payload)
        index.append(struct.pack('<4sIII', b'00db', 0x10, offset, frame_bytes))
        offset += len(payload)
    payload = b'AVI ' + hdrl + listing(b'movi', b''.join(data)) + chunk(b'idx1', b''.join(index))
    path.write_bytes(b'RIFF' + struct.pack('<I', len(payload)) + payload)
