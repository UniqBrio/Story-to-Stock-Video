"""
CLIP byte-pair tokenizer (numpy only).

Adapted from OpenAI CLIP `clip/simple_tokenizer.py` (MIT License, Copyright (c) 2021 OpenAI)
via lakeraai/onnx_clip (MIT License). ftfy is replaced by a double html.unescape — the
prompts tokenised here are our own fixed English sentences. See LICENSE-CLIP.txt.
"""

import gzip
import html
from functools import lru_cache
from pathlib import Path

import numpy as np
import regex as re

BPE_PATH = Path(__file__).with_name("bpe_simple_vocab_16e6.txt.gz")


@lru_cache()
def bytes_to_unicode() -> dict:
    bs = list(range(ord("!"), ord("~") + 1)) + list(range(ord("¡"), ord("¬") + 1)) + list(range(ord("®"), ord("ÿ") + 1))
    cs = bs[:]
    n = 0
    for b in range(2 ** 8):
        if b not in bs:
            bs.append(b)
            cs.append(2 ** 8 + n)
            n += 1
    return dict(zip(bs, [chr(c) for c in cs]))


def _pairs(word) -> set:
    return {(a, b) for a, b in zip(word, word[1:])}


class Tokenizer:
    def __init__(self, bpe_path: Path = BPE_PATH):
        self.byte_encoder = bytes_to_unicode()
        merges = gzip.open(bpe_path).read().decode("utf-8").split("\n")[1: 49152 - 256 - 2 + 1]
        merges = [tuple(m.split()) for m in merges]
        vocab = list(self.byte_encoder.values())
        vocab = vocab + [v + "</w>" for v in vocab] + ["".join(m) for m in merges]
        vocab += ["<|startoftext|>", "<|endoftext|>"]
        self.encoder = {v: i for i, v in enumerate(vocab)}
        self.bpe_ranks = {m: i for i, m in enumerate(merges)}
        self.cache = {"<|startoftext|>": "<|startoftext|>", "<|endoftext|>": "<|endoftext|>"}
        self.pat = re.compile(r"""<\|startoftext\|>|<\|endoftext\|>|'s|'t|'re|'ve|'m|'ll|'d|[\p{L}]+|[\p{N}]|[^\s\p{L}\p{N}]+""",
                              re.IGNORECASE)

    def bpe(self, token: str) -> str:
        if token in self.cache:
            return self.cache[token]
        word = tuple(token[:-1]) + (token[-1] + "</w>",)
        pairs = _pairs(word)
        if not pairs:
            return token + "</w>"
        while True:
            bigram = min(pairs, key=lambda p: self.bpe_ranks.get(p, float("inf")))
            if bigram not in self.bpe_ranks:
                break
            first, second = bigram
            new, i = [], 0
            while i < len(word):
                try:
                    j = word.index(first, i)
                except ValueError:
                    new.extend(word[i:])
                    break
                new.extend(word[i:j])
                i = j
                if word[i] == first and i < len(word) - 1 and word[i + 1] == second:
                    new.append(first + second)
                    i += 2
                else:
                    new.append(word[i])
                    i += 1
            word = tuple(new)
            if len(word) == 1:
                break
            pairs = _pairs(word)
        out = " ".join(word)
        self.cache[token] = out
        return out

    def encode(self, text: str) -> list:
        text = re.sub(r"\s+", " ", html.unescape(html.unescape(text))).strip().lower()
        ids = []
        for tok in re.findall(self.pat, text):
            tok = "".join(self.byte_encoder[b] for b in tok.encode("utf-8"))
            ids.extend(self.encoder[t] for t in self.bpe(tok).split(" "))
        return ids

    def encode_batch(self, texts: list, context_length: int = 77) -> np.ndarray:
        sot, eot = self.encoder["<|startoftext|>"], self.encoder["<|endoftext|>"]
        out = np.zeros((len(texts), context_length), dtype=np.int32)
        for i, t in enumerate(texts):
            ids = [sot] + self.encode(t) + [eot]
            if len(ids) > context_length:
                ids = ids[:context_length - 1] + [eot]
            out[i, :len(ids)] = ids
        return out
