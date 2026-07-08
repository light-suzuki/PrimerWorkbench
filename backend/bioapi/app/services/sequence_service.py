"""
シーケンス解析に関するドメインロジックをまとめたモジュール。

Biopython を利用して以下の処理を行う：
- basic 解析（長さ・GC%・翻訳フレーム）
- ORF 検出
- 制限酵素サイト解析
"""

from collections import Counter
from typing import Dict, List

from Bio.Restriction import AllEnzymes, RestrictionBatch
from Bio.Seq import Seq

from .sequence_utils import normalize_sequence as _normalize_sequence


def analyze_basic(sequence: str, include_translation: bool = False) -> Dict:
    """
    basic シーケンス解析を行う。

    - 長さ（bp）
    - GC 含量（%）
    - オプションで 3 フレームの翻訳結果
    """
    seq = _normalize_sequence(sequence)
    length = len(seq)

    if length == 0:
        raise ValueError("シーケンス長が 0 です。1 文字以上の塩基配列を指定してください。")

    counts = Counter(seq)
    gc_count = counts.get("G", 0) + counts.get("C", 0)
    gc_percent = (gc_count / length) * 100.0 if length > 0 else 0.0

    translations: List[Dict] = []
    if include_translation:
        dna = Seq(seq)
        # 3 フレーム分の翻訳結果を計算
        for frame in range(3):
            # frame ずらしで翻訳（末端の端数コドンは自動的に無視される）
            protein = dna[frame:].translate(to_stop=False)
            translations.append(
                {
                    "frame": frame,
                    "protein_sequence": str(protein),
                }
            )

    return {
        "length": length,
        "gc_percent": gc_percent,
        "translations": translations if include_translation else None,
    }


def find_orfs(sequence: str, min_aa_length: int) -> List[Dict]:
    """
    前方 3 フレームで ORF を検出する。

    - 開始コドン: ATG
    - 終止コドン: TAA, TAG, TGA
    - ORF 長が min_aa_length 以上のもののみを返す

    戻り値は、Pydantic モデルにそのまま渡せる dict のリスト。
    """
    if min_aa_length <= 0:
        raise ValueError("min_aa_length は 1 以上を指定してください。")

    seq = _normalize_sequence(sequence)
    n = len(seq)

    if n < 3:
        return []

    dna = Seq(seq)
    stop_codons = {"TAA", "TAG", "TGA"}
    results: List[Dict] = []

    for frame in range(3):
        i = frame
        while i <= n - 3:
            codon = seq[i : i + 3]
            if codon == "ATG":
                # 開始コドンを見つけた場合、終止コドンまで探索する
                j = i + 3
                while j <= n - 3:
                    stop_codon = seq[j : j + 3]
                    if stop_codon in stop_codons:
                        orf_nt_len = (j + 3) - i
                        orf_aa_len = orf_nt_len // 3
                        if orf_aa_len >= min_aa_length:
                            sub_seq = dna[i : j + 3]
                            protein = sub_seq.translate(to_stop=True)
                            results.append(
                                {
                                    "frame": frame,
                                    "start": i + 1,  # 1-based
                                    "end": j + 3,  # 1-based, stop コドンを含む
                                    "length_nt": orf_nt_len,
                                    "length_aa": orf_aa_len,
                                    "protein_sequence": str(protein),
                                }
                            )
                        break
                    j += 3
            i += 3

    return results


def analyze_restriction_sites(sequence: str, enzymes: List[str]) -> Dict[str, List[int]]:
    """
    指定された制限酵素ごとの切断位置を解析する。

    - Bio.Restriction.AllEnzymes から利用可能な酵素を解決
    - 解決できなかった酵素名もキーとして含めるが、cut_positions は空リストとする
    - 返り値は enzyme_name -> [cut_positions] の dict
    """
    seq = _normalize_sequence(sequence)
    dna = Seq(seq)

    # 利用可能な酵素名からクラスを引くための辞書を構築
    available = {enzyme.__name__: enzyme for enzyme in AllEnzymes}

    valid_enzymes = []
    for name in enzymes:
        enzyme_cls = available.get(name)
        if enzyme_cls is not None:
            valid_enzymes.append(enzyme_cls)

    batch = RestrictionBatch(valid_enzymes) if valid_enzymes else RestrictionBatch([])
    analysis = batch.search(dna)

    result: Dict[str, List[int]] = {}

    # 解決できた酵素については実際の切断位置を格納
    for enzyme_cls in valid_enzymes:
        name = enzyme_cls.__name__
        positions = analysis.get(enzyme_cls, [])
        result[name] = list(positions)

    # 解決できなかった酵素名についてもキーだけ追加（空リスト）
    for name in enzymes:
        if name not in result:
            result[name] = []

    return result

