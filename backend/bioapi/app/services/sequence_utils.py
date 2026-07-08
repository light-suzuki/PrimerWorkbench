"""
シーケンス文字列に関する共通ユーティリティ。

現在は FASTA 由来の配列貼り付けを前提とした正規化処理のみを提供する。
"""

from __future__ import annotations


def normalize_sequence(sequence: str) -> str:
  """
  入力シーケンスから空白文字（改行・スペースなど）を除去し、大文字化した文字列を返す。

  DNA 配列を想定しているが、曖昧塩基などもそのまま残す。
  """
  return "".join(sequence.split()).upper()

