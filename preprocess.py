import os
import sys
from multiprocessing import Pool, cpu_count
from concurrent.futures import ProcessPoolExecutor
from functools import partial
from tqdm import tqdm
from itertools import repeat
from librosa.core import load, stft
from librosa.feature import mfcc
from librosa.util import frame
import pyworld as world
import pysptk as sptk
import numpy as np
from utils import repeat_last_padding, encoder
from sklearn.preprocessing import StandardScaler


def get_features(filename, *, winlen, winstep, n_mcep, mcep_alpha, minf0, maxf0, type):
    wav, sr = load(filename, sr=None)

    # get f0
    x = wav.astype(float)
    _f0, t = world.harvest(x, sr, f0_floor=minf0, f0_ceil=maxf0, frame_period=winstep * 1000)
    f0 = world.stonemask(x, _f0, t, sr)

    window_size = int(sr * winlen)
    hop_size = int(sr * winstep)

    # get mel
    if type == 'mcc':
        spec = world.cheaptrick(x, f0, t, sr, f0_floor=minf0)
        h = sptk.sp2mc(spec, n_mcep - 1, mcep_alpha).T
    else:
        h = mfcc(x, sr, n_mfcc=n_mcep, n_fft=window_size, hop_length=hop_size)
    h = np.vstack((h, f0))
    maxlen = len(x) // hop_size + 2
    h = repeat_last_padding(h, maxlen)
    id = os.path.basename(filename).replace(".wav", "")
    return (id, x, h)


def calc_stats(npzfile, out_dir):
    scaler = StandardScaler()
    data_dict = np.load(npzfile)
    for name, x in data_dict.items():
        if name[-2:] == '_h':
            scaler.partial_fit(x.T)

    mean = scaler.mean_
    scale = scaler.scale_

    np.savez(os.path.join(out_dir, 'scaler.npz'), mean=np.float32(mean), scale=np.float32(scale))


def preprocess_cmu(wav_dir, output, *, q_channels, winlen, winstep, n_mcep, mcep_alpha, minf0, maxf0, type):
    in_dir = os.path.join(wav_dir)
    out_dir = os.path.join(output)
    train_data = os.path.join(out_dir, 'train.npz')
    test_data = os.path.join(out_dir, 'test.npz')
    os.makedirs(out_dir, exist_ok=True)

    files = [os.path.join(in_dir, f) for f in os.listdir(in_dir)]
    files.sort()
    train_files = files[:1032]
    test_files = files[1032:]

    feature_fn = partial(get_features, winlen=winlen, winstep=winstep, n_mcep=n_mcep, mcep_alpha=mcep_alpha,
                         minf0=minf0, maxf0=maxf0, type=type)
    n_workers = cpu_count() // 2
    print("Running", n_workers, "processes.")

    data_dict = {}
    enc = encoder(q_channels)
    print("Processing training data ...")
    with ProcessPoolExecutor(n_workers) as executor:
        futures = [executor.submit(feature_fn, f) for f in train_files]
        for future in tqdm(futures):
            name, data, feature = future.result()
            data_dict[name] = enc(data).astype(np.uint8)
            data_dict[name + '_h'] = feature
    np.savez(train_data, **data_dict)

    data_dict = {}
    print("Processing test data ...")
    with ProcessPoolExecutor(n_workers) as executor:
        futures = [executor.submit(feature_fn, f) for f in test_files]
        for future in tqdm(futures):
            name, data, feature = future.result()
            data_dict[name] = enc(data).astype(np.uint8)
            data_dict[name + '_h'] = feature
    np.savez(test_data, **data_dict)

    calc_stats(train_data, out_dir)


def _process_wav(file_list, outfile, winlen, winstep, n_mcep, mcep_alpha, minf0, maxf0, q_channels, type):
    data_dict = {}
    enc = encoder(q_channels)
    for f in tqdm(file_list):
        wav, sr = load(f, sr=None)

        x = wav.astype(float)
        _f0, t = world.harvest(x, sr, f0_floor=minf0, f0_ceil=maxf0,
                               frame_period=winstep * 1000)  # can't adjust window size
        f0 = world.stonemask(x, _f0, t, sr)

        window_size = int(sr * winlen)
        hop_size = int(sr * winstep)
        # get mel
        if type == 'mcc':
            nfft = 2 ** (window_size - 1).bit_length()
            spec = np.abs(stft(x, n_fft=nfft, hop_length=hop_size, win_length=window_size, window='blackman')) ** 2
            h = sptk.mcep(spec, n_mcep - 1, mcep_alpha, eps=-60, etype=2, itype=4).T
        else:
            h = mfcc(x, sr, n_mfcc=n_mcep, n_fft=int(sr * winlen), hop_length=int(sr * winstep))
        h = np.vstack((h, f0))
        # mulaw encode
        wav = enc(x).astype(np.uint8)

        id = os.path.basename(f).replace(".wav", "")
        data_dict[id] = wav
        data_dict[id + "_h"] = h
    np.savez(outfile, **data_dict)


def preprocess(wav_dir, output, **kwargs):
    in_dir = os.path.join(wav_dir)
    out_dir = os.path.join(output)
    # print(in_dir, out_dir)
    train_data = os.path.join(out_dir, 'train.npz')
    test_data = os.path.join(out_dir, 'test.npz')
    os.makedirs(out_dir, exist_ok=True)

    files = [os.path.join(in_dir, f) for f in os.listdir(in_dir)]
    files.sort()
    train_files = files[:1032]
    test_files = files[1032:]

    _process_wav(train_files, train_data, **kwargs)
    _process_wav(test_files, test_data, **kwargs)

    calc_stats(train_data, out_dir)


if __name__ == '__main__':
    preprocess("/media/ycy/Shared/Datasets/cmu_us_rms_arctic/wav", "training_data", winlen=0.025, winstep=0.01,
               n_mcep=25, mcep_alpha=0.42, minf0=40, maxf0=500,
               q_channels=256, type='mcc')
