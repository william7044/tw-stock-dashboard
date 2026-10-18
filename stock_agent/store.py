"""data/ 目錄的讀寫。每個交易日一個 JSON（data/daily/年/日期.json），快照型資料各有自己的資料夾。"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"


def read_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def write_text_if_changed(path, text):
    """內容沒變就不寫，回傳是否有寫入（排程才不會在沒有新資料時也產生提交）。"""
    path = Path(path)
    try:
        if path.read_text(encoding="utf-8") == text:
            return False
    except FileNotFoundError:
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    tmp.replace(path)
    return True


def write_json(path, obj):
    return write_text_if_changed(path, json.dumps(obj, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n")


class Store:
    def __init__(self, root=DATA_DIR):
        self.root = Path(root)

    def path(self, *parts):
        return self.root.joinpath(*parts)

    def read(self, *parts, default=None):
        return read_json(self.path(*parts), default)

    def write(self, obj, *parts):
        return write_json(self.path(*parts), obj)

    def files(self, folder, pattern="*.json"):
        return sorted(self.path(folder).glob(pattern))

    # ---- 每日資料 ----
    def day_path(self, iso):
        return self.path("daily", iso[:4], f"{iso}.json")

    def load_day(self, iso):
        return read_json(self.day_path(iso), {"date": iso})

    def save_day(self, doc):
        return write_json(self.day_path(doc["date"]), doc)

    def day_dates(self):
        return sorted(p.stem for p in self.path("daily").glob("*/*.json"))
