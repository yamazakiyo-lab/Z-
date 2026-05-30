"""organizer.py の _remove_B4_and_empty_subdirs バグ修正スクリプト"""

TARGET = r'C:\Users\Yamazakiyo\tseg_vscode\Zフォルダ整理\91GDX・252WORKNO-program\organizer.py'

with open(TARGET, encoding='utf-8') as f:
    lines = f.readlines()

print(f"総行数: {len(lines)}")

# --- 挿入するクラスメソッド ---
new_method_lines = [
    '\n',
    '    def _remove_B4_and_empty_subdirs(self, b4: Path):\n',
    '        """B4配下の空サブフォルダを削除し、B4自体も空なら削除する。"""\n',
    '        try:\n',
    '            for sub in list(b4.iterdir()):\n',
    '                if sub.is_dir():\n',
    '                    items = list(sub.iterdir())\n',
    '                    if not items:\n',
    '                        self._remove_dir_if_empty(sub)\n',
    '        except Exception as e:\n',
    '            self.log.warn(f"B4配下サブフォルダ空判定失敗: {b4} ({e})")\n',
    '        self._remove_dir_if_empty(b4)\n',
]

# 各インデックスを特定
insert_after = None   # _cleanup_empty_B_folders の末尾行
comment_line = None   # モジバケコメント行
call_line = None      # self._remove_B4_and_empty_subdirs(b4) 呼び出し行
remove_start = None   # 誤ったネスト def の開始行
remove_end = None     # 誤ったネスト def の終了行

for i, line in enumerate(lines):
    if '            self._remove_dir_if_empty(b)' in line:
        insert_after = i
    if 'self._remove_B4_and_empty_subdirs(b4)' in line and 'def ' not in line:
        call_line = i
        comment_line = i - 1
    if '            def _remove_B4_and_empty_subdirs' in line:
        remove_start = i
    if remove_start is not None and i > remove_start and '                self._remove_dir_if_empty(b4)' in line:
        remove_end = i
        break

print(f"insert_after : {insert_after} (行{insert_after+1}): {repr(lines[insert_after].rstrip())}")
print(f"comment_line : {comment_line} (行{comment_line+1}): {repr(lines[comment_line].rstrip())}")
print(f"call_line    : {call_line} (行{call_line+1}): {repr(lines[call_line].rstrip())}")
print(f"remove_start : {remove_start} (行{remove_start+1}): {repr(lines[remove_start].rstrip())}")
print(f"remove_end   : {remove_end} (行{remove_end+1}): {repr(lines[remove_end].rstrip())}")

assert insert_after is not None, "insert_after が見つかりません"
assert comment_line is not None, "comment_line が見つかりません"
assert call_line is not None, "call_line が見つかりません"
assert remove_start is not None, "remove_start が見つかりません"
assert remove_end is not None, "remove_end が見つかりません"
assert remove_start == call_line + 1, f"remove_start({remove_start}) は call_line+1({call_line+1}) のはず"

# --- 修正実施（後ろから処理してインデックスずれを防ぐ）---
new_lines = list(lines)

# Step1: 誤ったネスト定義（remove_start〜remove_end）を削除
del new_lines[remove_start:remove_end + 1]
print(f"\nStep1: 行{remove_start+1}〜{remove_end+1} のネスト定義を削除 ({remove_end - remove_start + 1}行)")

# Step2: モジバケコメントを正しい日本語に修正
new_lines[comment_line] = '        # B4配下の空サブフォルダも削除し、B4自体も再度空判定して削除\n'
print(f"Step2: 行{comment_line+1} のコメントを修正")

# Step3: _cleanup_empty_B_folders の直後に新クラスメソッドを挿入
new_lines[insert_after + 1:insert_after + 1] = new_method_lines
print(f"Step3: 行{insert_after+2} に _remove_B4_and_empty_subdirs クラスメソッドを挿入 ({len(new_method_lines)}行)")

print(f"\n修正後総行数: {len(new_lines)}")

# バックアップ保存
backup = TARGET + '.bak'
with open(backup, 'w', encoding='utf-8') as f:
    f.writelines(lines)
print(f"バックアップ: {backup}")

# 本体を上書き
with open(TARGET, 'w', encoding='utf-8') as f:
    f.writelines(new_lines)
print("修正完了: organizer.py を上書きしました")

# 構文チェック
import py_compile, sys
try:
    py_compile.compile(TARGET, doraise=True)
    print("構文チェック: OK")
except py_compile.PyCompileError as e:
    print(f"構文チェック: NG → {e}", file=sys.stderr)
