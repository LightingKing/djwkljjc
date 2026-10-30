"""FCI <-> standard Lua 5.3 bytecode codec for 《电击文库：零境交错》.

The game's Lua modules are stored as:
  TextAsset m_Script = b"FCI\0" + XOR-ciphertext      (layer 1, see decode_fci/encode_fci)
  plaintext          = b"\x00fci" + <standard Lua 5.3 body after the 33-byte header>
                       with every instruction opcode permuted per fci-lua53.opmap.

This module converts between that FCI container and a stock Lua 5.3 luac file
(as produced by lupa's lua53 `string.dump`), so modified Lua *source* can be
compiled and repacked for delivery through the hot-update channel.
"""
from __future__ import annotations
import struct
from pathlib import Path

LUA53_HEADER = bytes.fromhex(
    "1b4c7561530019930d0a1a0a040804080878560000000000000000000000287740")
FCI_MAGIC4 = bytes([0x00, 0x66, 0x63, 0x69])          # replaces the 33-byte header
CIPHER_MAGIC = b"FCI\x00"
PLAIN_MAGIC4 = FCI_MAGIC4

# ---- layer 1: XOR cipher (position-rotated 4-byte key) --------------------
def decode_fci(raw: bytes):
    assert raw[:4] == CIPHER_MAGIC, raw[:4]
    body = raw[4:]
    key = [body[j] ^ PLAIN_MAGIC4[j] for j in range(4)]
    out = bytearray(len(body))
    for i in range(len(body)):
        out[i] = body[i] ^ key[(i % 4 + i // 4) % 4]
    return bytes(out), key

def encode_fci(plain: bytes, key):
    body = bytearray(len(plain))
    for i in range(len(plain)):
        body[i] = plain[i] ^ key[(i % 4 + i // 4) % 4]
    return CIPHER_MAGIC + bytes(body)

# ---- opcode permutation ---------------------------------------------------
STD_NAMES = ("move loadk loadkx loadbool loadnil getupval gettabup gettable "
             "settabup setupval settable newtable self add sub mul mod pow div "
             "idiv band bor bxor shl shr unm bnot not len concat jmp eq lt le "
             "test testset call tailcall return forloop forprep tforcall "
             "tforloop setlist closure vararg extraarg").split()

def _load_opmap(path: Path):
    fci_by_name = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line.startswith(".op"):
            continue
        _, num, name = line.split()
        fci_by_name[name] = int(num)
    return fci_by_name

_OPMAP_PATH = Path(__file__).with_name("fci-lua53.opmap")
_FCI_BY_NAME = _load_opmap(_OPMAP_PATH)
STD_TO_FCI = [ _FCI_BY_NAME[STD_NAMES[s]] for s in range(len(STD_NAMES)) ]  # std# -> fci#
FCI_TO_STD = [0]*len(STD_NAMES)
for s, f in enumerate(STD_TO_FCI):
    FCI_TO_STD[f] = s

# ---- Lua 5.3 bytecode parser (locate all instruction words) --------------
class _R:
    def __init__(self, d, p=0): self.d=d; self.p=p
    def byte(self):
        b=self.d[self.p]; self.p+=1; return b
    def u32(self):
        v=struct.unpack_from('<I', self.d, self.p)[0]; self.p+=4; return v
    def skip(self, n): self.p+=n
    def string(self):
        n=self.byte()
        if n==0xFF:
            n=struct.unpack_from('<Q', self.d, self.p)[0]; self.p+=8
        if n==0: return
        self.skip(n-1)   # stored size is len+1

def _parse_function(r: _R, code_spans: list):
    r.string()                 # source (NULL for nested)
    r.u32(); r.u32()           # linedefined, lastlinedefined
    r.byte(); r.byte(); r.byte()  # numparams, is_vararg, maxstacksize
    sizecode = r.u32()
    code_spans.append((r.p, sizecode))
    r.skip(sizecode*4)
    sizek = r.u32()            # constants
    for _ in range(sizek):
        t = r.byte()
        if t == 0: pass
        elif t == 1: r.byte()
        elif t in (3, 0x13): r.skip(8)
        elif t in (4, 0x14): r.string()
        else: raise ValueError("bad constant tag 0x%x at %d" % (t, r.p-1))
    nup = r.u32()              # upvalues
    r.skip(nup*2)
    sizep = r.u32()            # nested protos
    for _ in range(sizep):
        _parse_function(r, code_spans)
    sizelineinfo = r.u32()     # debug: lineinfo
    r.skip(sizelineinfo*4)
    sizelocvars = r.u32()
    for _ in range(sizelocvars):
        r.string(); r.u32(); r.u32()
    sizeupvalnames = r.u32()
    for _ in range(sizeupvalnames):
        r.string()

def _collect_code_spans(standard_luac: bytes):
    """standard_luac = 33-byte header + body. Returns list of (offset,count)."""
    r = _R(standard_luac, len(LUA53_HEADER))
    r.byte()                   # sizeupvalues of main closure
    spans = []
    _parse_function(r, spans)
    if r.p != len(standard_luac):
        raise ValueError("trailing bytes: parsed %d of %d" % (r.p, len(standard_luac)))
    return spans

def _remap(standard_luac: bytes, table) -> bytes:
    spans = _collect_code_spans(standard_luac)
    out = bytearray(standard_luac)
    for off, cnt in spans:
        for i in range(cnt):
            o = off + i*4
            instr = struct.unpack_from('<I', out, o)[0]
            op = instr & 0x3F
            instr = (instr & ~0x3F) | table[op]
            struct.pack_into('<I', out, o, instr)
    return bytes(out)

# ---- container conversions -----------------------------------------------
def fci_to_standard(container: bytes) -> bytes:
    """FCI plaintext container (b'\x00fci'+body, FCI opcodes) -> stock luac."""
    assert container[:4] == FCI_MAGIC4, container[:4]
    canonical = LUA53_HEADER + container[4:]     # swap header, FCI opcodes still
    return _remap(canonical, FCI_TO_STD)          # -> standard opcodes

def standard_to_fci(standard_luac: bytes) -> bytes:
    """stock luac -> FCI plaintext container."""
    assert standard_luac[:33] == LUA53_HEADER, standard_luac[:4]
    remapped = _remap(standard_luac, STD_TO_FCI)
    return FCI_MAGIC4 + remapped[len(LUA53_HEADER):]

if __name__ == "__main__":
    import sys
    print("opmap:", _OPMAP_PATH)
    print("STD_TO_FCI:", STD_TO_FCI)
    print("rotation check (std+17)%47:", all(STD_TO_FCI[s]==(s+17)%47 for s in range(47)))
