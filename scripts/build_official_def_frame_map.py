#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import struct
import zlib
from collections import Counter, defaultdict
from pathlib import Path


def read_lod(path: Path):
    with path.open('rb') as f:
        if f.read(4) != b'LOD\0':
            raise ValueError(f'not LOD: {path}')
        version, count = struct.unpack('<II', f.read(8))
        f.read(80)
        rows = []
        for index in range(count):
            name = f.read(16).split(b'\0', 1)[0].decode('latin1')
            offset, size, typ, packed = struct.unpack('<IIII', f.read(16))
            rows.append({'index': index, 'name': name, 'offset': offset, 'size': size, 'type': typ, 'packed': packed})
        out = {}
        for row in rows:
            if Path(row['name']).suffix.lower() != '.def':
                continue
            f.seek(row['offset'])
            raw = f.read(row['packed'] or row['size'])
            if row['packed']:
                raw = zlib.decompress(raw)
            if len(raw) != row['size']:
                raise ValueError(f"{row['name']}: decoded size mismatch")
            out[Path(row['name']).stem.upper()] = {'lod': row, 'bytes': raw}
    return version, out


def parse_def(blob: bytes):
    if len(blob) < 784:
        raise ValueError('DEF too small')
    typ, width, height, group_count = struct.unpack_from('<4I', blob, 0)
    p = 784
    groups = []
    slots = []
    for gi in range(group_count):
        if p + 16 > len(blob):
            raise ValueError(f'group {gi}: header out of range')
        group_id, frame_count, unk1, unk2 = struct.unpack_from('<4I', blob, p)
        p += 16
        names = []
        for fi in range(frame_count):
            if p + 13 > len(blob):
                raise ValueError(f'group {group_id}: frame name out of range')
            raw_name = blob[p:p+13]
            p += 13
            name = raw_name.split(b'\0', 1)[0].decode('ascii', errors='replace')
            names.append(name)
        need = frame_count * 4
        if p + need > len(blob):
            raise ValueError(f'group {group_id}: frame offsets out of range')
        offsets = list(struct.unpack_from('<' + 'I' * frame_count, blob, p)) if frame_count else []
        p += need
        frame_rows = []
        for fi, (name, offset) in enumerate(zip(names, offsets)):
            if offset + 32 > len(blob):
                raise ValueError(f'group {group_id} frame {fi}: header offset out of range')
            frame_header = list(struct.unpack_from('<6I2i', blob, offset))
            row = {
                'group_id': group_id,
                'frame_index': fi,
                'resource': name,
                'resource_stem': Path(name).stem.upper(),
                'offset': offset,
                'frame_header': frame_header,
            }
            frame_rows.append(row)
            slots.append(row)
        groups.append({'group_id': group_id, 'frame_count': frame_count, 'unknown': [unk1, unk2], 'frames': frame_rows})
    return {'type': typ, 'width': width, 'height': height, 'group_count': group_count, 'groups': groups, 'slots': slots}


def load_pak_manifest(path: Path):
    doc = json.loads(path.read_text(encoding='utf-8'))
    entries = {}
    for e in doc['entries']:
        frames = []
        for fr in e.get('descriptor_frames', []):
            if not fr.get('parse_ok'):
                continue
            layers = fr.get('frame_layers') or []
            frames.append({
                'resource': fr['resource'],
                'values': fr.get('values', []),
                'extra_count': int(fr.get('extra_count', 0)),
                'primary_path': fr.get('frame_png'),
                'primary_size': [fr.get('frame_width'), fr.get('frame_height')] if fr.get('frame_width') is not None else None,
                'layers': layers,
            })
        entries[e['name'].upper()] = {'name': e['name'], 'frames': frames, 'sheet_count': e['sheet_count']}
    return entries


def resource_multimap(frames):
    out = defaultdict(list)
    for fr in frames:
        out[fr['resource'].upper()].append(fr)
    return out


def pick_entry(stem, common, localized):
    # Chinese target: localized entry wins when present; otherwise common.
    if stem in localized:
        return 'CH', localized[stem]
    if stem in common:
        return 'common', common[stem]
    return None, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--official', type=Path, required=True)
    ap.add_argument('--json', type=Path, required=True)
    ap.add_argument('--md', type=Path, required=True)
    a = ap.parse_args()

    lod_path = a.official / 'metadata' / 'H3SPRITE.LOD'
    _, defs = read_lod(lod_path)
    p = a.official / 'pak_frames'
    common2 = load_pak_manifest(p / 'common/sprite/x2/manifest.json')
    common3 = load_pak_manifest(p / 'common/sprite/x3/manifest.json')
    ch2 = load_pak_manifest(p / 'CH/sprite/x2/manifest.json')
    ch3 = load_pak_manifest(p / 'CH/sprite/x3/manifest.json')

    items = []
    totals = Counter()
    unresolved_samples = []
    for stem in sorted(defs):
        canonical = parse_def(defs[stem]['bytes'])
        scope2, e2 = pick_entry(stem, common2, ch2)
        scope3, e3 = pick_entry(stem, common3, ch3)
        if e2 is None or e3 is None:
            status = 'no_high_density_entry'
            totals[status] += 1
            items.append({
                'identity': stem,
                'status': status,
                'canonical': {k: canonical[k] for k in ('type','width','height','group_count')},
                'canonical_slot_count': len(canonical['slots']),
                'scope_x2': scope2,
                'scope_x3': scope3,
            })
            continue

        m2 = resource_multimap(e2['frames'])
        m3 = resource_multimap(e3['frames'])
        canonical_names = Counter(s['resource_stem'] for s in canonical['slots'])
        pak2_names = set(m2)
        pak3_names = set(m3)
        unique_canonical = set(canonical_names)
        missing2 = sorted(unique_canonical - pak2_names)
        missing3 = sorted(unique_canonical - pak3_names)
        extra2 = sorted(pak2_names - unique_canonical)
        extra3 = sorted(pak3_names - unique_canonical)
        x2_only = sorted(pak2_names - pak3_names)
        x3_only = sorted(pak3_names - pak2_names)

        mappings = []
        duplicate_descriptor_resources = []
        aux_layers = 0
        for resource in sorted(unique_canonical & pak2_names & pak3_names):
            f2 = m2[resource]
            f3 = m3[resource]
            if len(f2) != 1 or len(f3) != 1:
                duplicate_descriptor_resources.append({'resource': resource, 'x2': len(f2), 'x3': len(f3)})
                continue
            slots = [
                {'group_id': s['group_id'], 'frame_index': s['frame_index'], 'frame_header': s['frame_header']}
                for s in canonical['slots'] if s['resource_stem'] == resource
            ]
            aux_layers += int(f3[0].get('extra_count', 0))
            mappings.append({
                'resource': resource,
                'canonical_slots': slots,
                'reuse_count': len(slots),
                'x2': f2[0],
                'x3': f3[0],
            })

        exact = (
            not missing2 and not missing3 and not extra2 and not extra3 and
            not x2_only and not x3_only and not duplicate_descriptor_resources
        )
        status = 'exact_frame_name_map' if exact else 'frame_name_review'
        totals[status] += 1
        totals['canonical_slots'] += len(canonical['slots'])
        totals['mapped_resources'] += len(mappings)
        totals['mapped_slots'] += sum(m['reuse_count'] for m in mappings)
        totals['aux_layers_x3'] += aux_layers
        if not exact and len(unresolved_samples) < 80:
            unresolved_samples.append({
                'identity': stem,
                'missing_x2': missing2[:20], 'missing_x3': missing3[:20],
                'extra_x2': extra2[:20], 'extra_x3': extra3[:20],
                'x2_only': x2_only[:20], 'x3_only': x3_only[:20],
                'duplicate_descriptor_resources': duplicate_descriptor_resources[:20],
            })
        items.append({
            'identity': stem,
            'status': status,
            'source_scope': scope3,
            'canonical': {k: canonical[k] for k in ('type','width','height','group_count')},
            'canonical_slot_count': len(canonical['slots']),
            'canonical_unique_resource_count': len(unique_canonical),
            'pak_resource_count_x2': len(pak2_names),
            'pak_resource_count_x3': len(pak3_names),
            'missing_x2': missing2,
            'missing_x3': missing3,
            'extra_x2': extra2,
            'extra_x3': extra3,
            'x2_only': x2_only,
            'x3_only': x3_only,
            'duplicate_descriptor_resources': duplicate_descriptor_resources,
            'aux_layers_x3': aux_layers,
            'mappings': mappings,
        })

    report = {
        'schema': 'h3.official-def-frame-map.v1',
        'date': '2026-10-05',
        'official_root': str(a.official),
        'policy': 'Exact canonical DEF 13-byte frame-name mapping to Official HD descriptor resources; CH entry wins over common for Chinese target; auxiliary atlas placements are preserved as separate layers.',
        'counts': dict(totals),
        'canonical_def_count': len(defs),
        'items': items,
    }
    a.json.parent.mkdir(parents=True, exist_ok=True)
    a.json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')

    exact_items = [x for x in items if x['status'] == 'exact_frame_name_map']
    review_items = [x for x in items if x['status'] == 'frame_name_review']
    nohd_items = [x for x in items if x['status'] == 'no_high_density_entry']
    md = [
        '# Official HD 2015 DEF frame-name map', '',
        f'- Official canonical DEF groups: **{len(defs)}**',
        f'- Exact canonical frame-name mapped groups: **{len(exact_items)}**',
        f'- Frame-name review groups: **{len(review_items)}**',
        f'- No high-density entry: **{len(nohd_items)}**',
        f"- Canonical animation slots in mapped groups: **{totals['canonical_slots']}**",
        f"- Mapped canonical slots: **{totals['mapped_slots']}**",
        f"- Unique mapped descriptor resources: **{totals['mapped_resources']}**",
        f"- x3 auxiliary placements preserved: **{totals['aux_layers_x3']}**", '',
        '> Exact mapping uses the original DEF frame-name table, not image similarity. A descriptor resource may be reused by multiple canonical group/frame slots.', '',
        '## Exact groups', '```text', *[x['identity'] for x in exact_items], '```', '',
        '## Review groups', '```text', *[x['identity'] for x in review_items], '```', '',
        '## No HD entry', '```text', *[x['identity'] for x in nohd_items], '```', '',
        '## Review samples', '```text', *[json.dumps(x, ensure_ascii=False) for x in unresolved_samples], '```',
    ]
    a.md.write_text('\n'.join(md) + '\n', encoding='utf-8')
    print(json.dumps({
        'canonical_defs': len(defs),
        'exact': len(exact_items),
        'review': len(review_items),
        'no_hd': len(nohd_items),
        'canonical_slots': totals['canonical_slots'],
        'mapped_slots': totals['mapped_slots'],
        'mapped_resources': totals['mapped_resources'],
        'aux_layers_x3': totals['aux_layers_x3'],
    }, ensure_ascii=False))


if __name__ == '__main__':
    main()