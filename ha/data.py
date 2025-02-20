from pathlib import Path

import torch
import torchaudio
from kaldialign import align


def make_frames(wav, op='fbank'):
    if op == 'mfcc':
        frames = torchaudio.compliance.kaldi.mfcc(wav)

        # utterance-level CMVN
        # frames -= frames.mean(dim=0)
        # frames /= frames.std(dim=0)
    elif op == 'fbank':
        frames = torchaudio.compliance.kaldi.fbank(wav, num_mel_bins=80)
        #frames += 8.
        #frames /= 4.
    else:
        assert False

    return frames # (T, F)


class LabelFile(torch.utils.data.Dataset):
    def __init__(self, path: Path):
        super().__init__()
        self.resample = {
            16000: torch.nn.Identity(), # speech
            22050: torchaudio.transforms.Resample(orig_freq=22050), # tts data
            32000: torchaudio.transforms.Resample(orig_freq=32000), # common voice
            48000: torchaudio.transforms.Resample(orig_freq=48000), # opus
        }
        with open(path) as f:
            self.ark = dict(line.strip().split(maxsplit=1) for line in f)
            self.filenames = list(self.ark.keys())

    def __len__(self):
        return len(self.filenames)

    def utt_id(self, index):
        return self.filenames[index]

    def __getitem__(self, index):
        wav, sr = torchaudio.load(self.filenames[index])
        resample = self.resample.get(sr)
        if not resample:
            raise ValueError(f'unsupported sample rate {sr}, add a resampler to LabelFile.resample')
        return index, make_frames(resample(wav)), self.ark[self.filenames[index]]


class Directory(torch.utils.data.Dataset):
    def __init__(self, path: Path):
        super().__init__()
        self.files = list(path.glob("*.wav"))

    def __len__(self):
        return len(self.files)

    def utt_id(self, index):
        return self.files[index].stem

    def __getitem__(self, index):
        wav, sr = torchaudio.load(self.files[index])
        assert sr == 16000
        return index, make_frames(wav), "the quick brown fox jumps over the lazy dog"


class LibriSpeech(torch.utils.data.Dataset):
    def __init__(self, url='train-clean-100'):
        super().__init__()
        self.librispeech = torchaudio.datasets.LIBRISPEECH('.', url=url, download=True)

    def __len__(self):
        return len(self.librispeech)

    def utt_id(self, index):
        wav, sr, text, speaker_id, chapter_id, utterance_id = self.librispeech[index]
        utt_id = f'{speaker_id}-{chapter_id}-{utterance_id}'
        return utt_id

    def __getitem__(self, index):
        wav, sr, text, speaker_id, chapter_id, utterance_id = self.librispeech[index]
        return index, make_frames(wav), text


class Mask(torch.utils.data.Dataset):
    def __init__(self, dataset):
        super().__init__()
        self.dataset = dataset

    def __len__(self):
        return len(self.dataset)

    def utt_id(self, index):
        return self.dataset.utt_id(index)

    def __getitem__(self, index):
        index, frames, text = self.dataset[index]

        frames = frames[None,None,:]

        frames = torchaudio.functional.mask_along_axis_iid(
            frames,
            mask_param=2, # mask a little bit as we have only 13 components
            mask_value=0,
            axis=3 # frequency
        )

        frames = torchaudio.functional.mask_along_axis_iid(
            frames,
            mask_param=7,
            mask_value=0,   
            axis=2 # time
        )

        return index, frames[0, 0, :], text


class WordDrop(torch.utils.data.Dataset):
    def __init__(self, dataset, p_drop_words=0.4):
        super().__init__()
        self.dataset = dataset
        self.p_drop_words = p_drop_words

    def __len__(self):
        return len(self.dataset)

    def utt_id(self, index):
        return self.dataset.utt_id(index)

    def __getitem__(self, index):
        index, frames, original_text = self.dataset[index]
        generator = torch.Generator().manual_seed(index)
        text = ' '.join(w for w in original_text.split(' ') if torch.rand(1, generator=generator) > self.p_drop_words)
        if not text:
            text = original_text
        if False:
            hyp, _ref = list(zip(*align(text.split(), original_text.split(), '*')))
            print(index, ' '.join(h.replace(' ', '_') for h in hyp))
        return index, frames, text


def make_dataset(s):
    match s.split(':', maxsplit=1):
        case [subset]:
            return LibriSpeech(subset)
        case ['labels', label_file]:
            return LabelFile(Path(label_file))
        case ['head', subset]:
            return torch.utils.data.Subset(make_dataset(subset), range(16))
        case ['wdrop.4', subset]:
            return WordDrop(make_dataset(subset), p_drop_words=0.4)
        case ['wdrop.1', subset]:
            return WordDrop(make_dataset(subset), p_drop_words=0.1)
        case ['mask', subset]:
            return Mask(make_dataset(subset))


def concat_datasets(s):
    if not s:
        return []
    parts = s.split(',')
    paths = [Path(part) for part in parts]
    return torch.utils.data.ConcatDataset(
        Directory(path) if path.exists() else make_dataset(str(part))
        for path, part in zip(paths, parts)
    )


if __name__ == '__main__':
    import sys
    for i, f, t in make_dataset(sys.argv[1]):
        print(i, f.mean(dim=0), f.std(dim=0), f.shape, t)
        break
    m,s = torch.zeros(80), torch.zeros(80)
    frames = torch.cat([f for i, f, t in make_dataset(sys.argv[1])], dim=0)
    print(f.mean(dim=0))
    print(f.std(dim=0))
