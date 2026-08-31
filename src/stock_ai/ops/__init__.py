"""自動売買(正典リポジトリ)の運用モニタ。

実運用は WSL の別リポジトリで動いている。ここはその状態を読み、停止方向の操作だけを
中継する層で、売買ルールや帳簿の解釈は持たない(:mod:`stock_ai.ops.bridge` 参照)。
"""

from stock_ai.ops.bridge import OpsBridge, OpsTarget, Reply, RunResult, get_bridge

__all__ = ["OpsBridge", "OpsTarget", "Reply", "RunResult", "get_bridge"]
