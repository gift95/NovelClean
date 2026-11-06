"""让 `pytest` 与 `python -m pytest` 都能从仓库根导入 cleaner/app。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
