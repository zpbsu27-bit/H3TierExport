"""HQ patch quilting with fixed flip-compatible two-dimensional boundaries.

Used by the versioned asset-repair terrain operator, never an art approval.
Every output pixel is an actual input pixel.
The boundary template uses mirrored HQ quadrants, not averaged edge colors.
"""
import numpy as np


def vertical_cut(error, start=None, end=None):
    """Minimum-cost top-to-bottom seam, with an optional pinned endpoint."""
    h, w = error.shape
    if h < 1 or w < 1 or not np.isfinite(error).all() or np.any(error < 0):
        raise ValueError('Finite nonnegative seam costs required')
    previous = error[0].astype(np.float64).copy()
    if start is not None:
        previous[:] = np.inf
        previous[start] = error[0, start]
    parents = np.empty((h, w), np.int8)
    positions = np.arange(w)
    for y in range(1, h):
        choices = np.stack((np.r_[np.inf, previous[:-1]], previous, np.r_[previous[1:], np.inf]))
        direction = choices.argmin(axis=0)
        parents[y] = direction - 1
        previous = error[y] + choices[direction, positions]
    x = int(previous.argmin()) if end is None else end
    if not np.isfinite(previous[x]):
        raise ValueError('Seam endpoints cannot be connected')
    path = np.empty(h, np.int32)
    for y in range(h - 1, -1, -1):
        path[y] = x
        if y:
            x += int(parents[y, x])
    return path


def error_rgb(a, b):
    delta = a[..., :3].astype(np.float32) - b[..., :3].astype(np.float32)
    return np.sum(delta * delta, axis=-1)


def patch_quilt(source, size, seed, patch=256, overlap=48, candidates=32):
    """Deterministic Efros/Freeman-style patch synthesis with binary seams."""
    if (source.dtype != np.uint8 or source.ndim != 3 or source.shape[2] != 4
            or min(source.shape[:2]) < patch or not np.all(source[..., 3] == 255)
            or not 8 <= overlap < patch // 2 or size < patch or candidates < 2):
        raise ValueError('Opaque true HQ source and explicit patch parameters required')
    rng = np.random.default_rng(seed)
    out = np.zeros((size, size, 4), np.uint8)
    provenance = np.full((size, size, 2), -1, np.int32)
    stride = patch - overlap
    placements = []
    for y in range(0, size, stride):
        for x in range(0, size, stride):
            h, w = min(patch, size-y), min(patch, size-x)
            left, top = min(overlap, w) if x else 0, min(overlap, h) if y else 0
            target = out[y:y+h, x:x+w]
            scores = []
            trials = []
            for _ in range(candidates):
                sx = int(rng.integers(source.shape[1]-w+1))
                sy = int(rng.integers(source.shape[0]-h+1))
                block = source[sy:sy+h, sx:sx+w]
                score = float(error_rgb(target[:, :left], block[:, :left]).sum()) if left else 0.
                if top:
                    score += float(error_rgb(target[:top, left:], block[:top, left:]).sum())
                trials.append((sx, sy))
                scores.append(score)
            scores = np.asarray(scores)
            eligible = np.flatnonzero(scores <= scores.min() * 1.1 + 1)
            selected = int(rng.choice(eligible))
            sx, sy = trials[selected]
            block = source[sy:sy+h, sx:sx+w]
            keep = np.zeros((h, w), bool)
            if left:
                cut = vertical_cut(error_rgb(target[:, :left], block[:, :left]))
                keep[:, :left] |= np.arange(left)[None, :] < cut[:, None]
            if top:
                cut = vertical_cut(error_rgb(target[:top], block[:top]).T)
                keep[:top] |= np.arange(top)[:, None] < cut[None, :]
            target[~keep] = block[~keep]
            yy, xx = np.indices((h, w))
            coordinates = np.stack((xx+sx, yy+sy), axis=-1)
            provenance[y:y+h, x:x+w][~keep] = coordinates[~keep]
            placements.append({'target_xy': [x, y], 'source_xy': [sx, sy], 'pixels': [w, h]})
    if not np.all(out[..., 3] == 255):
        raise AssertionError('Quilting left an unfilled pixel')
    return out, provenance, placements


def boundary_template(source, size):
    """Mirror a real HQ quadrant, preserving RGB without averaging or stretch."""
    if size % 2 or min(source.shape[:2]) < size // 2:
        raise ValueError('An even canvas and sufficiently large HQ source are required')
    half = size // 2
    quarter = source[:half, :half]
    top = np.concatenate((quarter, quarter[:, ::-1]), axis=1)
    return np.concatenate((top, top[::-1]), axis=0)


def join_fixed_frame(candidate, template, minimum=96, maximum=256):
    """Four minimum-error cuts join a variable centre to a fixed real 2D rim.

    The outer minimum pixels are immutable. Joining is selection of full RGB
    pixels, with no extrusion, alpha fade, blur, color averaging or resampling.
    """
    h, w = candidate.shape[:2]
    if template.shape != candidate.shape or not 1 <= minimum < maximum < min(h, w)//2:
        raise ValueError('Matched HQ canvas and a bounded frame region required')
    cost = error_rgb(candidate, template)
    keep = np.zeros((h, w), bool)
    paths = {}
    for name, plane in (('top', cost), ('bottom', cost[::-1]), ('left', cost.T), ('right', cost.T[::-1])):
        path = vertical_cut(plane[minimum:maximum].T) + minimum
        paths[name] = path
    yy, xx = np.indices((h, w))
    keep |= yy <= paths['top'][xx]
    keep |= h-1-yy <= paths['bottom'][xx]
    keep |= xx <= paths['left'][yy]
    keep |= w-1-xx <= paths['right'][yy]
    out = candidate.copy()
    out[keep] = template[keep]
    return out, keep


def texture_variant(source, size=1024, seed=1, minimum=64, maximum=128, patch=256, overlap=48):
    candidate, provenance, placements = patch_quilt(source, size, seed, patch=patch, overlap=overlap)
    template = boundary_template(source, size)
    out, boundary = join_fixed_frame(candidate, template, minimum, maximum)
    # A source-coordinate image proves this is HQ pixel assembly, not a hidden
    # resize, repeated edge column or synthetic interpolated painting.
    yy, xx = np.indices((size, size))
    half = size // 2
    refx = np.where(xx < half, xx, size-1-xx)
    refy = np.where(yy < half, yy, size-1-yy)
    provenance[boundary] = np.stack((refx, refy), axis=-1)[boundary]
    recovered = source[provenance[..., 1], provenance[..., 0]]
    if not np.array_equal(out, recovered):
        raise AssertionError('An output pixel has no exact true HQ source coordinate')
    if (not np.array_equal(out[0], out[-1]) or not np.array_equal(out[:, 0], out[:, -1])
            or not np.array_equal(out[0], out[0, ::-1]) or not np.array_equal(out[:, 0], out[::-1, 0])):
        raise AssertionError('Runtime H/V boundary invariants failed')
    return out, {'algorithm': 'hq-patch-quilting-binary-minimum-error-cut-v1',
                 'seed': seed, 'size': size, 'patches': placements,
                 'HQ_RGB_resampled': False, 'HQ_RGB_averaged': False,
                 'all_pixels_exactly_from_source': True,
                 'boundary_frozen_pixels': int(boundary.sum()),
                 'fixed_minimum_boundary_pixels': minimum,
                 'flip_compatible_boundary_profiles': True,
                 'art_or_runtime_approved': False}
