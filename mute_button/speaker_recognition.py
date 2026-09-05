import numpy as np
from numpy.typing import NDArray
from typing import Optional
import onnxruntime as ort
import librosa
from librosa import feature


_models = {}

def get_model(model_path: str = 'models/speaker_encoder_int8.onnx'):
    """Return an ONNX inference session for the given model path. Caches sessions per path."""
    global _models
    if model_path in _models:
        return _models[model_path]
    print(f'Loading speaker recognition model: {model_path}...')
    sess = ort.InferenceSession(model_path)
    _models[model_path] = sess
    return sess

def _compute_mel_spectrogram(audio: NDArray[np.float32]) -> NDArray[np.float32]:
    mel = librosa.feature.melspectrogram(
        y=audio, sr=24000, n_fft=1024, hop_length=256,
        n_mels=128, fmin=0, fmax=12000,
    )
    mel = np.log(np.clip(mel, a_min=1e-5, a_max=None))
    mel = mel.T[np.newaxis, ...]  # (1, time, 128)
    return mel

def _compute_embedding(audio: NDArray[np.float32], model_path: str = 'models/speaker_encoder_int8.onnx'):
    mel = _compute_mel_spectrogram(audio)
    model = get_model(model_path)
    emb = model.run(None, {'mel_spectrogram': mel.astype(np.float32)})[0]
    emb = np.asarray(emb)
    norm = np.linalg.norm(emb)
    if norm > 0:
        emb = emb / norm
    return emb

def get_embedding_for_audio_file(audio_file_path: str, model_path: str = 'models/speaker_encoder_int8.onnx'):
    audio, sr = librosa.load(audio_file_path, sr=24000, mono=True)
    return _compute_embedding(audio, model_path=model_path)

def get_embedding_for_audio_sample(audio_sample: NDArray[np.float32], sample_rate: int, model_path: str = 'models/speaker_encoder_int8.onnx'):
    audio = audio_sample.mean(axis=1)
    if sample_rate != 24000:
        audio = librosa.resample(audio, orig_sr=sample_rate, target_sr=24000)
    return _compute_embedding(audio, model_path=model_path)

def get_cosine_similarities(reference_embeddings: NDArray[np.float32], query_embedding: NDArray[np.float32]):
    if reference_embeddings is None:
        return np.array([], dtype=np.float32)
    query = np.asarray(query_embedding).reshape(-1)
    similarities = np.dot(reference_embeddings, query.T)
    return np.asarray(similarities, dtype=np.float32)


def get_weighted_similarity_score(similarities: NDArray[np.float32], method: Optional[str] = 'softmax', temperature: float = 0.1, top_k: Optional[int] = 5) -> float:
    """
    Reduce an array of similarity scores to a single score.

    Methods:
      - 'softmax' (default): softmax-weighted average which emphasizes the highest matches.
      - 'topk_mean': arithmetic mean of the top_k similarities.

    Args:
      similarities: 1-D array of similarity scores.
      method: Reduction method.
      temperature: Softmax temperature (smaller -> more focus on top values).
      top_k: If set and method uses top-k, number of top elements to consider.
    """
    if similarities is None or len(similarities) == 0:
        return 0.0
    sims = np.asarray(similarities, dtype=np.float32)

    if top_k is not None and top_k > 0 and len(sims) > top_k:
        sims = np.sort(sims)[-top_k:]

    if method == 'topk_mean':
        return float(np.mean(sims))

    # Default: softmax-weighted average (numerically stable)
    s_max = np.max(sims)
    exps = np.exp((sims - s_max) / max(temperature, 1e-6))
    weights = exps / np.sum(exps)
    score = float(np.dot(weights, sims))
    return score
