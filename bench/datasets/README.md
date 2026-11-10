# Benchmark datasets — acquisition

[`bench/RUN-LOCOMO.md`](../RUN-LOCOMO.md) is the end-to-end walkthrough; this page
is the dataset half of it, in more detail.

Datasets are **not committed**. `datasets/` is gitignored: LoCoMo is CC BY-NC 4.0
(not ours to redistribute) and the files are large.

## LoCoMo

```sh
mkdir -p datasets
curl -sL https://raw.githubusercontent.com/snap-research/locomo/main/data/locomo10.json \
  -o datasets/locomo10.json
curl -sL https://raw.githubusercontent.com/snap-research/locomo/main/LICENSE.txt \
  -o datasets/locomo10.LICENSE.txt
```

- **Source:** `snap-research/locomo`, `data/locomo10.json`
- **Size:** 2,805,274 bytes
- **Licence: CC BY-NC 4.0 (Attribution-NonCommercial).** GitHub reports the repo
  licence as "Other / NOASSERTION"; the actual `LICENSE.txt` is Creative Commons
  Attribution-NonCommercial 4.0. **The NonCommercial term is a live question for
  any use of these numbers in commercial marketing material** — internal
  measurement and research are squarely fine, publication as product marketing is
  a decision for the project owner, not an engineering call.

### What the file actually contains (verified 2026-07-22)

`LoCoMoLoader` parses it without modification. Measured, not assumed:

| | |
|---|---|
| haystacks (conversations) | 10 |
| sessions | 272 |
| turns | 5,882 |
| **questions** | **1,986** |
| adversarial (abstention) | 446 |
| single-hop | 841 |
| temporal-reasoning | 321 |
| multi-hop | 282 |
| open-domain-knowledge | 96 |

**The published figure is 1,540; this file holds 1,986.** The difference is
exactly the 446 adversarial questions (1,986 − 446 = 1,540), so the headline
number quoted in the literature evidently excludes the adversarial category.
Anyone comparing an Engraphy figure against a published LoCoMo figure must state
which denominator they used, or the comparison is meaningless. The harness
carries all five categories and reports per-category, so either denominator can
be reconstructed from a run.

Loader liberties on this file: 6 non-string answers coerced to text, 0
image-only turns dropped.

## LongMemEval

Not acquired. Several hundred MB (`longmemeval_s` alone is ~115k tokens per
question across 500 questions), distributed via a Google Drive link from its
repo. Deferred deliberately — see design/09.

## BEAM

```sh
mkdir -p datasets/beam
curl -sL "https://huggingface.co/datasets/Mohammadta/BEAM/resolve/3205395e/data/100K-00000-of-00001.parquet"   -o datasets/beam/100K.parquet
for o in 0 5 10 15; do
  curl -sL "https://datasets-server.huggingface.co/rows?dataset=Mohammadta/BEAM&config=default&split=100K&offset=$o&length=5"     -o datasets/beam/rows_$o.json
done
```

- **Source:** `Mohammadta/BEAM` on Hugging Face, revision `3205395e`, split `100K`
- **`100K.parquet` sha256:** `c0519be25907005ba873c927c50877471d550873039d96c041554d0075a78ace`,
  recorded as the run's dataset digest
- **Licence: CC BY-SA 4.0.** Attribute Tavakoli et al., "Beyond a Million Tokens:
  Benchmarking and Enhancing Long-Term Memory in LLMs" (arXiv:2510.27246). The
  share-alike term binds redistributing the data, not reporting a score.

`BeamLoader` reads the `rows_*.json` pages beside the parquet (the harness does not
depend on pyarrow) and records each page's sha256 in the corpus notes. Run with
`--benchmark beam --dataset datasets/beam/100K.parquet`. The pages match upstream's
`chats/100K/` files (`mohammadtavakoli78/BEAM` at `b2da22e`) turn for turn and
rubric for rubric.

| | |
|---|---|
| haystacks (conversations) | 20 |
| sessions (chat batches) | 90 |
| turns | 5,732 |
| characters | 12,232,071 |
| **questions** | **400**, 40 in each of 10 categories |

The official grading is `bench/core/beam_official.py`, applied to a finished run by
`python -m bench.beam_pass`.
