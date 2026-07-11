import numpy as np
from numpy.typing import NDArray
import onnxruntime as ort
import librosa
from librosa import feature


_model = None

def get_model():
    global _model
    if _model is None:
        print('Loading speaker recognition model...')
        _model = ort.InferenceSession('models/speaker_encoder_int8.onnx')
    return _model

def _compute_mel_spectrogram(audio: NDArray[np.float32]) -> NDArray[np.float32]:
    mel = librosa.feature.melspectrogram(
        y=audio, sr=24000, n_fft=1024, hop_length=256,
        n_mels=128, fmin=0, fmax=12000,
    )
    mel = np.log(np.clip(mel, a_min=1e-5, a_max=None))
    mel = mel.T[np.newaxis, ...]  # (1, time, 128)
    return mel

def _compute_embedding(audio: NDArray[np.float32]):
    mel = _compute_mel_spectrogram(audio)
    model = get_model()
    emb = model.run(None, {'mel_spectrogram': mel.astype(np.float32)})[0]
    emb = np.asarray(emb)
    norm = np.linalg.norm(emb)
    if norm > 0:
        emb = emb / norm
    return emb

def get_embedding_for_audio_file(audio_file_path: str):
    audio, sr = librosa.load(audio_file_path, sr=24000, mono=True)
    return _compute_embedding(audio)

def get_embedding_for_audio_sample(audio_sample: NDArray[np.float32], sample_rate: int):
    audio = audio_sample.mean(axis=1)
    if sample_rate != 24000:
        audio = librosa.resample(audio, orig_sr=sample_rate, target_sr=24000)
    return _compute_embedding(audio)

def get_max_cosine_similarity(reference_embeddings: NDArray[np.float32], query_embedding: NDArray[np.float32]):
    similarities = np.dot(reference_embeddings, query_embedding.T)
    return np.mean(similarities)
