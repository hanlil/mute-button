#!/usr/bin/env python3
"""
Analyze speaker sample similarities using sliding overlapping query windows.
Outputs a CSV at uploaded_files/analysis_similarity.csv and prints a short summary.
"""
import os
import sys
import csv
import time
import numpy as np
import librosa

# Repo root is the directory containing this script
REPO_ROOT = os.path.abspath(os.path.dirname(__file__))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

MODEL_PATH = 'models/speaker_encoder_fp32.onnx'  # change to use a different model

from mute_button.speaker_recognition import (
    get_embedding_for_audio_file,
    get_embedding_for_audio_sample,
    get_cosine_similarities,
    get_weighted_similarity_score,
)

SPEAKERS_DIR = os.path.join(REPO_ROOT, 'uploaded_files', 'audio', 'speakers')
OUT_CSV = os.path.join(REPO_ROOT, 'uploaded_files', 'analysis_similarity.csv')

# Windowing parameters chosen to mirror the app: frames_per_buffer = sr/5 (~0.2s)
# deque maxlen = 6 -> window ~1.2s. Use same sample rate 24000.
SR = 24000
WINDOW_SECONDS = 5#1.2
HOP_SECONDS = 0.2
WINDOW_SAMPLES = int(WINDOW_SECONDS * SR)
HOP_SAMPLES = int(HOP_SECONDS * SR)


def load_reference_embeddings(speakers_dir: str):
    """Load per-file reference embeddings for each speaker.
    Returns dict: speaker -> list of (filepath, embedding(np.ndarray, shape (1,D))).
    """
    speakers = {}
    if not os.path.isdir(speakers_dir):
        raise FileNotFoundError(f"Speakers directory not found: {speakers_dir}")

    for speaker in sorted(os.listdir(speakers_dir)):
        speaker_path = os.path.join(speakers_dir, speaker)
        if not os.path.isdir(speaker_path):
            continue
        wavs = [f for f in os.listdir(speaker_path) if f.lower().endswith('.wav')]
        items = []
        for wav in sorted(wavs):
            path = os.path.join(speaker_path, wav)
            try:
                emb = get_embedding_for_audio_file(path, model_path=MODEL_PATH)
            except Exception as e:
                print(f"Failed to embed {path}: {e}")
                continue
            arr = np.asarray(emb)
            if arr.ndim == 1:
                arr = arr[np.newaxis, :]
            items.append((path, arr))
        if items:
            speakers[speaker] = items
    return speakers


def compute_query_window_embeddings(wav_path: str):
    """Load an audio file and compute embeddings for overlapping windows.

    Returns list of embeddings (each 1-D numpy array) and number of windows.
    """
    # Load with librosa preserving channels: mono=False -> shape (n_channels, n_samples)
    y, sr = librosa.load(wav_path, sr=SR, mono=False)
    if y.ndim == 1:
        # mono -> convert to shape (n_samples, 1)
        y = y[np.newaxis, :]
    # librosa returns (channels, n_samples); convert to (n_samples, channels)
    y = y.T
    n_samples = y.shape[0]

    if n_samples < 1:
        return [], 0

    # If audio shorter than one window, zero-pad to WINDOW_SAMPLES
    if n_samples <= WINDOW_SAMPLES:
        pad = WINDOW_SAMPLES - n_samples
        y_padded = np.pad(y, ((0, pad), (0, 0)), mode='constant')
        emb = get_embedding_for_audio_sample(y_padded, SR, model_path=MODEL_PATH)
        return [np.asarray(emb).reshape(-1)], 1

    embeddings = []
    # Sliding windows
    start = 0
    while start + WINDOW_SAMPLES <= n_samples:
        win = y[start:start + WINDOW_SAMPLES]
        try:
            emb = get_embedding_for_audio_sample(win, SR, model_path=MODEL_PATH)
        except Exception as e:
            print(f"Embedding failed for window at {start} in {wav_path}: {e}")
            start += HOP_SAMPLES
            continue
        embeddings.append(np.asarray(emb).reshape(-1))
        start += HOP_SAMPLES

    # handle tail window if remainder > 0 and not covered
    if start < n_samples:
        # last window anchored at end
        win = y[max(0, n_samples - WINDOW_SAMPLES):n_samples]
        if win.shape[0] < WINDOW_SAMPLES:
            pad = WINDOW_SAMPLES - win.shape[0]
            win = np.pad(win, ((0, pad), (0, 0)), mode='constant')
        try:
            emb = get_embedding_for_audio_sample(win, SR, model_path=MODEL_PATH)
            embeddings.append(np.asarray(emb).reshape(-1))
        except Exception as e:
            print(f"Embedding failed for tail window in {wav_path}: {e}")

    return embeddings, len(embeddings)


def analyze(speakers: dict):
    """Compute mean and variance of similarity scores per query window.

    For each query file Q belonging to speaker A, compute window embeddings,
    then for each target speaker B build reference matrix of B's per-file embeddings
    (excluding Q when A==B). For each window embedding, compute similarities array
    against refs, reduce with get_weighted_similarity_score to a scalar. Then report
    mean and variance across windows for the query-target pair.
    """
    rows = []
    within_stats = []
    between_stats = []

    speakers_list = list(speakers.keys())

    # Pre-build reference matrices dict: speaker -> list of (path, emb_array)
    for sp in speakers_list:
        pass

    for sp in speakers_list:
        items = speakers[sp]
        for idx, (query_path, _) in enumerate(items):
            print(f"Processing query: {query_path}")
            q_window_embs, n_windows = compute_query_window_embeddings(query_path)
            if n_windows == 0:
                print(f"  No windows for {query_path}, skipping.")
                continue

            for target in speakers_list:
                # build reference matrix excluding query file if same speaker
                target_items = speakers[target]
                ref_embs = []
                for p, e in target_items:
                    if target == sp and p == query_path:
                        continue
                    ref_embs.append(e)
                if not ref_embs:
                    # nothing to compare to
                    continue
                ref_mat = np.concatenate(ref_embs, axis=0)

                # compute per-window scores
                scores = []
                for emb in q_window_embs:
                    sims = get_cosine_similarities(ref_mat, emb)
                    score = get_weighted_similarity_score(sims)
                    scores.append(score)

                scores_arr = np.array(scores, dtype=np.float32)
                mean_score = float(np.mean(scores_arr))
                var_score = float(np.var(scores_arr))

                rows.append({
                    'query_path': query_path,
                    'query_speaker': sp,
                    'target_speaker': target,
                    'mean_score': mean_score,
                    'var_score': var_score,
                    'n_windows': n_windows,
                    'n_refs': ref_mat.shape[0],
                })

                if target == sp:
                    within_stats.append(mean_score)
                else:
                    between_stats.append(mean_score)

    stats = {
        'n_speakers': len(speakers_list),
        'n_within_pairs': len(within_stats),
        'n_between_pairs': len(between_stats),
        'within_mean': float(np.mean(within_stats)) if within_stats else None,
        'within_std': float(np.std(within_stats)) if within_stats else None,
        'between_mean': float(np.mean(between_stats)) if between_stats else None,
        'between_std': float(np.std(between_stats)) if between_stats else None,
    }
    return rows, stats


def write_csv(rows, out_path):
    header = ['query_path', 'query_speaker', 'target_speaker', 'mean_score', 'var_score', 'n_windows', 'n_refs']
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=header)
        writer.writeheader()
        for r in rows:
            writer.writerow(r)


def main():
    start_time = time.time()
    print(f"Loading reference embeddings from: {SPEAKERS_DIR}")
    speakers = load_reference_embeddings(SPEAKERS_DIR)
    if not speakers:
        print("No speaker samples found. Exiting.")
        return
    print(f"Found {len(speakers)} speakers. Computing similarities with windowed queries...")
    rows, stats = analyze(speakers)
    write_csv(rows, OUT_CSV)

    elapsed = time.time() - start_time
    print("Analysis complete. Summary:")
    print(f"  Speakers: {stats['n_speakers']}")
    print(f"  Within-speaker pairs: {stats['n_within_pairs']}, mean={stats['within_mean']}, std={stats['within_std']}")
    print(f"  Between-speaker pairs: {stats['n_between_pairs']}, mean={stats['between_mean']}, std={stats['between_std']}")
    print(f"CSV written to: {OUT_CSV}")
    print(f"Total elapsed time: {elapsed:.2f} seconds")


if __name__ == '__main__':
    main()
