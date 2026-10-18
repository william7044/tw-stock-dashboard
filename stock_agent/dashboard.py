"""產生 docs/index.html：把分析結果嵌進 templates/dashboard.html，做成單一靜態檔（離線也能開）。"""
import json

from . import backtest
from .analyze import build_payload, load_docs
from .store import ROOT, Store, write_text_if_changed

TEMPLATE = ROOT / "templates" / "dashboard.html"
OUTPUT = ROOT / "docs" / "index.html"


def build(store=None, output=OUTPUT):
    store = store or Store()
    docs = load_docs(store, backtest.MAX_DAYS)  # 讀一次，指標與回測共用
    payload = build_payload(store, docs)
    payload["backtest"] = backtest.run(store, docs)
    # "<" 轉義後，資料裡就不可能出現 </script> 而提早結束 script 標籤
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")
    html = TEMPLATE.read_text(encoding="utf-8").replace("__PAYLOAD__", data)
    write_text_if_changed(output, html)  # 內容沒變就不寫，避免沒有新資料時也產生提交
    return output


def main():
    print(f"儀表板已產生：{build()}")


if __name__ == "__main__":
    main()
