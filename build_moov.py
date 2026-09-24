"""
build_moov.py - Build a playable MP4 from a Sony RSV file using a donor MP4.

The RSV file IS the MP4 mdat content with 3 interleaved tracks:
  Track order per chunk: metadata(rtmd) -> video(H.264) -> audio(PCM s16be)
  
This script:
1. Reads the donor MP4's moov to extract ftyp, codec config, timing info
2. Parses the RSV to discover chunk boundaries and video frame sizes
3. Builds a new MP4: ftyp + moov(new sample tables) + mdat(RSV data)

Usage: python build_moov.py <donor.mp4> <input.rsv> [output.mp4]
"""

import struct
import sys
import os


# ---------------------------------------------------------------------------
# MP4 box parsing helpers
# ---------------------------------------------------------------------------

def read_box_header(f):
    """Read an MP4 box header. Returns (pos, total_size, type_str, header_len)."""
    pos = f.tell()
    hdr = f.read(8)
    if len(hdr) < 8:
        return None, None, None, None
    size, btype = struct.unpack('>I4s', hdr)
    btype = btype.decode('ascii', errors='replace')
    header_len = 8
    if size == 1:
        size = struct.unpack('>Q', f.read(8))[0]
        header_len = 16
    elif size == 0:
        old = f.tell()
        f.seek(0, 2)
        size = f.tell() - pos
        f.seek(old)
    return pos, size, btype, header_len


def find_box(f, start, end, target):
    """Find a box by type within [start, end). Returns (pos, size) or (None, None)."""
    f.seek(start)
    while f.tell() < end:
        pos, size, btype, _ = read_box_header(f)
        if btype is None:
            return None, None
        if btype == target:
            return pos, size
        f.seek(pos + size)
    return None, None


def read_box_data(f, pos, size):
    """Read the full box bytes."""
    f.seek(pos)
    return f.read(size)


# ---------------------------------------------------------------------------
# Donor MP4 parser
# ---------------------------------------------------------------------------

class DonorInfo:
    """Parsed info from a donor MP4 file."""
    def __init__(self, path):
        self.path = path
        self.ftyp = None
        self.moov_raw = None
        self.mdat_data_offset = 0
        self.tracks = {}  # keyed by handler type ('vide', 'soun', 'meta')
        self._parse()
    
    def _parse(self):
        with open(self.path, 'rb') as f:
            f.seek(0, 2)
            fsize = f.tell()
            f.seek(0)
            
            # ftyp
            ftyp_pos, ftyp_size = find_box(f, 0, fsize, 'ftyp')
            if ftyp_pos is not None:
                self.ftyp = read_box_data(f, ftyp_pos, ftyp_size)
            
            # mdat
            mdat_pos, mdat_size = find_box(f, 0, fsize, 'mdat')
            if mdat_pos is not None:
                f.seek(mdat_pos)
                raw_size = struct.unpack('>I', f.read(4))[0]
                hdr_len = 16 if raw_size == 1 else 8
                self.mdat_data_offset = mdat_pos + hdr_len
            
            # moov
            moov_pos, moov_size = find_box(f, 0, fsize, 'moov')
            if moov_pos is None:
                raise ValueError("No moov box found in donor")
            self.moov_raw = read_box_data(f, moov_pos, moov_size)
            
            # mvhd
            mvhd_pos, mvhd_size = find_box(f, moov_pos + 8, moov_pos + moov_size, 'mvhd')
            f.seek(mvhd_pos + 8)
            ver = struct.unpack('>B', f.read(1))[0]
            f.read(3)
            if ver == 0:
                f.read(8)
                self.mvhd_timescale = struct.unpack('>I', f.read(4))[0]
                self.mvhd_duration = struct.unpack('>I', f.read(4))[0]
            else:
                f.read(16)
                self.mvhd_timescale = struct.unpack('>I', f.read(4))[0]
                self.mvhd_duration = struct.unpack('>Q', f.read(8))[0]
            
            # Parse each trak
            trak_start = moov_pos + 8
            trak_end = moov_pos + moov_size
            track_id = 0
            
            while trak_start < trak_end:
                pos, size = find_box(f, trak_start, trak_end, 'trak')
                if pos is None:
                    break
                track_id += 1
                self._parse_track(f, pos, size, track_id)
                trak_start = pos + size
    
    def _parse_track(self, f, trak_pos, trak_size, track_id):
        trak_end = trak_pos + trak_size
        
        tkhd_pos, tkhd_size = find_box(f, trak_pos + 8, trak_end, 'tkhd')
        tkhd_data = read_box_data(f, tkhd_pos, tkhd_size)
        
        mdia_pos, mdia_size = find_box(f, trak_pos + 8, trak_end, 'mdia')
        if mdia_pos is None:
            return
        mdia_end = mdia_pos + mdia_size
        
        hdlr_pos, hdlr_size = find_box(f, mdia_pos + 8, mdia_end, 'hdlr')
        f.seek(hdlr_pos + 8 + 4 + 4)
        handler = f.read(4).decode('ascii', errors='replace')
        hdlr_data = read_box_data(f, hdlr_pos, hdlr_size)
        
        mdhd_pos, mdhd_size = find_box(f, mdia_pos + 8, mdia_end, 'mdhd')
        f.seek(mdhd_pos + 8)
        ver = struct.unpack('>B', f.read(1))[0]
        f.read(3)
        if ver == 0:
            f.read(8)
            timescale = struct.unpack('>I', f.read(4))[0]
            duration = struct.unpack('>I', f.read(4))[0]
        else:
            f.read(16)
            timescale = struct.unpack('>I', f.read(4))[0]
            duration = struct.unpack('>Q', f.read(8))[0]
        
        minf_pos, minf_size = find_box(f, mdia_pos + 8, mdia_end, 'minf')
        stbl_pos, stbl_size = find_box(f, minf_pos + 8, minf_pos + minf_size, 'stbl')
        stbl_end = stbl_pos + stbl_size
        
        stsd_pos, stsd_size = find_box(f, stbl_pos + 8, stbl_end, 'stsd')
        stsd_data = read_box_data(f, stsd_pos, stsd_size)
        
        stts_pos, stts_size = find_box(f, stbl_pos + 8, stbl_end, 'stts')
        f.seek(stts_pos + 12)
        stts_count = struct.unpack('>I', f.read(4))[0]
        stts_entries = []
        for _ in range(stts_count):
            sc, sd = struct.unpack('>II', f.read(8))
            stts_entries.append((sc, sd))
        sample_delta = stts_entries[0][1] if stts_entries else 1001
        
        ctts_data = None
        ctts_pos, ctts_size = find_box(f, stbl_pos + 8, stbl_end, 'ctts')
        if ctts_pos is not None:
            ctts_data = read_box_data(f, ctts_pos, ctts_size)
        
        stsc_pos, stsc_size = find_box(f, stbl_pos + 8, stbl_end, 'stsc')
        f.seek(stsc_pos + 12)
        stsc_count = struct.unpack('>I', f.read(4))[0]
        stsc_entries = []
        for _ in range(stsc_count):
            fc, spc, sdi = struct.unpack('>III', f.read(12))
            stsc_entries.append((fc, spc, sdi))
        
        stsz_pos, stsz_size = find_box(f, stbl_pos + 8, stbl_end, 'stsz')
        f.seek(stsz_pos + 12)
        stsz_uniform = struct.unpack('>I', f.read(4))[0]
        stsz_count = struct.unpack('>I', f.read(4))[0]
        
        stss_entries = None
        stss_pos, stss_size = find_box(f, stbl_pos + 8, stbl_end, 'stss')
        if stss_pos is not None:
            f.seek(stss_pos + 12)
            stss_count = struct.unpack('>I', f.read(4))[0]
            stss_entries = []
            for _ in range(stss_count):
                stss_entries.append(struct.unpack('>I', f.read(4))[0])
        
        extra_boxes = []
        # Note: 'edts' deliberately excluded — it contains segment durations
        # from the donor that would limit playback of the repaired file.
        for box_type in ['tref', 'uuid']:
            bpos, bsize = find_box(f, trak_pos + 8, trak_end, box_type)
            if bpos is not None:
                extra_boxes.append(read_box_data(f, bpos, bsize))
        
        mhd_data = None
        for mhd_type in ['vmhd', 'smhd', 'nmhd']:
            mhd_pos, mhd_size = find_box(f, minf_pos + 8, minf_pos + minf_size, mhd_type)
            if mhd_pos is not None:
                mhd_data = read_box_data(f, mhd_pos, mhd_size)
                break
        
        dinf_pos, dinf_size = find_box(f, minf_pos + 8, minf_pos + minf_size, 'dinf')
        dinf_data = read_box_data(f, dinf_pos, dinf_size)
        
        self.tracks[handler] = {
            'track_id': track_id,
            'handler': handler,
            'tkhd': tkhd_data,
            'hdlr': hdlr_data,
            'timescale': timescale,
            'duration': duration,
            'sample_delta': sample_delta,
            'stsd': stsd_data,
            'stsc_entries': stsc_entries,
            'stsz_uniform': stsz_uniform,
            'samples_per_chunk': stsc_entries[0][1] if stsc_entries else 15,
            'ctts': ctts_data,
            'stss_entries': stss_entries,
            'extra_boxes': extra_boxes,
            'mhd': mhd_data,
            'dinf': dinf_data,
        }


# ---------------------------------------------------------------------------
# RSV parser
# ---------------------------------------------------------------------------

META_SAMPLE_SIZE = 11264
META_SAMPLES_PER_CHUNK = 15
META_CHUNK_SIZE = META_SAMPLE_SIZE * META_SAMPLES_PER_CHUNK  # 168960
AUDIO_CHUNK_SIZE = 24024 * 4  # 96096
META_SIG = b'\x00\x1c\x01\x00'  # 4-byte RTMD signature (5th byte: 0x08=H.264, 0x09=HEVC)


def verify_meta_sig(f, offset, rsv_size):
    """Check if offset has the meta signature and the next sample also has it."""
    if offset + META_SAMPLE_SIZE + len(META_SIG) > rsv_size:
        return False
    f.seek(offset)
    sig = f.read(len(META_SIG))
    if sig != META_SIG:
        return False
    f.seek(offset + META_SAMPLE_SIZE)
    sig2 = f.read(len(META_SIG))
    return sig2 == META_SIG


def detect_samples_per_chunk(f, rsv_size):
    """Auto-detect how many metadata samples are in each chunk."""
    count = 0
    pos = 0
    while pos + len(META_SIG) < rsv_size:
        f.seek(pos)
        sig = f.read(len(META_SIG))
        if sig != META_SIG:
            break
        count += 1
        pos += META_SAMPLE_SIZE
    return count


def detect_audio_chunk_size(f, video_end, next_meta_start):
    """Calculate audio chunk size from the gap between video end and next meta."""
    return next_meta_start - video_end


def parse_video_chunk_avcc(f, video_start, max_end):
    """Parse AVCC NAL units to find frame boundaries within a video chunk.
    
    Handles both H.264 and HEVC NAL formats. Uses AUD NAL as frame delimiter.
    H.264 AUD = NAL type 9, HEVC AUD = NAL type 35.
    Returns list of frame sizes.
    """
    pos = video_start
    frame_start = pos
    frame_sizes = []
    
    # Detect codec from first NAL: read 6 bytes (4 len + 2 NAL header)
    f.seek(pos)
    first = f.read(6)
    if len(first) < 6:
        return frame_sizes
    first_nal_byte = first[4]
    # HEVC NAL header: forbidden(1) | type(6) | layer_id(6) | tid(3) = 16 bits
    # H.264 NAL header: forbidden(1) | ref_idc(2) | type(5) = 8 bits
    # HEVC AUD byte = (35 << 1) = 0x46, H.264 AUD byte = 0x09 (ref_idc=0, type=9)
    is_hevc = (first_nal_byte & 0x81) == 0 and ((first_nal_byte >> 1) & 0x3f) in (32, 33, 34, 35, 39, 19, 20, 0, 1)
    
    # For HEVC: also check if it looks like AUD (0x46 0x01)
    if not is_hevc and first_nal_byte == 0x46:
        is_hevc = True
    
    aud_type = 35 if is_hevc else 9
    
    while pos < max_end:
        f.seek(pos)
        lb = f.read(6)
        if len(lb) < 5:
            break
        nl = struct.unpack('>I', lb[:4])[0]
        
        if is_hevc:
            nt = (lb[4] >> 1) & 0x3f
        else:
            nt = lb[4] & 0x1f
        
        # Stop on zero length or unreasonable length
        if nl == 0 or nl > 10_000_000:
            break
        
        # Stop if this NAL would push us past the boundary
        if pos + 4 + nl > max_end:
            break
        
        # Validate NAL type range
        if is_hevc:
            if nt > 48:
                break
        else:
            if nt > 12 and nt not in (24, 25, 26, 27, 28):
                break
        
        # AUD marks frame boundary
        if nt == aud_type and pos > frame_start:
            frame_sizes.append(pos - frame_start)
            frame_start = pos
        
        pos += 4 + nl
    
    # Last frame
    if pos > frame_start:
        frame_sizes.append(pos - frame_start)
    
    return frame_sizes


def parse_rsv(rsv_path, log_fn=None):
    """Parse an RSV file to extract chunk layout and video frame sizes.
    
    Auto-detects samples_per_chunk and audio_chunk_size from the file.
    Uses a walk-forward approach for speed.
    
    log_fn: optional callback(msg_str) for progress output. Defaults to print.
    """
    import time
    if log_fn is None:
        log_fn = print
    t0 = time.time()
    
    with open(rsv_path, 'rb') as f:
        f.seek(0, 2)
        rsv_size = f.tell()
        
        log_fn(f"RSV size: {rsv_size:,} bytes ({rsv_size / (1024**3):.2f} GB)")
        
        # Auto-detect samples per chunk
        samples_per_chunk = detect_samples_per_chunk(f, rsv_size)
        if samples_per_chunk < 1:
            raise ValueError("Could not detect meta chunk structure")
        
        meta_chunk_size = samples_per_chunk * META_SAMPLE_SIZE
        log_fn(f"Detected {samples_per_chunk} meta samples per chunk "
              f"(meta_chunk_size = {meta_chunk_size:,})")
        
        # Parse first video chunk to find its end, then detect audio size
        video_start_0 = meta_chunk_size
        frame_sizes_0 = parse_video_chunk_avcc(f, video_start_0, video_start_0 + 12_000_000)
        video_end_0 = video_start_0 + sum(frame_sizes_0)
        
        # Find second meta chunk to determine audio size
        # Search from video_end_0 onwards
        audio_chunk_size = None
        for search_pos in range(video_end_0, min(video_end_0 + 500_000, rsv_size), 1):
            f.seek(search_pos)
            sig = f.read(len(META_SIG))
            if sig == META_SIG:
                f.seek(search_pos + META_SAMPLE_SIZE)
                sig2 = f.read(len(META_SIG))
                if sig2 == META_SIG:
                    audio_chunk_size = search_pos - video_end_0
                    log_fn(f"Detected audio_chunk_size = {audio_chunk_size:,} bytes "
                          f"({audio_chunk_size // 4:,} samples)")
                    break
        
        if audio_chunk_size is None:
            raise ValueError("Could not detect audio chunk size")
        
        log_fn(f"Parsing chunks (walk-forward)...")
        
        chunks = []
        all_video_sizes = []
        offset = 0
        chunk_idx = 0
        
        while offset + meta_chunk_size < rsv_size:
            meta_start = offset
            
            # Verify meta signature
            if not verify_meta_sig(f, meta_start, rsv_size):
                f.seek(meta_start)
                test = f.read(64)
                if all(b == 0 for b in test):
                    log_fn(f"  End of data (zeros) at chunk {chunk_idx}, offset {meta_start:,}")
                    break
                # Try to find next meta chunk nearby
                found = False
                for search_off in range(meta_start, min(meta_start + 20_000_000, rsv_size), META_SAMPLE_SIZE):
                    if verify_meta_sig(f, search_off, rsv_size):
                        log_fn(f"  Chunk {chunk_idx}: skipped gap, found meta at {search_off:,}")
                        meta_start = search_off
                        found = True
                        break
                if not found:
                    log_fn(f"  No more meta chunks found after offset {meta_start:,}")
                    break
            
            video_start = meta_start + meta_chunk_size
            
            # Find the NEXT meta chunk first so we know exact boundaries
            next_meta = None
            # Estimate: video is ~8-10MB, then audio
            est_cycle = meta_chunk_size + 9_000_000 + audio_chunk_size
            search_start = meta_start + meta_chunk_size + 7_000_000  # min video
            search_end = min(meta_start + meta_chunk_size + 12_000_000 + audio_chunk_size, rsv_size)
            
            # Read the search region and scan for META_SIG
            f.seek(search_start)
            search_buf = f.read(search_end - search_start)
            for i in range(len(search_buf) - len(META_SIG)):
                if search_buf[i:i+len(META_SIG)] == META_SIG:
                    candidate = search_start + i
                    if verify_meta_sig(f, candidate, rsv_size):
                        next_meta = candidate
                        break
            
            if next_meta is not None:
                # Video ends at next_meta - audio_chunk_size
                max_video_end = next_meta - audio_chunk_size
            else:
                # Last chunk — use generous bound
                max_video_end = min(video_start + 12_000_000, rsv_size)
            
            frame_sizes = parse_video_chunk_avcc(f, video_start, max_video_end)
            
            if not frame_sizes:
                log_fn(f"  Chunk {chunk_idx}: no valid frames at {video_start:,}, stopping")
                break
            
            video_end = video_start + sum(frame_sizes)
            audio_start = video_end
            
            if next_meta is not None:
                actual_audio_size = next_meta - audio_start
                next_offset = next_meta
            else:
                actual_audio_size = min(audio_chunk_size, max(0, rsv_size - audio_start))
                next_offset = rsv_size
            
            chunks.append({
                'meta_offset': meta_start,
                'video_offset': video_start,
                'audio_offset': audio_start,
                'video_frame_sizes': frame_sizes,
                'audio_size': actual_audio_size,
                'audio_samples': actual_audio_size // 4,
            })
            all_video_sizes.extend(frame_sizes)
            
            n_frames = len(frame_sizes)
            if chunk_idx % 500 == 0 or chunk_idx < 5:
                elapsed = time.time() - t0
                pct = 100 * meta_start / rsv_size if rsv_size > 0 else 0
                log_fn(f"  Chunk {chunk_idx:4d}: {n_frames:2d} frames, "
                      f"video={sum(frame_sizes):>10,}b @ {pct:.1f}% ({elapsed:.0f}s)")
            
            offset = next_offset
            chunk_idx += 1
        
        elapsed = time.time() - t0
        total_frames = len(all_video_sizes)
        log_fn(f"\nTotal: {len(chunks)} chunks, {total_frames} video frames, "
              f"parsed in {elapsed:.1f}s")
        
        return {
            'num_chunks': len(chunks),
            'chunks': chunks,
            'rsv_size': rsv_size,
            'samples_per_chunk': samples_per_chunk,
            'audio_chunk_size': audio_chunk_size,
        }


# ---------------------------------------------------------------------------
# MP4 box building helpers
# ---------------------------------------------------------------------------

def make_box(box_type, data):
    """Build a box: size(4) + type(4) + data."""
    if isinstance(box_type, str):
        box_type = box_type.encode('ascii')
    size = 8 + len(data)
    return struct.pack('>I', size) + box_type + data


def make_full_box(box_type, version, flags, data):
    """Build a full box: size(4) + type(4) + version(1) + flags(3) + data."""
    if isinstance(box_type, str):
        box_type = box_type.encode('ascii')
    full_data = struct.pack('>I', (version << 24) | flags) + data
    return make_box(box_type, full_data)


def make_mdat_header(data_size):
    """Create mdat box header. Uses 64-bit extended size for data > ~4GB."""
    total = data_size + 8
    if total > 0xFFFFFFFF:
        total_ext = data_size + 16
        return struct.pack('>I', 1) + b'mdat' + struct.pack('>Q', total_ext)
    else:
        return struct.pack('>I', total) + b'mdat'


def build_stts(sample_count, sample_delta):
    data = struct.pack('>I', 1) + struct.pack('>II', sample_count, sample_delta)
    return make_full_box('stts', 0, 0, data)


def build_stsc_variable(entries):
    """entries = [(first_chunk_1indexed, samples_per_chunk, sample_desc_idx), ...]"""
    data = struct.pack('>I', len(entries))
    for fc, spc, sdi in entries:
        data += struct.pack('>III', fc, spc, sdi)
    return make_full_box('stsc', 0, 0, data)


def build_stsz_uniform(sample_size, sample_count):
    data = struct.pack('>II', sample_size, sample_count)
    return make_full_box('stsz', 0, 0, data)


def build_stsz_variable(sizes):
    data = struct.pack('>II', 0, len(sizes))
    for s in sizes:
        data += struct.pack('>I', s)
    return make_full_box('stsz', 0, 0, data)


def build_co64(offsets):
    data = struct.pack('>I', len(offsets))
    for o in offsets:
        data += struct.pack('>Q', o)
    return make_full_box('co64', 0, 0, data)


def build_stss(sync_samples):
    """sync_samples: list of 1-indexed sample numbers."""
    data = struct.pack('>I', len(sync_samples))
    for s in sync_samples:
        data += struct.pack('>I', s)
    return make_full_box('stss', 0, 0, data)


def build_ctts_from_donor(donor_ctts_data, needed_samples):
    """Rebuild ctts box, tiling the donor pattern to match needed_samples."""
    if donor_ctts_data is None:
        return None
    
    version = donor_ctts_data[8]
    entry_count = struct.unpack('>I', donor_ctts_data[12:16])[0]
    
    # Expand to per-sample offsets
    per_sample = []
    for i in range(entry_count):
        offset = 16 + i * 8
        if version == 1:
            sc, co = struct.unpack('>Ii', donor_ctts_data[offset:offset+8])
        else:
            sc, co = struct.unpack('>II', donor_ctts_data[offset:offset+8])
        per_sample.extend([co] * sc)
    
    if not per_sample:
        return None
    
    # Tile to fill needed_samples
    pattern = list(per_sample)
    result = []
    while len(result) < needed_samples:
        result.extend(pattern[:needed_samples - len(result)])
    
    # Re-compress into run-length
    new_entries = []
    current_co = result[0]
    current_count = 1
    for co in result[1:]:
        if co == current_co:
            current_count += 1
        else:
            new_entries.append((current_count, current_co))
            current_co = co
            current_count = 1
    new_entries.append((current_count, current_co))
    
    data = struct.pack('>I', len(new_entries))
    for sc, co in new_entries:
        if version == 1:
            data += struct.pack('>Ii', sc, co)
        else:
            data += struct.pack('>II', sc, co)
    
    return make_full_box('ctts', version, 0, data)


def build_mdhd(timescale, duration, version=0):
    if version == 0:
        data = struct.pack('>II', 0, 0)
        data += struct.pack('>I', timescale)
        data += struct.pack('>I', duration)
        data += struct.pack('>HH', 0x55C4, 0)
    else:
        data = struct.pack('>QQ', 0, 0)
        data += struct.pack('>I', timescale)
        data += struct.pack('>Q', duration)
        data += struct.pack('>HH', 0x55C4, 0)
    return make_full_box('mdhd', version, 0, data)


def build_mvhd(timescale, duration, next_track_id, version=0):
    if version == 0:
        data = struct.pack('>II', 0, 0)
        data += struct.pack('>I', timescale)
        data += struct.pack('>I', duration)
    else:
        data = struct.pack('>QQ', 0, 0)
        data += struct.pack('>I', timescale)
        data += struct.pack('>Q', duration)
    data += struct.pack('>I', 0x00010000)   # rate = 1.0
    data += struct.pack('>H', 0x0100)       # volume = 1.0
    data += b'\x00' * 10
    data += struct.pack('>9I',
        0x00010000, 0, 0,
        0, 0x00010000, 0,
        0, 0, 0x40000000)
    data += b'\x00' * 24
    data += struct.pack('>I', next_track_id)
    return make_full_box('mvhd', version, 0, data)


def update_tkhd_duration(tkhd_data, new_duration):
    """Update duration in tkhd, preserving everything else."""
    result = bytearray(tkhd_data)
    version = result[8]
    if version == 0:
        struct.pack_into('>I', result, 28, new_duration)
    else:
        struct.pack_into('>Q', result, 40, new_duration)
    return bytes(result)


# ---------------------------------------------------------------------------
# Build moov
# ---------------------------------------------------------------------------

def build_moov(donor, rsv_info, mdat_offset):
    """Build the complete moov box for the new MP4."""
    chunks = rsv_info['chunks']
    num_chunks = rsv_info['num_chunks']
    
    # Flatten video frame sizes
    video_frame_sizes = []
    for c in chunks:
        video_frame_sizes.extend(c['video_frame_sizes'])
    total_video_samples = len(video_frame_sizes)
    total_meta_samples = num_chunks * 15
    total_audio_samples = sum(c['audio_samples'] for c in chunks)
    
    # Timing from donor
    vide_info = donor.tracks['vide']
    soun_info = donor.tracks['soun']
    meta_info = donor.tracks['meta']
    
    video_delta = vide_info['sample_delta']
    video_timescale = vide_info['timescale']
    audio_timescale = soun_info['timescale']
    meta_timescale = meta_info['timescale']
    
    video_duration = total_video_samples * video_delta
    meta_duration = total_meta_samples * meta_info['sample_delta']
    audio_duration = total_audio_samples
    
    movie_duration = int(video_duration * donor.mvhd_timescale / video_timescale)
    
    # Chunk offsets (file-absolute)
    video_offsets = [mdat_offset + c['video_offset'] for c in chunks]
    audio_offsets = [mdat_offset + c['audio_offset'] for c in chunks]
    meta_offsets = [mdat_offset + c['meta_offset'] for c in chunks]
    
    # Sync samples: first frame of every chunk (all are IDR in Sony RSV)
    sync_samples = []
    sample_num = 0
    for c in chunks:
        sample_num += 1
        sync_samples.append(sample_num)
        sample_num += len(c['video_frame_sizes']) - 1
    
    # Video stsc
    video_stsc_entries = []
    prev_spc = None
    for i, c in enumerate(chunks):
        spc = len(c['video_frame_sizes'])
        if spc != prev_spc:
            video_stsc_entries.append((i + 1, spc, 1))
            prev_spc = spc
    
    # Audio stsc
    audio_stsc_entries = []
    prev_spc = None
    for i, c in enumerate(chunks):
        spc = c['audio_samples']
        if spc != prev_spc:
            audio_stsc_entries.append((i + 1, spc, 1))
            prev_spc = spc
    
    # ---- Video track ----
    video_stbl = b''
    video_stbl += vide_info['stsd']
    video_stbl += build_stts(total_video_samples, video_delta)
    ctts_box = build_ctts_from_donor(vide_info['ctts'], total_video_samples)
    if ctts_box:
        video_stbl += ctts_box
    video_stbl += build_stsc_variable(video_stsc_entries)
    video_stbl += build_stsz_variable(video_frame_sizes)
    video_stbl += build_co64(video_offsets)
    video_stbl += build_stss(sync_samples)
    
    video_minf = vide_info['mhd'] + vide_info['dinf'] + make_box('stbl', video_stbl)
    video_mdia = (build_mdhd(video_timescale, video_duration) +
                  vide_info['hdlr'] +
                  make_box('minf', video_minf))
    video_tkhd = update_tkhd_duration(vide_info['tkhd'], movie_duration)
    video_trak = video_tkhd + b''.join(vide_info['extra_boxes']) + make_box('mdia', video_mdia)
    
    # ---- Audio track ----
    audio_stbl = b''
    audio_stbl += soun_info['stsd']
    audio_stbl += build_stts(total_audio_samples, 1)
    audio_stbl += build_stsc_variable(audio_stsc_entries)
    audio_stbl += build_stsz_uniform(4, total_audio_samples)
    audio_stbl += build_co64(audio_offsets)
    
    audio_minf = soun_info['mhd'] + soun_info['dinf'] + make_box('stbl', audio_stbl)
    audio_mdia = (build_mdhd(audio_timescale, audio_duration) +
                  soun_info['hdlr'] +
                  make_box('minf', audio_minf))
    audio_tkhd = update_tkhd_duration(soun_info['tkhd'], movie_duration)
    audio_trak = audio_tkhd + b''.join(soun_info['extra_boxes']) + make_box('mdia', audio_mdia)
    
    # ---- Meta track ----
    meta_stbl = b''
    meta_stbl += meta_info['stsd']
    meta_stbl += build_stts(total_meta_samples, meta_info['sample_delta'])
    meta_stbl += build_stsc_variable([(1, 15, 1)])
    meta_stbl += build_stsz_uniform(META_SAMPLE_SIZE, total_meta_samples)
    meta_stbl += build_co64(meta_offsets)
    
    meta_minf = meta_info['mhd'] + meta_info['dinf'] + make_box('stbl', meta_stbl)
    meta_mdia = (build_mdhd(meta_timescale, meta_duration) +
                 meta_info['hdlr'] +
                 make_box('minf', meta_minf))
    meta_tkhd = update_tkhd_duration(meta_info['tkhd'], movie_duration)
    meta_trak = meta_tkhd + b''.join(meta_info['extra_boxes']) + make_box('mdia', meta_mdia)
    
    # ---- Assemble moov ----
    mvhd = build_mvhd(donor.mvhd_timescale, movie_duration, next_track_id=4)
    moov_content = mvhd
    moov_content += make_box('trak', video_trak)
    moov_content += make_box('trak', audio_trak)
    moov_content += make_box('trak', meta_trak)
    
    return make_box('moov', moov_content)


# ---------------------------------------------------------------------------
# Main: assemble the output MP4
# ---------------------------------------------------------------------------

def build_mp4(donor_path, rsv_path, output_path, log_fn=None):
    if log_fn is None:
        log_fn = print
    log_fn(f"Donor:  {donor_path}")
    log_fn(f"RSV:    {rsv_path}")
    log_fn(f"Output: {output_path}")
    log_fn("")
    
    log_fn("=== Parsing donor MP4 ===")
    donor = DonorInfo(donor_path)
    log_fn(f"  Movie timescale: {donor.mvhd_timescale}")
    for handler, info in donor.tracks.items():
        log_fn(f"  {handler}: timescale={info['timescale']}, delta={info['sample_delta']}, "
              f"spc={info['samples_per_chunk']}")
    log_fn("")
    
    log_fn("=== Parsing RSV file ===")
    rsv_info = parse_rsv(rsv_path, log_fn=log_fn)
    log_fn("")
    
    # Layout: ftyp + moov + mdat
    ftyp = donor.ftyp
    rsv_size = rsv_info['rsv_size']
    ftyp_size = len(ftyp)
    
    # Two-pass moov build to resolve offset chicken-and-egg
    dummy_mdat_header = make_mdat_header(rsv_size)
    dummy_moov = build_moov(donor, rsv_info, ftyp_size + 1_000_000 + len(dummy_mdat_header))
    moov_size = len(dummy_moov)
    
    mdat_header = make_mdat_header(rsv_size)
    mdat_data_offset = ftyp_size + moov_size + len(mdat_header)
    moov = build_moov(donor, rsv_info, mdat_data_offset)
    assert len(moov) == moov_size, f"Moov size changed: {moov_size} -> {len(moov)}"
    
    log_fn("=== Writing output MP4 ===")
    log_fn(f"  ftyp: {ftyp_size} bytes")
    log_fn(f"  moov: {moov_size:,} bytes")
    log_fn(f"  mdat: {rsv_size:,} bytes (data starts at offset {mdat_data_offset})")
    
    total_size = ftyp_size + moov_size + len(mdat_header) + rsv_size
    log_fn(f"  Total: {total_size:,} bytes ({total_size / (1024**3):.2f} GB)")
    
    BUF = 4 * 1024 * 1024
    
    with open(output_path, 'wb') as out:
        out.write(ftyp)
        out.write(moov)
        out.write(mdat_header)
        
        with open(rsv_path, 'rb') as rsv:
            remaining = rsv_size
            written = 0
            while remaining > 0:
                chunk = rsv.read(min(BUF, remaining))
                if not chunk:
                    break
                out.write(chunk)
                remaining -= len(chunk)
                written += len(chunk)
                if written % (100 * 1024 * 1024) == 0:
                    pct = written / rsv_size * 100
                    log_fn(f"  Writing mdat: {written:,} / {rsv_size:,} ({pct:.0f}%)")
    
    log_fn(f"\nDone! Output: {output_path}")
    log_fn(f"Output size: {os.path.getsize(output_path):,} bytes")


# ---------------------------------------------------------------------------
# Standalone mode helpers
# ---------------------------------------------------------------------------

class BitReader:
    def __init__(self, data):
        # Remove emulation prevention bytes
        clean_data = bytearray()
        i = 0
        while i < len(data):
            if i + 2 < len(data) and data[i] == 0 and data[i+1] == 0 and data[i+2] == 3:
                clean_data.extend(data[i:i+2])
                i += 3
            else:
                clean_data.append(data[i])
                i += 1
        self.data = clean_data
        self.byte_idx = 0
        self.bit_idx = 0
        
    def read_bit(self):
        if self.byte_idx >= len(self.data): return 0
        val = (self.data[self.byte_idx] >> (7 - self.bit_idx)) & 1
        self.bit_idx += 1
        if self.bit_idx == 8:
            self.byte_idx += 1
            self.bit_idx = 0
        return val

    def read_bits(self, n):
        val = 0
        for _ in range(n):
            val = (val << 1) | self.read_bit()
        return val

    def read_ue(self):
        zeros = 0
        while self.read_bit() == 0 and self.byte_idx < len(self.data):
            zeros += 1
        if self.byte_idx >= len(self.data): return 0
        return (1 << zeros) - 1 + self.read_bits(zeros)
        
    def read_se(self):
        v = self.read_ue()
        if v % 2 == 0:
            return -(v // 2)
        else:
            return (v + 1) // 2


def construct_sps_pps(f, video_start, meta_start):
    """Extract real H.264 SPS and PPS from the Sony kkad box in RTMD metadata.
    
    Sony embeds the actual SPS/PPS in a 'kkad' box within each RTMD metadata
    sample. The kkad box uses a TLV format:
      - Tag 02 04: SPS NAL unit
      - Tag 03 04: PPS NAL unit (one per PPS ID)
    
    Returns (sps_bytes, pps_list, codec_info_dict).
    """
    # Read first metadata sample (11264 bytes) to find kkad box
    f.seek(meta_start)
    meta_sample = f.read(META_SAMPLE_SIZE)
    
    # Find kkad box by searching for 'kkad' tag
    kkad_data = None
    idx = meta_sample.find(b'kkad')
    if idx >= 4:
        box_size = struct.unpack('>I', meta_sample[idx-4:idx])[0]
        if 8 < box_size < 2000 and idx - 4 + box_size <= len(meta_sample):
            kkad_data = meta_sample[idx+4:idx-4+box_size]
    
    if kkad_data is None:
        raise ValueError("Could not find kkad box in RTMD metadata")
    
    # Extract SPS/PPS by scanning for NAL headers within kkad data.
    # Sony kkad uses TLV entries: [len_hi len_lo tag1 tag2 NAL_data...]
    # SPS: tag 02 04, NAL byte 0x27 (ref_idc=1, type=7)
    # PPS: tag 03 04, NAL byte 0x28 (ref_idc=1, type=8)
    # We scan for the 4-byte pattern [tag1 tag2 0x27/0x28 profile/pps_byte]
    # and use the 2-byte length prefix before the tag to determine NAL size.
    sps = None
    pps_list = []
    
    for i in range(2, len(kkad_data) - 8):
        tag1, tag2 = kkad_data[i], kkad_data[i+1]
        nal_byte = kkad_data[i+2]
        nal_type = nal_byte & 0x1f
        nal_ref = (nal_byte >> 5) & 3
        
        if nal_ref == 0:
            continue
        
        # Read length from 2 bytes before the tag
        entry_len = struct.unpack('>H', kkad_data[i-2:i])[0]
        if entry_len < 6 or entry_len > 500:
            continue
        
        data_len = entry_len - 4  # subtract 2-byte len field + 2-byte tag
        nal_start = i + 2
        nal_end = nal_start + data_len
        
        if nal_end > len(kkad_data):
            continue
        
        if tag1 == 0x02 and tag2 == 0x04 and nal_type == 7:
            candidate = bytes(kkad_data[nal_start:nal_end])
            # Verify: profile_idc should be a valid H.264 profile
            if len(candidate) > 3 and candidate[1] in (0x42, 0x4D, 0x58, 0x64, 0x6E, 0x7A, 0xF4):
                sps = candidate
        
        elif tag1 == 0x03 and tag2 == 0x04 and nal_type == 8:
            candidate = bytes(kkad_data[nal_start:nal_end])
            if len(candidate) > 4:
                pps_list.append(candidate)
    
    if sps is None:
        raise ValueError("Could not find SPS in kkad box")
    if not pps_list:
        raise ValueError("Could not find PPS in kkad box")
    
    # Sort PPS by pps_id (parse first ue(v) from each PPS NAL)
    def get_pps_id(pps_nal):
        bits = []
        for b in pps_nal[1:4]:
            for j in range(7, -1, -1):
                bits.append((b >> j) & 1)
        zeros = 0
        for bit in bits:
            if bit == 0:
                zeros += 1
            else:
                break
        val = 0
        for k in range(zeros + 1 + zeros):
            if k < len(bits):
                val = (val << 1) | bits[k]
        return val - 1 if val > 0 else 0
    
    pps_list.sort(key=get_pps_id)
    
    # Parse SPS for codec info using the existing BitReader/parse_sps
    codec_info = parse_sps(sps)
    codec_info['sps_bytes'] = sps
    codec_info['pps_list'] = pps_list
    
    return sps, pps_list, codec_info


def detect_codec_type(rsv_path):
    """Detect codec from RSV file. Returns 'h264' or 'hevc'."""
    with open(rsv_path, 'rb') as f:
        header = f.read(5)
    if len(header) >= 5 and header[4] == 0x09:
        return 'hevc'
    return 'h264'


def construct_hevc_params(f, meta_start, samples_to_scan=8):
    """Extract HEVC VPS/SPS/PPS from Sony kkad box across RTMD metadata samples.
    
    Sony embeds VPS, SPS, and multiple PPS across metadata samples in the chunk.
    kkad TLV tags for HEVC:
      - Tag 12 04: VPS NAL unit (nal_type=32)
      - Tag 02 04: SPS NAL unit (nal_type=33)
      - Tag 03 04: PPS NAL unit (nal_type=34)
    
    Returns codec_info dict with vps, sps, pps_list, and parsed parameters.
    """
    vps = None
    sps = None
    pps_map = {}
    
    for s in range(samples_to_scan):
        f.seek(meta_start + s * META_SAMPLE_SIZE)
        meta_sample = f.read(META_SAMPLE_SIZE)
        if len(meta_sample) < META_SAMPLE_SIZE:
            break
        
        idx = meta_sample.find(b'kkad')
        if idx < 4:
            continue
        box_size = struct.unpack('>I', meta_sample[idx-4:idx])[0]
        if not (8 < box_size < 2000 and idx - 4 + box_size <= len(meta_sample)):
            continue
        kkad_data = meta_sample[idx+4:idx-4+box_size]
        
        for i in range(2, len(kkad_data) - 8):
            tag1, tag2 = kkad_data[i], kkad_data[i+1]
            nal_byte = kkad_data[i+2]
            hevc_nal_type = (nal_byte >> 1) & 0x3f
            
            entry_len = struct.unpack('>H', kkad_data[i-2:i])[0]
            if entry_len < 6 or entry_len > 500:
                continue
            
            data_len = entry_len - 4  # entry_len includes 2-byte len + 2-byte tag
            nal_start = i + 2
            nal_end = nal_start + data_len
            
            if nal_end > len(kkad_data):
                continue
            
            candidate = bytes(kkad_data[nal_start:nal_end])
            
            if tag1 == 0x12 and tag2 == 0x04 and hevc_nal_type == 32 and len(candidate) > 4:
                vps = candidate
            elif tag1 == 0x02 and tag2 == 0x04 and hevc_nal_type == 33 and len(candidate) > 4:
                sps = candidate
            elif tag1 == 0x03 and tag2 == 0x04 and hevc_nal_type == 34 and len(candidate) > 4:
                r = BitReader(candidate[2:])
                pid = r.read_ue()
                pps_map[pid] = candidate
    
    if vps is None:
        raise ValueError("Could not find VPS in kkad box")
    if sps is None:
        raise ValueError("Could not find SPS in kkad box")
    if not pps_map:
        raise ValueError("Could not find PPS in kkad box")
    
    pps_list = [pps_map[k] for k in sorted(pps_map.keys())]
    
    # Parse VPS using BitReader (handles emulation prevention automatically)
    vps_reader = BitReader(vps[2:])
    vps_reader.read_bits(4 + 1 + 1 + 6 + 3 + 1 + 16)  # vps header fields
    general_profile_space = vps_reader.read_bits(2)
    general_tier_flag = vps_reader.read_bits(1)
    general_profile_idc = vps_reader.read_bits(5)
    general_profile_compat = vps_reader.read_bits(32)
    general_constraint = bytes([vps_reader.read_bits(8) for _ in range(6)])
    general_level_idc = vps_reader.read_bits(8)
    
    # Parse SPS using BitReader
    sps_reader = BitReader(sps[2:])
    sps_reader.read_bits(4)  # sps_video_parameter_set_id
    sps_max_sub_layers = sps_reader.read_bits(3)
    sps_reader.read_bits(1)  # sps_temporal_id_nesting_flag
    
    # profile_tier_level(1, sps_max_sub_layers)
    sps_reader.read_bits(96)
    sub_layer_profile_present = []
    sub_layer_level_present = []
    for _ in range(sps_max_sub_layers):
        sub_layer_profile_present.append(sps_reader.read_bit())
        sub_layer_level_present.append(sps_reader.read_bit())
    if sps_max_sub_layers > 0:
        for _ in range(2 * (8 - sps_max_sub_layers)):
            sps_reader.read_bit()
    for i in range(sps_max_sub_layers):
        if sub_layer_profile_present[i]:
            sps_reader.read_bits(96)
        if sub_layer_level_present[i]:
            sps_reader.read_bits(8)
            
    sps_seq_parameter_set_id = sps_reader.read_ue()
    chroma_format_idc = sps_reader.read_ue()
    if chroma_format_idc == 3:
        sps_reader.read_bit()
    pic_width = sps_reader.read_ue()
    pic_height = sps_reader.read_ue()
    
    conformance = sps_reader.read_bit()
    if conformance:
        sps_reader.read_ue()
        sps_reader.read_ue()
        sps_reader.read_ue()
        sps_reader.read_ue()
        
    bit_depth_luma_minus8 = sps_reader.read_ue()
    bit_depth_chroma_minus8 = sps_reader.read_ue()
    
    codec_info = {
        'codec': 'hevc',
        'vps_bytes': vps,
        'sps_bytes': sps,
        'pps_list': pps_list,
        'width': pic_width,
        'height': pic_height,
        'profile_idc': general_profile_idc,
        'level_idc': general_level_idc,
        'general_tier_flag': general_tier_flag,
        'general_profile_space': general_profile_space,
        'general_profile_compat': general_profile_compat,
        'general_constraint': general_constraint,
        'chroma_format_idc': chroma_format_idc,
        'bit_depth_luma': bit_depth_luma_minus8,
        'bit_depth_chroma': bit_depth_chroma_minus8,
        'time_scale': 48000,
        'num_units_in_tick': 1001,
    }
    
    return codec_info


def build_hvcc(codec_info):
    """Build hvcC box content from HEVC codec parameters."""
    vps = codec_info['vps_bytes']
    sps = codec_info['sps_bytes']
    pps_list = codec_info['pps_list']
    
    hvcc = bytearray()
    hvcc.append(1)  # configurationVersion
    
    byte1 = ((codec_info['general_profile_space'] & 3) << 6) | \
             ((codec_info['general_tier_flag'] & 1) << 5) | \
             (codec_info['profile_idc'] & 0x1f)
    hvcc.append(byte1)
    
    hvcc.extend(struct.pack('>I', codec_info['general_profile_compat']))
    hvcc.extend(codec_info['general_constraint'])  # 6 bytes
    hvcc.append(codec_info['level_idc'])
    
    hvcc.extend(struct.pack('>H', 0xf000))  # min_spatial_segmentation_idc = 0, reserved
    hvcc.append(0xfd)  # parallelismType = 1, reserved
    hvcc.append(0xfc | (codec_info['chroma_format_idc'] & 3))
    hvcc.append(0xf8 | (codec_info['bit_depth_luma'] & 7))
    hvcc.append(0xf8 | (codec_info['bit_depth_chroma'] & 7))
    hvcc.extend(struct.pack('>H', 0))  # avgFrameRate = 0
    
    # constantFrameRate(1) | numTemporalLayers(1) | temporalIdNested(0) | lengthSizeMinusOne(3)
    hvcc.append(0x4b)
    
    # numOfArrays = 3 (VPS, SPS, PPS)
    hvcc.append(3)
    
    # VPS array
    hvcc.append(0xa0 | 32)
    hvcc.extend(struct.pack('>H', 1))
    hvcc.extend(struct.pack('>H', len(vps)))
    hvcc.extend(vps)
    
    # SPS array
    hvcc.append(0xa0 | 33)
    hvcc.extend(struct.pack('>H', 1))
    hvcc.extend(struct.pack('>H', len(sps)))
    hvcc.extend(sps)
    
    # PPS array
    hvcc.append(0xa0 | 34)
    hvcc.extend(struct.pack('>H', len(pps_list)))
    for pps in pps_list:
        hvcc.extend(struct.pack('>H', len(pps)))
        hvcc.extend(pps)
    
    return bytes(hvcc)


def build_video_stsd_hevc(hvcc_data, width, height):
    """Build video stsd box with hvc1 sample entry."""
    hvc1 = b'\x00' * 6
    hvc1 += struct.pack('>H', 1)  # dref_idx
    hvc1 += b'\x00' * 16  # pre_defined, reserved, pre_defined
    hvc1 += struct.pack('>HH', width, height)
    hvc1 += struct.pack('>II', 0x00480000, 0x00480000)  # 72 dpi
    hvc1 += b'\x00' * 4
    hvc1 += struct.pack('>H', 1)  # frame_count
    compressorname = b'\x0BHEVC Coding' + b'\x00' * 20
    hvc1 += compressorname
    hvc1 += struct.pack('>H', 0x0018)  # depth
    hvc1 += struct.pack('>h', -1)  # pre_defined
    hvc1 += make_box('hvcC', hvcc_data)
    hvc1 += make_box('pasp', struct.pack('>II', 1, 1))
    
    stsd_data = struct.pack('>I', 1) + make_box('hvc1', hvc1)
    return make_full_box('stsd', 0, 0, stsd_data)


def build_audio_stsd_twos():
    """Build audio stsd for PCM s16be using 'twos' codec tag (HEVC recordings)."""
    twos = b'\x00' * 6
    twos += struct.pack('>H', 1)  # dref_idx
    twos += b'\x00' * 8  # reserved
    twos += struct.pack('>HH', 2, 16)  # channel_count, sample_size
    twos += struct.pack('>HH', 0, 0)  # compression_id, packet_size
    twos += struct.pack('>I', 48000 << 16)  # sample_rate
    
    stsd_data = struct.pack('>I', 1) + make_box('twos', twos)
    return make_full_box('stsd', 0, 0, stsd_data)


class ExpGolombWriter:
    """Bitstream writer with Exp-Golomb encoding."""
    def __init__(self):
        self.bits = []
    
    def write_bits(self, value, n):
        for i in range(n - 1, -1, -1):
            self.bits.append((value >> i) & 1)
    
    def write_ue(self, value):
        """Unsigned Exp-Golomb."""
        value += 1
        n = value.bit_length()
        self.write_bits(0, n - 1)  # leading zeros
        self.write_bits(value, n)   # 1 + suffix
    
    def write_se(self, value):
        """Signed Exp-Golomb."""
        if value > 0:
            self.write_ue(2 * value - 1)
        elif value < 0:
            self.write_ue(-2 * value)
        else:
            self.write_ue(0)
    
    def write_bool(self, value):
        self.bits.append(1 if value else 0)
    
    def to_bytes(self):
        """Convert to bytes with emulation prevention."""
        # Pad to byte boundary
        while len(self.bits) % 8 != 0:
            self.bits.append(0)
        
        raw = bytearray()
        for i in range(0, len(self.bits), 8):
            byte = 0
            for j in range(8):
                if i + j < len(self.bits):
                    byte = (byte << 1) | self.bits[i + j]
                else:
                    byte <<= 1
            raw.append(byte)
        
        # Add emulation prevention bytes (0x00 0x00 -> 0x00 0x00 0x03)
        result = bytearray()
        zero_count = 0
        for b in raw:
            if zero_count >= 2 and b <= 3:
                result.append(0x03)
                zero_count = 0
            result.append(b)
            if b == 0:
                zero_count += 1
            else:
                zero_count = 0
        
        return bytes(result)


def _build_sps_nal(profile_idc, level_idc, chroma_format_idc,
                   bit_depth_luma, bit_depth_chroma,
                   pic_width_in_mbs, pic_height_in_mbs, num_slices):
    """Build a valid H.264 SPS NAL unit."""
    w = ExpGolombWriter()
    
    # NAL header: forbidden_zero(1) + nal_ref_idc(2) + nal_unit_type(5)
    # 0x67 = 0b01100111 = forbidden=0, ref_idc=3, type=7(SPS)
    nal_header = bytes([0x67])
    
    # SPS RBSP
    # profile_idc
    w.write_bits(profile_idc, 8)
    # constraint_set0-5_flags + reserved_zero_2bits
    # For High 4:2:2: constraint_set0=0, set1=0, set2=0, set3=0, set4=0, set5=0
    w.write_bits(0x00, 8)
    # level_idc
    w.write_bits(level_idc, 8)
    # seq_parameter_set_id
    w.write_ue(0)
    
    # High profile extensions
    if profile_idc in (100, 110, 122, 244, 44, 83, 86, 118, 128, 138, 139, 134):
        w.write_ue(chroma_format_idc)           # chroma_format_idc
        w.write_ue(bit_depth_luma - 8)          # bit_depth_luma_minus8
        w.write_ue(bit_depth_chroma - 8)        # bit_depth_chroma_minus8
        w.write_bool(False)                      # qpprime_y_zero_transform_bypass_flag
        w.write_bool(False)                      # seq_scaling_matrix_present_flag
    
    # log2_max_frame_num_minus4
    w.write_ue(0)  # log2_max_frame_num = 4, so max_frame_num = 16
    # pic_order_cnt_type
    w.write_ue(0)
    # log2_max_pic_order_cnt_lsb_minus4
    w.write_ue(2)  # log2_max_poc_lsb = 6, so max_poc_lsb = 64
    # max_num_ref_frames
    w.write_ue(2)
    # gaps_in_frame_num_value_allowed_flag
    w.write_bool(False)
    # pic_width_in_mbs_minus1
    w.write_ue(pic_width_in_mbs - 1)
    # pic_height_in_map_units_minus1 (frame_mbs_only=1, so same as mbs)
    w.write_ue(pic_height_in_mbs - 1)
    # frame_mbs_only_flag
    w.write_bool(True)
    # direct_8x8_inference_flag
    w.write_bool(True)
    
    # frame_cropping_flag
    # 3840x2160: 240*16=3840, 135*16=2160, no cropping needed
    needs_crop = (pic_width_in_mbs * 16 != 3840) or (pic_height_in_mbs * 16 != 2160)
    w.write_bool(needs_crop)
    if needs_crop:
        crop_right = (pic_width_in_mbs * 16 - 3840) // 2
        crop_bottom = (pic_height_in_mbs * 16 - 2160) // 2
        w.write_ue(0)           # crop_left
        w.write_ue(crop_right)  # crop_right
        w.write_ue(0)           # crop_top
        w.write_ue(crop_bottom) # crop_bottom
    
    # VUI parameters
    w.write_bool(True)  # vui_parameters_present_flag
    
    # aspect_ratio_info_present_flag
    w.write_bool(True)
    w.write_bits(1, 8)  # aspect_ratio_idc = 1 (1:1 square pixels)
    
    # overscan_info_present_flag
    w.write_bool(False)
    
    # video_signal_type_present_flag
    w.write_bool(True)
    w.write_bits(5, 3)   # video_format = 5 (unspecified)
    w.write_bool(False)   # video_full_range_flag = 0 (limited/TV range)
    w.write_bool(True)    # colour_description_present_flag
    w.write_bits(1, 8)    # colour_primaries = 1 (BT.709)
    w.write_bits(1, 8)    # transfer_characteristics = 1 (BT.709)
    w.write_bits(1, 8)    # matrix_coefficients = 1 (BT.709)
    
    # chroma_loc_info_present_flag
    w.write_bool(False)
    
    # timing_info_present_flag
    w.write_bool(True)
    w.write_bits(1001, 32)   # num_units_in_tick
    w.write_bits(60000, 32)  # time_scale (field rate = 59.94, frame rate = 29.97)
    w.write_bool(True)       # fixed_frame_rate_flag
    
    # nal_hrd_parameters_present_flag
    w.write_bool(False)
    # vcl_hrd_parameters_present_flag
    w.write_bool(False)
    # pic_struct_present_flag
    w.write_bool(False)
    # bitstream_restriction_flag
    w.write_bool(True)
    w.write_bool(False)      # motion_vectors_over_pic_boundaries_flag
    w.write_ue(2)            # max_bytes_per_pic_denom
    w.write_ue(1)            # max_bits_per_mb_denom
    w.write_ue(9)            # log2_max_mv_length_horizontal
    w.write_ue(8)            # log2_max_mv_length_vertical
    w.write_ue(1)            # max_num_reorder_frames
    w.write_ue(2)            # max_dec_frame_buffering
    
    # RBSP trailing bits
    w.write_bool(True)  # stop bit
    
    return nal_header + w.to_bytes()


def _build_pps_nal(pps_id=0):
    """Build a valid H.264 PPS NAL unit."""
    w = ExpGolombWriter()
    
    nal_header = bytes([0x68])  # ref_idc=3, type=8(PPS)
    
    # pic_parameter_set_id
    w.write_ue(pps_id)
    # seq_parameter_set_id
    w.write_ue(0)
    # entropy_coding_mode_flag (CABAC=1 for High profile)
    w.write_bool(True)
    # bottom_field_pic_order_in_frame_present_flag
    w.write_bool(False)
    # num_slice_groups_minus1
    w.write_ue(0)
    # num_ref_idx_l0_default_active_minus1
    w.write_ue(0)
    # num_ref_idx_l1_default_active_minus1
    w.write_ue(0)
    # weighted_pred_flag
    w.write_bool(False)
    # weighted_bipred_idc
    w.write_bits(0, 2)
    # pic_init_qp_minus26
    w.write_se(0)
    # pic_init_qs_minus26
    w.write_se(0)
    # chroma_qp_index_offset
    w.write_se(0)
    # deblocking_filter_control_present_flag
    w.write_bool(True)
    # constrained_intra_pred_flag — Sony XAVC-S uses independent slices
    w.write_bool(True)
    # redundant_pic_cnt_present_flag
    w.write_bool(False)
    
    # High profile extensions
    # transform_8x8_mode_flag
    w.write_bool(True)
    # pic_scaling_matrix_present_flag
    w.write_bool(False)
    # second_chroma_qp_index_offset
    w.write_se(0)
    
    # RBSP trailing bits
    w.write_bool(True)
    
    return nal_header + w.to_bytes()


def parse_sps(sps_bytes):
    """Parse H.264 SPS NAL unit. Returns dict with codec info."""
    reader = BitReader(sps_bytes[1:])
    
    profile_idc = reader.read_bits(8)
    constraint_flags = reader.read_bits(8)
    level_idc = reader.read_bits(8)
    seq_parameter_set_id = reader.read_ue()
    
    chroma_format_idc = 1
    bit_depth_luma_minus8 = 0
    bit_depth_chroma_minus8 = 0
    
    if profile_idc in (100, 110, 122, 244, 44, 83, 86, 118, 128, 138, 139, 134):
        chroma_format_idc = reader.read_ue()
        if chroma_format_idc == 3:
            reader.read_bit() # separate_colour_plane_flag
        bit_depth_luma_minus8 = reader.read_ue()
        bit_depth_chroma_minus8 = reader.read_ue()
        reader.read_bit() # qpprime_y_zero_transform_bypass_flag
        if reader.read_bit(): # seq_scaling_matrix_present_flag
            num_scaling_lists = 8 if chroma_format_idc != 3 else 12
            for i in range(num_scaling_lists):
                if reader.read_bit():
                    last_scale = 8
                    next_scale = 8
                    size_of_scaling_list = 16 if i < 6 else 64
                    for j in range(size_of_scaling_list):
                        if next_scale != 0:
                            delta_scale = reader.read_se()
                            next_scale = (last_scale + delta_scale + 256) % 256
                        last_scale = last_scale if next_scale == 0 else next_scale
    
    reader.read_ue() # log2_max_frame_num_minus4
    pic_order_cnt_type = reader.read_ue()
    if pic_order_cnt_type == 0:
        reader.read_ue() # log2_max_pic_order_cnt_lsb_minus4
    elif pic_order_cnt_type == 1:
        reader.read_bit()
        reader.read_se()
        reader.read_se()
        num_ref_frames_in_poc_cycle = reader.read_ue()
        for _ in range(num_ref_frames_in_poc_cycle):
            reader.read_se()
            
    reader.read_ue() # max_num_ref_frames
    reader.read_bit() # gaps_in_frame_num_value_allowed_flag
    pic_width_in_mbs_minus1 = reader.read_ue()
    pic_height_in_map_units_minus1 = reader.read_ue()
    
    frame_mbs_only_flag = reader.read_bit()
    if not frame_mbs_only_flag:
        reader.read_bit() # mb_adaptive_frame_field_flag
        
    reader.read_bit() # direct_8x8_inference_flag
    
    frame_cropping_flag = reader.read_bit()
    crop_left = 0
    crop_right = 0
    crop_top = 0
    crop_bottom = 0
    if frame_cropping_flag:
        crop_left = reader.read_ue()
        crop_right = reader.read_ue()
        crop_top = reader.read_ue()
        crop_bottom = reader.read_ue()
        
    vui_parameters_present_flag = reader.read_bit()
    
    time_scale = 60000
    num_units_in_tick = 1001
    
    if vui_parameters_present_flag:
        if reader.read_bit(): # aspect_ratio_info_present_flag
            if reader.read_bits(8) == 255: # Extended_SAR
                reader.read_bits(16)
                reader.read_bits(16)
        if reader.read_bit(): # overscan_info_present_flag
            reader.read_bit()
        if reader.read_bit(): # video_signal_type_present_flag
            reader.read_bits(3)
            reader.read_bit()
            if reader.read_bit(): # colour_description_present_flag
                reader.read_bits(24)
        if reader.read_bit(): # chroma_loc_info_present_flag
            reader.read_ue()
            reader.read_ue()
        if reader.read_bit(): # timing_info_present_flag
            num_units_in_tick = reader.read_bits(32)
            time_scale = reader.read_bits(32)
            
    width = (pic_width_in_mbs_minus1 + 1) * 16
    height = (2 - frame_mbs_only_flag) * (pic_height_in_map_units_minus1 + 1) * 16
    
    if frame_cropping_flag:
        crop_unit_x = 1 if chroma_format_idc == 0 else 2
        crop_unit_y = (2 - frame_mbs_only_flag) * (1 if chroma_format_idc == 0 else (2 if chroma_format_idc == 1 else 1))
        width -= (crop_left + crop_right) * crop_unit_x
        height -= (crop_top + crop_bottom) * crop_unit_y
        
    return {
        'profile_idc': profile_idc,
        'constraint_flags': constraint_flags,
        'level_idc': level_idc,
        'chroma_format_idc': chroma_format_idc,
        'bit_depth_luma': bit_depth_luma_minus8,
        'bit_depth_chroma': bit_depth_chroma_minus8,
        'width': width,
        'height': height,
        'time_scale': time_scale,
        'num_units_in_tick': num_units_in_tick,
    }

def build_avcc(sps_bytes, pps_list, profile_idc, level_idc, 
               chroma_format_idc=1, bit_depth_luma=0, bit_depth_chroma=0):
    """Build avcC box content (not including box header).
    pps_list can be a single bytes object or a list of bytes objects."""
    if isinstance(pps_list, bytes):
        pps_list = [pps_list]
    avcc = bytearray()
    avcc.append(1) # version
    avcc.append(profile_idc)
    avcc.append(sps_bytes[2]) # profile compatibility (constraint_set flags)
    avcc.append(level_idc)
    avcc.append(0xFF) # lengthSizeMinusOne = 3
    avcc.append(0xE1) # numOfSPS = 1
    avcc.extend(struct.pack('>H', len(sps_bytes)))
    avcc.extend(sps_bytes)
    avcc.append(len(pps_list))  # numOfPPS
    for pps in pps_list:
        avcc.extend(struct.pack('>H', len(pps)))
        avcc.extend(pps)
    
    if profile_idc in (100, 110, 122, 244, 44, 83, 86, 118, 128, 138, 139, 134):
        avcc.append(0xFC | (chroma_format_idc & 0x03))
        avcc.append(0xF8 | (bit_depth_luma & 0x07))
        avcc.append(0xF8 | (bit_depth_chroma & 0x07))
        avcc.append(0) # numSPSExt
        
    return bytes(avcc)

def build_video_stsd_standalone(avcc_data, width, height):
    """Build video stsd box with avc1 sample entry."""
    avc1 = b'\x00' * 6
    avc1 += struct.pack('>H', 1) # dref_idx
    avc1 += b'\x00' * 16 # pre_defined, reserved, pre_defined
    avc1 += struct.pack('>HH', width, height)
    avc1 += struct.pack('>II', 0x00480000, 0x00480000)
    avc1 += b'\x00' * 4
    avc1 += struct.pack('>H', 1) # frame_count
    compressorname = b'\x0AAVC Coding' + b'\x00' * 21
    avc1 += compressorname
    avc1 += struct.pack('>H', 0x0018) # depth
    avc1 += struct.pack('>h', -1) # pre_defined
    avc1 += make_box('avcC', avcc_data)
    # pasp: pixel aspect ratio — 1:1 square pixels for 4K
    avc1 += make_box('pasp', struct.pack('>II', 1, 1))
    
    stsd_data = struct.pack('>I', 1) + make_box('avc1', avc1)
    return make_full_box('stsd', 0, 0, stsd_data)

def build_audio_stsd_standalone():
    """Build audio stsd for PCM s16be (48kHz, stereo)."""
    pcmc = make_full_box('pcmC', 0, 0, struct.pack('>BB', 0, 16)) # format_flags=0, PCM_sample_size=16
    
    ipcm = b'\x00' * 6
    ipcm += struct.pack('>H', 1) # dref_idx
    ipcm += b'\x00' * 8 # reserved
    ipcm += struct.pack('>HH', 2, 16) # channel_count, sample_size
    ipcm += struct.pack('>HH', 0, 0) # compression_id, packet_size
    ipcm += struct.pack('>I', 48000 << 16) # sample_rate
    ipcm += pcmc
    
    stsd_data = struct.pack('>I', 1) + make_box('ipcm', ipcm)
    return make_full_box('stsd', 0, 0, stsd_data)

def build_meta_stsd_standalone():
    """Build metadata track stsd (rtmd handler)."""
    rtmd = b'\x00' * 6
    rtmd += struct.pack('>H', 1)
    stsd_data = struct.pack('>I', 1) + make_box('rtmd', rtmd)
    return make_full_box('stsd', 0, 0, stsd_data)

def build_tkhd_standalone(track_id, duration_ticks, width=0, height=0, volume=0, is_enabled=True):
    """Build tkhd (track header) from scratch."""
    flags = 3 if is_enabled else 0
    data = struct.pack('>IIIIIIII',
                       0, 0, track_id, 0, duration_ticks,
                       0, 0, 0)
    data += struct.pack('>hhHh', 0, 0, volume, 0)
    data += struct.pack('>9I',
                        0x00010000, 0, 0,
                        0, 0x00010000, 0,
                        0, 0, 0x40000000)
    data += struct.pack('>II', width << 16, height << 16)
    return make_full_box('tkhd', 0, flags, data)

def build_hdlr_standalone(handler_type, handler_name):
    """Build hdlr box."""
    if isinstance(handler_type, str): handler_type = handler_type.encode('ascii')
    if isinstance(handler_name, str): handler_name = handler_name.encode('utf-8')
    if not handler_name.endswith(b'\x00'): handler_name += b'\x00'
        
    data = struct.pack('>I', 0)
    data += handler_type
    data += b'\x00' * 12
    data += handler_name
    return make_full_box('hdlr', 0, 0, data)

def build_vmhd_standalone():
    return make_full_box('vmhd', 0, 1, struct.pack('>HHHH', 0, 0, 0, 0))

def build_smhd_standalone():
    return make_full_box('smhd', 0, 0, struct.pack('>HH', 0, 0))

def build_nmhd_standalone():
    return make_full_box('nmhd', 0, 0, b'')

def build_dinf_standalone():
    url = make_full_box('url ', 0, 1, b'')
    dref = make_full_box('dref', 0, 0, struct.pack('>I', 1) + url)
    return make_box('dinf', dref)

def build_hevc_ctts(total_samples, delta):
    """Build ctts box for HEVC using Sony's fixed IBBP pattern: (1, 3*delta), (2, 0)."""
    offset_val = 3 * delta
    entries = []
    remaining = total_samples
    while remaining > 0:
        entries.append((1, offset_val))
        remaining -= 1
        if remaining <= 0:
            break
        take = min(2, remaining)
        entries.append((take, 0))
        remaining -= take
        
    data = struct.pack('>I', len(entries))
    for count, off in entries:
        data += struct.pack('>II', count, off)
    return make_full_box('ctts', 0, 0, data)

def build_moov_standalone(rsv_info, codec_info, mdat_offset):
    """Build complete moov box without a donor."""
    chunks = rsv_info['chunks']
    num_chunks = rsv_info['num_chunks']
    
    video_frame_sizes = []
    for c in chunks:
        video_frame_sizes.extend(c['video_frame_sizes'])
    total_video_samples = len(video_frame_sizes)
    meta_spc = rsv_info.get('samples_per_chunk', 15)
    total_meta_samples = num_chunks * meta_spc
    total_audio_samples = sum(c['audio_samples'] for c in chunks)
    
    video_timescale = codec_info['time_scale'] // 2
    video_delta = codec_info['num_units_in_tick']
    audio_timescale = 48000
    meta_timescale = video_timescale
    meta_delta = video_delta
    
    mvhd_timescale = video_timescale * 3
    
    video_duration = total_video_samples * video_delta
    meta_duration = total_meta_samples * meta_delta
    audio_duration = total_audio_samples
    
    movie_duration = int(video_duration * mvhd_timescale / video_timescale)
    
    video_offsets = [mdat_offset + c['video_offset'] for c in chunks]
    audio_offsets = [mdat_offset + c['audio_offset'] for c in chunks]
    meta_offsets = [mdat_offset + c['meta_offset'] for c in chunks]
    
    sync_samples = []
    sample_num = 0
    for c in chunks:
        sample_num += 1
        sync_samples.append(sample_num)
        sample_num += len(c['video_frame_sizes']) - 1
        
    video_stsc_entries = []
    prev_spc = None
    for i, c in enumerate(chunks):
        spc = len(c['video_frame_sizes'])
        if spc != prev_spc:
            video_stsc_entries.append((i + 1, spc, 1))
            prev_spc = spc
            
    audio_stsc_entries = []
    prev_spc = None
    for i, c in enumerate(chunks):
        spc = c['audio_samples']
        if spc != prev_spc:
            audio_stsc_entries.append((i + 1, spc, 1))
            prev_spc = spc
            
    # Video
    # Video stsd — codec-aware
    is_hevc = codec_info.get('codec') == 'hevc'
    if is_hevc:
        hvcc = build_hvcc(codec_info)
        video_stsd = build_video_stsd_hevc(hvcc, codec_info['width'], codec_info['height'])
    else:
        avcc = build_avcc(codec_info['sps_bytes'], codec_info['pps_list'], codec_info['profile_idc'], codec_info['level_idc'], codec_info['chroma_format_idc'], codec_info['bit_depth_luma'], codec_info['bit_depth_chroma'])
        video_stsd = build_video_stsd_standalone(avcc, codec_info['width'], codec_info['height'])
    
    video_stbl = b''
    video_stbl += video_stsd
    video_stbl += build_stts(total_video_samples, video_delta)
    if is_hevc:
        video_stbl += build_hevc_ctts(total_video_samples, video_delta)
    video_stbl += build_stsc_variable(video_stsc_entries)
    video_stbl += build_stsz_variable(video_frame_sizes)
    video_stbl += build_co64(video_offsets)
    video_stbl += build_stss(sync_samples)
    
    video_minf = build_vmhd_standalone() + build_dinf_standalone() + make_box('stbl', video_stbl)
    video_mdia = (build_mdhd(video_timescale, video_duration) +
                  build_hdlr_standalone('vide', 'Video Media Handler') +
                  make_box('minf', video_minf))
    video_tkhd = build_tkhd_standalone(1, movie_duration, codec_info['width'], codec_info['height'])
    video_trak = video_tkhd + make_box('mdia', video_mdia)
    
    # Audio stsd — codec-aware
    audio_stbl = b''
    audio_stbl += build_audio_stsd_twos() if is_hevc else build_audio_stsd_standalone()
    audio_stbl += build_stts(total_audio_samples, 1)
    audio_stbl += build_stsc_variable(audio_stsc_entries)
    audio_stbl += build_stsz_uniform(4, total_audio_samples)
    audio_stbl += build_co64(audio_offsets)
    
    audio_minf = build_smhd_standalone() + build_dinf_standalone() + make_box('stbl', audio_stbl)
    audio_mdia = (build_mdhd(audio_timescale, audio_duration) +
                  build_hdlr_standalone('soun', 'Sound Media Handler') +
                  make_box('minf', audio_minf))
    audio_tkhd = build_tkhd_standalone(2, movie_duration, volume=0x0100)
    audio_trak = audio_tkhd + make_box('mdia', audio_mdia)
    
    # Meta
    meta_stbl = b''
    meta_stbl += build_meta_stsd_standalone()
    meta_stbl += build_stts(total_meta_samples, meta_delta)
    meta_stbl += build_stsc_variable([(1, meta_spc, 1)])
    meta_stbl += build_stsz_uniform(META_SAMPLE_SIZE, total_meta_samples)
    meta_stbl += build_co64(meta_offsets)
    
    meta_minf = build_nmhd_standalone() + build_dinf_standalone() + make_box('stbl', meta_stbl)
    meta_mdia = (build_mdhd(meta_timescale, meta_duration) +
                 build_hdlr_standalone('meta', 'Timed Metadata Media Handler') +
                 make_box('minf', meta_minf))
    meta_tkhd = build_tkhd_standalone(3, movie_duration)
    meta_trak = meta_tkhd + make_box('mdia', meta_mdia)
    
    mvhd = build_mvhd(mvhd_timescale, movie_duration, next_track_id=4)
    moov_content = mvhd
    moov_content += make_box('trak', video_trak)
    moov_content += make_box('trak', audio_trak)
    moov_content += make_box('trak', meta_trak)
    
    return make_box('moov', moov_content)

def build_mp4_standalone(rsv_path, output_path, log_fn=None):
    """Build MP4 from RSV file without a donor MP4."""
    if log_fn is None:
        log_fn = print
    log_fn(f"RSV:    {rsv_path}")
    log_fn(f"Output: {output_path}")
    log_fn("")
    
    log_fn("=== Parsing RSV file ===")
    rsv_info = parse_rsv(rsv_path, log_fn=log_fn)
    log_fn("")
    
    # Detect codec type
    codec_type = detect_codec_type(rsv_path)
    log_fn(f"=== Detecting codec parameters ({codec_type.upper()}) ===")
    
    with open(rsv_path, 'rb') as f:
        if codec_type == 'hevc':
            codec_info = construct_hevc_params(
                f,
                meta_start=rsv_info['chunks'][0]['meta_offset'],
            )
            # Determine frame rate from samples_per_chunk
            spc = rsv_info.get('samples_per_chunk', 24)
            if spc == 24:
                codec_info['time_scale'] = 48000
                codec_info['num_units_in_tick'] = 1001
            elif spc == 15:
                codec_info['time_scale'] = 60000
                codec_info['num_units_in_tick'] = 1001
            else:
                codec_info['time_scale'] = 48000
                codec_info['num_units_in_tick'] = 1001
        else:
            sps, pps, codec_info = construct_sps_pps(
                f,
                video_start=rsv_info['chunks'][0]['video_offset'],
                meta_start=rsv_info['chunks'][0]['meta_offset'],
            )
            codec_info['codec'] = 'h264'
    
    log_fn(f"  Video: {codec_info['width']}x{codec_info['height']}, "
           f"Profile: {codec_info['profile_idc']} Level: {codec_info['level_idc']}")
    fps = codec_info['time_scale'] / (2 * codec_info['num_units_in_tick'])
    log_fn(f"  FPS: {fps:.2f} ({codec_info['time_scale']}/(2*{codec_info['num_units_in_tick']}))")
    
    # Build ftyp
    if codec_type == 'hevc':
        ftyp = struct.pack('>I4s4sI', 32, b'ftyp', b'XAVC', 0x010A1FFF)
        ftyp += b'XAVCmp42iso2nras'
    else:
        ftyp = struct.pack('>I4s4sI', 28, b'ftyp', b'XAVC', 0x01004F1F)
        ftyp += b'XAVCmp42iso2'
    
    rsv_size = rsv_info['rsv_size']
    ftyp_size = len(ftyp)
    
    dummy_mdat_header = make_mdat_header(rsv_size)
    dummy_moov = build_moov_standalone(rsv_info, codec_info, ftyp_size + 1_000_000 + len(dummy_mdat_header))
    moov_size = len(dummy_moov)
    
    mdat_header = make_mdat_header(rsv_size)
    mdat_data_offset = ftyp_size + moov_size + len(mdat_header)
    moov = build_moov_standalone(rsv_info, codec_info, mdat_data_offset)
    assert len(moov) == moov_size
    
    log_fn("=== Writing output MP4 ===")
    log_fn(f"  ftyp: {ftyp_size} bytes")
    log_fn(f"  moov: {moov_size:,} bytes")
    log_fn(f"  mdat: {rsv_size:,} bytes")
    
    BUF = 4 * 1024 * 1024
    with open(output_path, 'wb') as out:
        out.write(ftyp)
        out.write(moov)
        out.write(mdat_header)
        with open(rsv_path, 'rb') as rsv:
            remaining = rsv_size
            written = 0
            while remaining > 0:
                chunk = rsv.read(min(BUF, remaining))
                if not chunk: break
                out.write(chunk)
                remaining -= len(chunk)
                written += len(chunk)
                if written % (100 * 1024 * 1024) == 0:
                    pct = written / rsv_size * 100
                    log_fn(f"  Writing mdat: {written:,} / {rsv_size:,} ({pct:.0f}%)")
                    
    log_fn(f"\nDone! Output: {output_path}")

def main():
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} [donor.mp4] <input.rsv> [output.mp4]")
        print("  Provide donor.mp4 for donor mode, or just input.rsv for standalone mode.")
        sys.exit(1)
        
    if len(sys.argv) == 2 or (len(sys.argv) == 3 and not sys.argv[1].lower().endswith('.mp4')):
        # Standalone mode
        rsv_path = sys.argv[1]
        if len(sys.argv) >= 3:
            output_path = sys.argv[2]
        else:
            base = os.path.splitext(rsv_path)[0]
            output_path = base + '_repaired.mp4'
            
        if not os.path.exists(rsv_path):
            print(f"Error: RSV file not found: {rsv_path}")
            sys.exit(1)
            
        build_mp4_standalone(rsv_path, output_path)
    else:
        # Donor mode
        if len(sys.argv) < 3:
            print(f"Usage: {sys.argv[0]} <donor.mp4> <input.rsv> [output.mp4]")
            sys.exit(1)
            
        donor_path = sys.argv[1]
        rsv_path = sys.argv[2]
        
        if len(sys.argv) >= 4:
            output_path = sys.argv[3]
        else:
            base = os.path.splitext(rsv_path)[0]
            output_path = base + '_repaired.mp4'
        
        if not os.path.exists(donor_path):
            print(f"Error: Donor file not found: {donor_path}")
            sys.exit(1)
        if not os.path.exists(rsv_path):
            print(f"Error: RSV file not found: {rsv_path}")
            sys.exit(1)
        
        build_mp4(donor_path, rsv_path, output_path)

if __name__ == '__main__':
    main()
