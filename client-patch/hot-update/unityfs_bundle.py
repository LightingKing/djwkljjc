"""Length-preserving patcher for the original LJJC login AssetBundle (UnityFS v6, 5.6.6f2).

Re-emits a byte-faithful UnityFS container (own writer), optionally swapping the single
standalone luac string constant b"Account" -> b"LJJCHOT" (same length) as a load marker.
Avoids UnityPy's writer, which produces an archive Unity 5.6.6 cannot open.

Usage: patch_login_bundle.py <orig_bundle> <out_bundle> [roundtrip|marker]
"""
import sys, struct
import lz4.block as lb

MAGIC_CIPHER = b"FCI\x00"
MAGIC_PLAIN4 = bytes([0x00, 0x66, 0x63, 0x69])
STANDALONE = b"\x04\x08Account"   # luac short-string const tag(0x04)+len(7+1=8)+"Account"
MARKED     = b"\x04\x08LJJCHOT"

def decode_fci(raw):
    assert raw[:4] == MAGIC_CIPHER
    body = raw[4:]
    key = [body[j] ^ MAGIC_PLAIN4[j] for j in range(4)]
    out = bytearray(len(body))
    for i in range(len(body)):
        out[i] = body[i] ^ key[(i % 4 + i // 4) % 4]
    return bytes(out), key

def encode_fci(plain, key):
    body = bytearray(len(plain))
    for i in range(len(plain)):
        body[i] = plain[i] ^ key[(i % 4 + i // 4) % 4]
    return MAGIC_CIPHER + bytes(body)

def cstr(b, o):
    e = b.index(0, o); return b[o:e], e + 1

def parse(d):
    assert d[:8] == b'UnityFS\0'
    p = 8
    ver = struct.unpack('>I', d[p:p+4])[0]; p += 4
    uv, p = cstr(d, p); ur, p = cstr(d, p)
    size = struct.unpack('>q', d[p:p+8])[0]; p += 8
    cbi = struct.unpack('>I', d[p:p+4])[0]; p += 4
    ubi = struct.unpack('>I', d[p:p+4])[0]; p += 4
    flags = struct.unpack('>I', d[p:p+4])[0]; p += 4
    hdr_end = p
    comp = flags & 0x3f
    at_end = bool(flags & 0x80)
    bi_off = (len(d) - cbi) if at_end else hdr_end
    raw_bi = d[bi_off:bi_off+cbi]
    bi = raw_bi if comp == 0 else lb.decompress(raw_bi, uncompressed_size=ubi)
    q = 0
    datahash = bi[q:q+16]; q += 16
    bcount = struct.unpack('>i', bi[q:q+4])[0]; q += 4
    blocks = []
    for _ in range(bcount):
        u, c = struct.unpack('>II', bi[q:q+8]); q += 8
        bf = struct.unpack('>H', bi[q:q+2])[0]; q += 2
        blocks.append((u, c, bf))
    ncount = struct.unpack('>i', bi[q:q+4])[0]; q += 4
    nodes = []
    for _ in range(ncount):
        off, sz = struct.unpack('>qq', bi[q:q+16]); q += 16
        nf = struct.unpack('>I', bi[q:q+4])[0]; q += 4
        nm, q = cstr(bi, q)
        nodes.append((off, sz, nf, nm))
    data_off = hdr_end if at_end else hdr_end + cbi
    raw = d[data_off:]; chunks = []; pos = 0
    for (u, c, bf) in blocks:
        blk = raw[pos:pos+c]; pos += c
        ct = bf & 0x3f
        chunks.append(blk if ct == 0 else lb.decompress(blk, uncompressed_size=u))
    return dict(uv=uv, ur=ur, datahash=datahash, blocks=blocks, nodes=nodes, data=b''.join(chunks))

def build_uncompressed(info, data):
    bi = bytearray()
    bi += info['datahash']
    bi += struct.pack('>i', 1)
    bi += struct.pack('>IIH', len(data), len(data), 0)
    bi += struct.pack('>i', len(info['nodes']))
    for (off, sz, nf, nm) in info['nodes']:
        bi += struct.pack('>qqI', off, sz, nf) + nm + b'\0'
    bi = bytes(bi)
    hdr = bytearray(b'UnityFS\0')
    hdr += struct.pack('>I', 6)
    hdr += info['uv'] + b'\0' + info['ur'] + b'\0'
    size_pos = len(hdr)
    hdr += struct.pack('>q', 0)
    hdr += struct.pack('>I', len(bi))
    hdr += struct.pack('>I', len(bi))
    hdr += struct.pack('>I', 0x40)
    out = bytes(hdr) + bi + data
    return out[:size_pos] + struct.pack('>q', len(out)) + out[size_pos+8:]

def payload_plain(data):
    off = data.find(MAGIC_CIPHER)
    assert off != -1 and data.find(MAGIC_CIPHER, off+1) == -1
    plen = struct.unpack('<i', data[off-4:off])[0]
    plain, key = decode_fci(bytes(data[off:off+plen]))
    return off, plen, plain, key

def main():
    orig, outp = sys.argv[1], sys.argv[2]
    mode = sys.argv[3] if len(sys.argv) > 3 else "marker"
    info = parse(open(orig, 'rb').read())
    data = bytearray(info['data'])
    off, plen, plain, key = payload_plain(data)
    if mode == "marker":
        n = plain.count(STANDALONE)
        assert n == 1, "expected exactly one standalone Account constant, got %d" % n
        new_plain = plain.replace(STANDALONE, MARKED)
        assert len(new_plain) == len(plain)
        data[off:off+plen] = encode_fci(new_plain, key)
    out = build_uncompressed(info, bytes(data))
    open(outp, 'wb').write(out)
    # verify: re-parse our output and decode the FCI payload back to plaintext
    chk = parse(out)
    _, _, cplain, _ = payload_plain(bytearray(chk['data']))
    want_mark = 1 if mode == "marker" else 0
    got_mark = cplain.count(MARKED)
    got_acct = cplain.count(STANDALONE)
    assert chk['nodes'] == info['nodes']
    print("WROTE %s size=%d mode=%s standalone-LJJCHOT=%d standalone-Account=%d node=%r"
          % (outp, len(out), mode, got_mark, got_acct, chk['nodes'][0][3].decode()))
    assert got_mark == want_mark, (got_mark, want_mark)

if __name__ == '__main__':
    main()
