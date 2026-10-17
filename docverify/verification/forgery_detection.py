"""
Forgery / tampering detection for uploaded documents.

Three independent signals are combined into a single weighted score in
[0, 1] (0 = looks clean, 1 = strong signs of tampering):

  1. Error Level Analysis (ELA)      - weight 0.5
  2. Copy-move (clone) detection      - weight 0.35
  3. Metadata (EXIF) inconsistency    - weight 0.15

None of these signals is proof of forgery on its own - a low-quality
scan or a photo taken on an old phone can trip ELA or lack EXIF data
entirely. That's why this is a *risk score* with human-readable flags,
not a yes/no verdict: the UI shows it to the issuer/verifier and lets
them make the final call, and only crosses into `is_suspected=True`
territory once several independent signals line up.

Public entry point: analyze_document(file_path) -> dict
    {
        "score": float,            # 0.0 - 1.0
        "flags": list[str],        # human-readable reasons
        "is_suspected": bool,      # score > SUSPECT_THRESHOLD
    }
"""
import io
import logging

import cv2
import numpy as np
from PIL import Image, ExifTags

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------
SUSPECT_THRESHOLD = 0.6

WEIGHT_ELA = 0.5
WEIGHT_COPY_MOVE = 0.35
WEIGHT_METADATA = 0.15

ELA_JPEG_QUALITY = 90           # quality used to re-save the image for ELA
ELA_HOT_PIXEL_PERCENTILE = 99   # top 1% brightest error pixels = "hot"
ELA_HOT_RATIO_SUSPECT = 0.015   # >1.5% of pixels being "hot" reads as tampering
ELA_HOT_RATIO_MILD = 0.006

ORB_N_FEATURES = 2000
ORB_MATCH_DISTANCE = 40         # max Hamming distance for a "good" descriptor match
ORB_MIN_SHIFT_PX = 24           # matched keypoints closer than this are just texture, not a clone
ORB_MIN_ABSOLUTE_CLUSTER = 8    # ignore tiny clusters outright, regardless of ratio (low-keypoint images)
# Thresholds are expressed as a *ratio* of the cluster size to the total
# keypoint count rather than a raw count, so a dense, high-resolution scan
# isn't penalized just for having more keypoints overall than a small one.
ORB_CLUSTER_RATIO_SUSPECT = 0.06
ORB_CLUSTER_RATIO_MILD = 0.03

# PDFs can have many pages. Running ELA + ORB on every page is the "correct"
# thorough approach but is slow (each page is a full-resolution rasterize +
# CV pass) and most forged multi-page PDFs only have the tampering on the
# page that matters (e.g. the marks table or the signature page). We trade
# thoroughness for speed and only analyze the first page by default; set
# PDF_ANALYZE_ALL_PAGES = True if you'd rather pay the cost for full coverage.
PDF_ANALYZE_ALL_PAGES = False
PDF_MAX_PAGES = 5  # hard cap even when PDF_ANALYZE_ALL_PAGES is True

# ORB copy-move matching is O(keypoints^2) - an uncapped modern phone photo
# (12MP+) or a high-DPI scan can make that step take minutes and allocate
# huge intermediate buffers. Capping the longer side before running ELA/ORB
# keeps analysis time bounded regardless of upload size, at the cost of some
# sensitivity to very fine, sub-pixel edits (an acceptable trade for a
# risk-score heuristic, not a forensic tool).
MAX_ANALYSIS_DIMENSION = 1600


def _cap_size(pil_img: Image.Image, max_dim: int = MAX_ANALYSIS_DIMENSION) -> Image.Image:
    width, height = pil_img.size
    largest = max(width, height)
    if largest <= max_dim:
        return pil_img
    scale = max_dim / largest
    new_size = (max(1, int(width * scale)), max(1, int(height * scale)))
    return pil_img.resize(new_size, Image.LANCZOS)


def analyze_document(file_path: str) -> dict:
    """Run all forgery checks on a document and return a combined report."""
    import os
    ext = os.path.splitext(file_path)[1].lower()

    flags = []
    ela_scores = []
    copy_move_scores = []
    metadata_score = 0.0

    if ext == '.pdf':
        images = _images_from_pdf(file_path)
        if not images:
            flags.append("Could not rasterize PDF for image forensics (is poppler installed?)")
            return {"score": 0.0, "flags": flags, "is_suspected": False}
        # PDFs rarely carry per-page EXIF, so metadata check is skipped for them.
        for page_img in images:
            analysis_img = _cap_size(page_img)
            ela_score, ela_flags = _ela_score(analysis_img)
            cm_score, cm_flags = _copy_move_score(analysis_img)
            ela_scores.append(ela_score)
            copy_move_scores.append(cm_score)
            flags.extend(ela_flags)
            flags.extend(cm_flags)
    elif ext in ('.png', '.jpg', '.jpeg', '.tiff', '.bmp'):
        img = Image.open(file_path)
        metadata_score, meta_flags = _metadata_score(img)  # read EXIF before any resize
        analysis_img = _cap_size(img)
        ela_score, ela_flags = _ela_score(analysis_img)
        cm_score, cm_flags = _copy_move_score(analysis_img)
        ela_scores.append(ela_score)
        copy_move_scores.append(cm_score)
        flags.extend(ela_flags)
        flags.extend(cm_flags)
        flags.extend(meta_flags)
    else:
        logger.info("Unsupported file type for forgery analysis: %s", ext)
        return {"score": 0.0, "flags": ["Unsupported file type for image forensics"], "is_suspected": False}

    ela_final = max(ela_scores) if ela_scores else 0.0
    copy_move_final = max(copy_move_scores) if copy_move_scores else 0.0

    score = (WEIGHT_ELA * ela_final) + (WEIGHT_COPY_MOVE * copy_move_final) + (WEIGHT_METADATA * metadata_score)
    score = round(min(max(score, 0.0), 1.0), 4)

    # de-duplicate flags while preserving order
    seen = set()
    unique_flags = []
    for f in flags:
        if f not in seen:
            seen.add(f)
            unique_flags.append(f)

    return {
        "score": score,
        "flags": unique_flags,
        "is_suspected": score > SUSPECT_THRESHOLD,
    }


# ---------------------------------------------------------------------------
# PDF -> page images
# ---------------------------------------------------------------------------
def _images_from_pdf(file_path: str):
    from pdf2image import convert_from_path
    try:
        last_page = None if PDF_ANALYZE_ALL_PAGES else 1
        pages = convert_from_path(file_path, dpi=200, first_page=1, last_page=last_page)
        return pages[:PDF_MAX_PAGES]
    except Exception as exc:
        logger.warning("pdf2image failed during forgery analysis (%s) - is poppler installed?", exc)
        return []


# ---------------------------------------------------------------------------
# 1. Error Level Analysis
# ---------------------------------------------------------------------------
def _ela_score(pil_img: Image.Image):
    """
    Re-compress the image at a known JPEG quality and diff it against the
    original. Regions that were untouched since the last JPEG save settle
    into a low, uniform error level; regions that were pasted in or edited
    *after* the last save (splice/clone edits, re-typed text, etc.) tend to
    re-compress differently and stand out as unusually bright/hot patches
    in the error map. This is the classic ELA heuristic - it's noisy on
    already-low-quality scans, which is why it's only half the final score.
    """
    flags = []
    try:
        rgb = pil_img.convert('RGB')

        buffer = io.BytesIO()
        rgb.save(buffer, 'JPEG', quality=ELA_JPEG_QUALITY)
        buffer.seek(0)
        resaved = Image.open(buffer)

        original_arr = np.asarray(rgb).astype(np.int16)
        resaved_arr = np.asarray(resaved).astype(np.int16)

        if original_arr.shape != resaved_arr.shape:
            return 0.0, flags

        diff = np.abs(original_arr - resaved_arr).sum(axis=2)  # per-pixel error, 0-765

        hot_threshold = np.percentile(diff, ELA_HOT_PIXEL_PERCENTILE)
        # A uniformly-compressed genuine image has a fairly flat error map, so
        # "hot" pixels should be a small, scattered minority. A large hot
        # fraction (or a very high absolute error ceiling) suggests localized
        # tampering rather than uniform recompression noise.
        hot_ratio = float(np.mean(diff > max(hot_threshold, 25)))

        if hot_ratio > ELA_HOT_RATIO_SUSPECT:
            flags.append(f"Error Level Analysis found high-error regions ({hot_ratio:.1%} of pixels) - possible localized edit")
            score = min(1.0, 0.6 + (hot_ratio - ELA_HOT_RATIO_SUSPECT) * 20)
        elif hot_ratio > ELA_HOT_RATIO_MILD:
            flags.append(f"Error Level Analysis found some elevated-error regions ({hot_ratio:.1%} of pixels)")
            score = 0.3 + (hot_ratio - ELA_HOT_RATIO_MILD) * 30
        else:
            score = hot_ratio / ELA_HOT_RATIO_MILD * 0.3 if ELA_HOT_RATIO_MILD else 0.0

        return float(min(max(score, 0.0), 1.0)), flags
    except Exception as exc:
        logger.exception("ELA failed: %s", exc)
        return 0.0, flags


# ---------------------------------------------------------------------------
# 2. Copy-move (clone) detection via ORB keypoint matching
# ---------------------------------------------------------------------------
def _copy_move_score(pil_img: Image.Image):
    """
    Detect duplicated regions within the same image (e.g. a digit or a
    signature copy-pasted from elsewhere on the same certificate) by
    matching ORB keypoints against themselves. Matches between keypoints
    that are very close together are just repetitive texture (e.g. a
    ruled table) and are discarded; a large cluster of good matches with a
    *consistent* spatial offset (many pairs shifted by roughly the same
    dx/dy) is the signature of a copy-move forgery.
    """
    flags = []
    try:
        gray = cv2.cvtColor(np.array(pil_img.convert('RGB')), cv2.COLOR_RGB2GRAY)

        orb = cv2.ORB_create(nfeatures=ORB_N_FEATURES)
        keypoints, descriptors = orb.detectAndCompute(gray, None)
        if descriptors is None or len(keypoints) < 20:
            return 0.0, flags

        # self-matching every keypoint always turns up the keypoint itself as
        # the nearest neighbor (distance 0); a real copy-move duplicate shows
        # up as the *next* nearest neighbor. We deliberately skip Lowe's
        # ratio test here: a true clone is made of several keypoints that
        # are all near-perfect duplicates of each other, which is exactly
        # the "ambiguous, ratio test rejects it" case the ratio test exists
        # to filter out for ordinary (non-self) matching.
        bf = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=False)
        matches = bf.knnMatch(descriptors, descriptors, k=3)

        offsets = []
        for pair in matches:
            for m in pair:
                if m.queryIdx == m.trainIdx:
                    continue  # the keypoint matching itself - skip and look at the next neighbor
                if m.distance > ORB_MATCH_DISTANCE:
                    break  # neighbors are sorted by distance, so nothing further will pass either

                pt1 = np.array(keypoints[m.queryIdx].pt)
                pt2 = np.array(keypoints[m.trainIdx].pt)
                spatial_dist = np.linalg.norm(pt1 - pt2)
                if spatial_dist >= ORB_MIN_SHIFT_PX:
                    dx, dy = pt2 - pt1
                    offsets.append((round(dx / 4) * 4, round(dy / 4) * 4))  # bucket to tolerate jitter
                break  # only take the single best non-self neighbor per keypoint

        if not offsets:
            return 0.0, flags

        # find the most common (bucketed) offset - a real clone produces a
        # spike of matches sharing almost the same translation vector. Each
        # keypoint contributes at most one offset, so no double-counting to
        # correct for (a forward a->b match and its mirror b->a match land
        # in different, opposite-signed buckets).
        from collections import Counter
        counts = Counter(offsets)
        (_, _), cluster_size = counts.most_common(1)[0]
        cluster_ratio = cluster_size / len(keypoints)

        if cluster_size < ORB_MIN_ABSOLUTE_CLUSTER:
            score = 0.0
        elif cluster_ratio >= ORB_CLUSTER_RATIO_SUSPECT:
            flags.append(f"Copy-move detection found a cluster of {cluster_size} duplicated keypoints ({cluster_ratio:.1%} of all keypoints) - possible pasted region")
            score = min(1.0, 0.6 + (cluster_ratio - ORB_CLUSTER_RATIO_SUSPECT) * 4)
        elif cluster_ratio >= ORB_CLUSTER_RATIO_MILD:
            flags.append(f"Copy-move detection found {cluster_size} similarly-shifted duplicated keypoints ({cluster_ratio:.1%} of all keypoints)")
            score = 0.25 + (cluster_ratio - ORB_CLUSTER_RATIO_MILD) * 10
        else:
            score = 0.0

        return float(min(max(score, 0.0), 1.0)), flags
    except Exception as exc:
        logger.exception("Copy-move detection failed: %s", exc)
        return 0.0, flags


# ---------------------------------------------------------------------------
# 3. Metadata (EXIF) inconsistency
# ---------------------------------------------------------------------------
def _metadata_score(pil_img: Image.Image):
    """
    Mild signal only: scanned/printed documents legitimately have little or
    no EXIF data, so a *missing* EXIF block is a small nudge, not a red
    flag. A creation date that is *later* than the modification date (or a
    software tag naming an image editor) is a stronger, more specific tell
    that the file was touched after its original capture/scan.
    """
    flags = []
    try:
        exif = pil_img.getexif()
        if not exif:
            flags.append("No EXIF metadata present (common for scans, but also for stripped/edited files)")
            return 0.15, flags

        tag_map = {ExifTags.TAGS.get(k, k): v for k, v in exif.items()}
        software = tag_map.get('Software', '')
        date_original = tag_map.get('DateTimeOriginal') or tag_map.get('DateTimeDigitized')
        date_modified = tag_map.get('DateTime')

        score = 0.0

        known_editors = ('photoshop', 'gimp', 'paint.net', 'affinity', 'pixlr')
        if isinstance(software, str) and any(editor in software.lower() for editor in known_editors):
            flags.append(f"Image metadata shows it was processed with editing software: {software}")
            score = max(score, 0.5)

        if date_original and date_modified and date_original != date_modified:
            try:
                from datetime import datetime
                fmt = '%Y:%m:%d %H:%M:%S'
                dt_orig = datetime.strptime(date_original, fmt)
                dt_mod = datetime.strptime(date_modified, fmt)
                if dt_mod > dt_orig:
                    flags.append(f"File modified ({date_modified}) after original capture ({date_original})")
                    score = max(score, 0.4)
            except ValueError:
                pass

        return score, flags
    except Exception as exc:
        logger.exception("Metadata check failed: %s", exc)
        return 0.0, flags
