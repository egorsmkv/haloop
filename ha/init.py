from dataclasses import dataclass, asdict
from pathlib import Path
import sys

import math
import torch
import torch.nn as nn
import torch.nn.functional as F

from . import lora
from .checkpoint import Checkpointer
from .attention import GPT
from .attention_audio import AudioEncoder
from .rnn import Encoder, Decoder
from .resnet import FixupResNet, FixupBasicBlock
from .recognizer import Recognizer


@dataclass
class GPTConfig:
    block_size: int = 1024
    vocab_size: int = 50304 # GPT-2 vocab_size of 50257, padded up to nearest multiple of 64 for efficiency
    n_layer: int = 12
    n_head: int = 12
    n_embd: int = 768
    dropout: float = 0.0
    bias: bool = False
    stable_embedding: bool = False
    causal: bool = True
    cross_attn: bool = False
    d_input: int = 1

    def state_dict(self):
        return asdict(self)


@dataclass
class AudioEncoderConfig(GPTConfig):
    block_size: int = 2048
    vocab_size: int = 128 # assume ascii
    causal: bool = False
    cross_attn: bool = False
    d_input: int = 80

    def state_dict(self):
        return asdict(self)

    
def load_model(ckpt_path, *, map_location='cpu'):
    checkpoint = torch.load(ckpt_path, map_location=map_location)

    if not 'vocab_size' in checkpoint['model_args']:
        # assume checkpoint for a large model

        checkpoint['model_args']['stable_embedding'] = True
        checkpoint['model_args']['vocab_size'] = 50257
        checkpoint['model_args']['bias'] = True

        gptconf = GPTConfig(**checkpoint['model_args'])
        model = nn.ModuleDict({'_orig_mod': GPT(gptconf)})
        model.load_state_dict(checkpoint['model'], strict=False)
    elif '_orig_mod.transformer.h.0.attn.c_attn.lora_A.weight' in checkpoint['model']:
        gptconf = GPTConfig(**checkpoint['model_args'])
        model = nn.ModuleDict({'_orig_mod': GPT(gptconf)})
        lora.attach_to_c_attn(model)
        model.load_state_dict(checkpoint['model'])
    else:
        gptconf = GPTConfig(**checkpoint['model_args'])
        model = nn.ModuleDict({'_orig_mod': GPT(gptconf)})
        model.load_state_dict(checkpoint['model'])

    model.eval()
    model.to(map_location)
    model = model._orig_mod

    return model


def create_model(arch: str, compile: bool = True):
    """
    Model architectures to initialize. Possible options:

        decoder
        encoder
        lstm
        rnnlm
        r9
        audio-encoder
        recognizer:encoder:vocab_size
        transducer:encoder:decoder:vocab_size
    """
    match arch.split(':'):
        case ['decoder']:
            gptconf = GPTConfig()
            model = GPT(gptconf)
        case ['encoder']:
            gptconf = GPTConfig(block_size=128, causal=False)
            model = GPT(gptconf)
        case ['lstm']:
            model = Encoder()
        case ['rnnlm']:
            model = Decoder(vocab_size=256,
                            emb_dim=2048,
                            hidden_dim=2048,
                            num_layers=1,
                            dropout=0.0)
        case ['r9']:
            model = FixupResNet(FixupBasicBlock, [5,5,5])
        case ['audio-encoder']:
            config = AudioEncoderConfig()
            encoder = AudioEncoder(config)
            model = nn.ModuleDict({
                'encoder': encoder,
                'recognizer': Recognizer(feat_dim=config.n_embd, vocab_size=config.vocab_size),
            })
        case ['recognizer', encoder_arch, vocab_size]:
            vocab_size = int(vocab_size)
            model = nn.ModuleDict({
                'encoder': create_model(encoder_arch, compile=False),
                'recognizer': Recognizer(feat_dim=1536, vocab_size=vocab_size),
            })
        case ['transducer', encoder_arch, decoder_arch, vocab_size]:
            vocab_size = int(vocab_size)
            model = nn.ModuleDict({
                'encoder': create_model(encoder_arch, compile=False),
                'recognizer': Recognizer(feat_dim=1024, vocab_size=vocab_size),
                'lm': create_model(decoder_arch, compile=False),
            })

    if compile:
        model = torch.compile(model)
    return model


@torch.inference_mode()
def main():
    import argparse

    class Formatter(argparse.ArgumentDefaultsHelpFormatter,
                    argparse.MetavarTypeHelpFormatter):
        pass

    parser = argparse.ArgumentParser(description='hai initializes models', formatter_class=Formatter)
    parser.add_argument('--seed', type=int, default=1337)
    parser.add_argument('arch', type=str, help=create_model.__doc__)
    parser.add_argument('path', type=Path)
    args = parser.parse_args()

    torch.manual_seed(args.seed)
    model = create_model(args.arch)
    print('creating a new model')
    print(model)
    if hasattr(model, 'config'):
        print(model.config)
        Checkpointer(args.path, save_all=True)(loss=float('inf'), epoch=-1, checkpoint_fn=lambda: {
            'model': model.state_dict(),
            'model_args': model.config.state_dict()
        })
    else:
        Checkpointer(args.path, save_all=True)(loss=float('inf'), epoch=-1, checkpoint_fn=lambda: model.state_dict())


if __name__ == '__main__':
    main()
