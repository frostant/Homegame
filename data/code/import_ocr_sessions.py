import csv
import json
from pathlib import Path
from typing import Dict, List, Any, Optional, Set

# ===== 路径配置 =====
# 当前文件在 data/code/ 下，所以项目根目录是 parents[2]
PROJECT_ROOT = Path(__file__).resolve().parents[2]  # poker_tracker/
DATA_DIR = PROJECT_ROOT / "data"
OCR_DIR = DATA_DIR / "ocr_data"

PLAYERS_CSV = DATA_DIR / "players.csv"
ALIAS_MAP_CSV = DATA_DIR / "alias_map.csv"
SESSIONS_CSV = DATA_DIR / "sessions.csv"
SESSION_PLAYERS_CSV = DATA_DIR / "session_players.csv"

# 统一使用的基准一手筹码量（例如：200 = 200 面值的一手）
BASE_PER_HAND_AMOUNT = 200

# 为特殊牌局配置倍率：
# 键为 ocr_data 下的 json 文件名，值为倍率。
# 例如某局截图中 per_hand_amount=1000，但真实应视为 200，
# 则有 1000 / 200 = 5，可以配置：
#   "20251025.json": 5
# 脚本会自动对该局所有 amount / profit 除以 5，并将 per_hand_amount 归一化为 200。
SESSION_MULTIPLIERS: Dict[str, int] = {
    # "20251025.json": 5,
}


# ===== 工具函数 =====

def load_players() -> Dict[int, Dict[str, Any]]:
    """读取 players.csv，返回 {player_id: row}."""
    players: Dict[int, Dict[str, Any]] = {}
    if not PLAYERS_CSV.exists():
        print(f"⚠️ players.csv 不存在：{PLAYERS_CSV}")
        return players

    with PLAYERS_CSV.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                pid = int(row["player_id"])
            except Exception:
                continue
            players[pid] = row
    print(f"✅ 读取 players.csv：{len(players)} 个玩家")
    return players


def load_alias_map() -> Dict[str, int]:
    """读取 alias_map.csv，返回 {规范化alias: player_id}."""
    alias_to_pid: Dict[str, int] = {}
    if not ALIAS_MAP_CSV.exists():
        print(f"⚠️ alias_map.csv 不存在：{ALIAS_MAP_CSV}")
        return alias_to_pid

    with ALIAS_MAP_CSV.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            alias = str(row.get("alias", "")).strip()
            if not alias:
                continue
            try:
                pid = int(row["player_id"])
            except Exception:
                continue
            key = alias.strip().lower()
            alias_to_pid[key] = pid
    print(f"✅ 读取 alias_map.csv：{len(alias_to_pid)} 个 alias 映射")
    return alias_to_pid


def normalize_name(name: str) -> str:
    """昵称归一化（用于查 alias_map 的 key）."""
    return name.strip().lower()


def resolve_player_id_from_alias(name: str, alias_to_pid: Dict[str, int]) -> Optional[int]:
    """根据昵称在 alias_map 中找到 player_id."""
    key = normalize_name(name)
    return alias_to_pid.get(key)


def load_existing_sessions() -> (List[Dict[str, Any]], Set[str], int):
    """
    读取 sessions.csv，返回：
    - sessions: 已有 session 行列表
    - imported_files: 已经导入过的 raw_file 集合（防止重复导入同一 json）
    - max_session_id: 当前最大 session_id，后续新增从此基础上 +1
    """
    sessions: List[Dict[str, Any]] = []
    imported_files: Set[str] = set()
    max_session_id = 0

    if not SESSIONS_CSV.exists():
        print(f"ℹ️ sessions.csv 不存在，将创建新文件：{SESSIONS_CSV}")
        return sessions, imported_files, max_session_id

    with SESSIONS_CSV.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            sessions.append(row)
            # 记录已导入的 raw_file
            raw_file = str(row.get("raw_file", "")).strip()
            if raw_file:
                imported_files.add(raw_file)
            # 更新 max_session_id
            try:
                sid = int(row.get("session_id", 0))
                if sid > max_session_id:
                    max_session_id = sid
            except Exception:
                continue

    print(f"✅ 读取 sessions.csv：{len(sessions)} 场牌局，最大 session_id = {max_session_id}")
    return sessions, imported_files, max_session_id


def load_existing_session_players() -> List[Dict[str, Any]]:
    """读取 session_players.csv，返回已有记录列表。"""
    records: List[Dict[str, Any]] = []
    if not SESSION_PLAYERS_CSV.exists():
        print(f"ℹ️ session_players.csv 不存在，将创建新文件：{SESSION_PLAYERS_CSV}")
        return records

    with SESSION_PLAYERS_CSV.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            records.append(row)

    print(f"✅ 读取 session_players.csv：{len(records)} 条记录")
    return records


def parse_session_date_from_stem(stem: str) -> str:
    """
    从文件名 stem 中解析日期，简单规则：
    - 如果是 8 位纯数字，例如 20250802 -> 2025-08-02
    - 否则返回空字符串，由你之后手动填/编辑
    """
    s = stem
    # 去掉可能的后缀，例如 20250920_2 -> 20250920
    if "_" in s:
        s = s.split("_", 1)[0]
    if len(s) == 8 and s.isdigit():
        return f"{s[0:4]}-{s[4:6]}-{s[6:8]}"
    return ""


def normalize_per_hand_amount(value: Any, raw_file: str, rows: List[Dict[str, Any]]) -> int:
    """校验并归一化 per_hand_amount 到 BASE_PER_HAND_AMOUNT，并按倍率缩放筹码量。

    规则：
    - 如果 value 为空或无法转为整数，直接抛错，由人工检查。
    - 如果值等于 BASE_PER_HAND_AMOUNT，则不做缩放，原样返回（假设本局就是标准 200 一手）。
    - 如果值不等于 BASE_PER_HAND_AMOUNT：
        * 必须在 SESSION_MULTIPLIERS 中为该 raw_file 配置倍率 multiplier；
        * 要求 value / multiplier == BASE_PER_HAND_AMOUNT；
        * 对本局所有行的 amount / profit 整体除以 multiplier；
        * 如果某个 amount/profit 不能整除 multiplier，则抛错，由人工检查。
    """
    if value is None:
        raise ValueError(f"文件 {raw_file} 中 per_hand_amount 缺失，请人工检查该 JSON 并修正后再导入")

    try:
        amt = int(value)
    except Exception:
        raise ValueError(f"文件 {raw_file} 中 per_hand_amount={value!r} 无法转换为整数，请人工检查")

    # 已经是基准筹码量，直接返回
    if amt == BASE_PER_HAND_AMOUNT:
        return BASE_PER_HAND_AMOUNT

    # 自动倍率逻辑
    # 200 / 300 → rate = 1
    # 1000 → rate = 5
    auto_multiplier = None
    if amt in (200, 300):
        auto_multiplier = 1
    elif amt == 1000:
        auto_multiplier = 5

    if auto_multiplier is not None:
        multiplier = auto_multiplier
        print(f"  ⚠️ 自动识别到 per_hand_amount={amt} → 使用倍率 {multiplier}")
    else:
        # 原有 SESSION_MULTIPLIERS 查询
        multiplier = SESSION_MULTIPLIERS.get(raw_file)

    # 如果自动和 SESSION_MULTIPLIERS 都没有结果，则保持旧的交互逻辑
    while multiplier is None:
        print(
            f"文件 {raw_file} 中 per_hand_amount={amt} 无法自动识别倍率。\n"
            f"请手动输入 multiplier，使得 {amt} / multiplier = {BASE_PER_HAND_AMOUNT}。"
        )
        user_input = input(f"👉 请输入牌局 {raw_file} 的倍率 multiplier：").strip()
        if user_input.lower() in {"q", "quit", "skip"}:
            raise ValueError(f"用户选择跳过牌局 {raw_file} 的导入。")

        try:
            multiplier = int(user_input)
        except Exception:
            print("⚠️ 非法输入，请输入正整数。\n")
            multiplier = None
            continue

        if multiplier <= 0:
            print("⚠️ 倍率必须为正整数。\n")
            multiplier = None
            continue

        SESSION_MULTIPLIERS[raw_file] = multiplier

    # 校验倍率是否匹配
    # if amt % multiplier != 0 or amt // multiplier != BASE_PER_HAND_AMOUNT:
    #     raise ValueError(
    #         f"文件 {raw_file} 中 per_hand_amount={amt} 与倍率 multiplier={multiplier} 不匹配："
    #         f"{amt} / {multiplier} != {BASE_PER_HAND_AMOUNT}"
    #     )

    # 定义一个安全的除法工具，用于缩放 amount / profit
    def scaled_int(value_any: Any, field_name: str) -> int:
        if value_any is None or value_any == "":
            return 0
        try:
            v = int(value_any)
        except Exception:
            raise ValueError(
                f"文件 {raw_file} 中字段 {field_name}={value_any!r} 无法转换为整数，"
                f"无法按倍率 {multiplier} 进行缩放，请人工检查。"
            )
        if v % multiplier != 0:
            raise ValueError(
                f"文件 {raw_file} 中字段 {field_name}={v} 不能被倍率 {multiplier} 整除，"
                f"请确认该局倍率设置是否正确或手动修正数值。"
            )
        return v // multiplier

    # 对本局所有行的 amount / profit 做缩放
    for r in rows:
        if not isinstance(r, dict):
            continue
        if "amount" in r:
            r["amount"] = scaled_int(r.get("amount"), "amount")
        if "profit" in r:
            r["profit"] = scaled_int(r.get("profit"), "profit")

    print(
        f"  ⚠️ 文件 {raw_file} 中 per_hand_amount={amt} 使用倍率 {multiplier} 归一化为 {BASE_PER_HAND_AMOUNT}，"
        f"所有 amount/profit 已除以 {multiplier} 后登记"
    )

    return BASE_PER_HAND_AMOUNT


# ===== 写回 CSV 的函数 =====

def save_sessions(sessions: List[Dict[str, Any]]) -> None:
    """写回 sessions.csv，自动合并所有字段。"""
    if not sessions:
        print("⚠️ 没有任何 sessions 记录，不写入 sessions.csv")
        return

    # 收集所有字段名（防止之前旧文件字段不一致）
    fieldnames_set = set()
    for row in sessions:
        fieldnames_set.update(row.keys())
    # 确保这些核心字段存在
    core_fields = [
        "session_id",
        "session_date",
        "session_name",
        "per_hand_amount",
        "total_profit",
        "is_balanced",
        "raw_file",
    ]
    for f in core_fields:
        fieldnames_set.add(f)

    fieldnames = list(fieldnames_set)

    with SESSIONS_CSV.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in sessions:
            writer.writerow(row)

    print(f"✅ 已写回 sessions.csv：{SESSIONS_CSV}（共 {len(sessions)} 行）")


def save_session_players(records: List[Dict[str, Any]]) -> None:
    """写回 session_players.csv."""
    if not records:
        print("⚠️ 没有任何 session_players 记录，不写入 session_players.csv")
        return

    fieldnames_set = set()
    for row in records:
        fieldnames_set.update(row.keys())
    core_fields = [
        "session_id",
        "player_id",
        "alias",
        "name",
        "hands",
        "amount",
        "profit",
    ]
    for f in core_fields:
        fieldnames_set.add(f)

    fieldnames = list(fieldnames_set)

    with SESSION_PLAYERS_CSV.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in records:
            writer.writerow(row)

    print(f"✅ 已写回 session_players.csv：{SESSION_PLAYERS_CSV}（共 {len(records)} 条）")


# ===== 主流程 =====

def main():
    print("=== 从 OCR JSON 导入牌局到 sessions.csv & session_players.csv ===\n")

    players = load_players()
    alias_to_pid = load_alias_map()
    sessions, imported_files, max_session_id = load_existing_sessions()
    session_players = load_existing_session_players()

    # 为了查 player 别名：player_id -> canonical name
    player_id_to_name = {
        pid: p.get("name", "")
        for pid, p in players.items()
    }

    if not OCR_DIR.exists():
        print(f"❌ OCR 结果目录不存在：{OCR_DIR}")
        return

    json_files = sorted(OCR_DIR.glob("*.json"))
    if not json_files:
        print(f"⚠️ {OCR_DIR} 下没有 json 文件可导入。")
        return

    print(f"🔍 在 {OCR_DIR} 下发现 {len(json_files)} 个 JSON 文件。\n")

    new_sessions_count = 0
    new_records_count = 0

    for jf in json_files:
        raw_file_name = jf.name
        if raw_file_name in imported_files:
            print(f"⏭ 已导入过的牌局，跳过：{raw_file_name}")
            continue

        print(f"➡️ 处理牌局文件：{raw_file_name}")

        try:
            with jf.open("r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            print(f"  ⚠️ 读取 JSON 失败，跳过：{e}")
            continue

        rows = data.get("rows") or []
        validation = data.get("validation") or {}

        per_hand_amount_raw = data.get("per_hand_amount")
        per_hand_amount = normalize_per_hand_amount(per_hand_amount_raw, raw_file_name, rows)

        total_profit = validation.get("total_profit")
        is_balanced = validation.get("is_balanced")

        # 新建 session_id
        max_session_id += 1
        session_id = max_session_id

        # 从文件名推 session_date / session_name
        stem = jf.stem  # 例如 20250802 或 20250920_2
        session_date = parse_session_date_from_stem(stem)
        session_name = stem

        sessions.append({
            "session_id": str(session_id),
            "session_date": session_date,
            "session_name": session_name,
            "per_hand_amount": per_hand_amount,
            "total_profit": total_profit,
            "is_balanced": is_balanced,
            "raw_file": raw_file_name,
        })
        imported_files.add(raw_file_name)
        new_sessions_count += 1

        # 逐行写入 session_players
        for r in rows:
            alias = str(r.get("name", "")).strip()
            if not alias:
                continue

            pid = resolve_player_id_from_alias(alias, alias_to_pid)
            pid_str = str(pid) if pid is not None else ""

            canonical_name = alias
            if pid is not None:
                # 如果 players 里有这个 player_id，用主名显示
                canonical_name = player_id_to_name.get(pid, alias)

            hands = r.get("hands")
            amount = r.get("amount")
            profit = r.get("profit")

            record = {
                "session_id": str(session_id),
                "player_id": pid_str,
                "alias": alias,
                "name": canonical_name,
                "hands": hands,
                "amount": amount,
                "profit": profit,
            }
            session_players.append(record)
            new_records_count += 1

        print(f"  ✅ 已导入该牌局：session_id={session_id}，共 {len(rows)} 位玩家。")

    # 全部 JSON 处理后，写回 CSV
    save_sessions(sessions)
    save_session_players(session_players)

    print(f"\n🎉 导入完成：新增 {new_sessions_count} 场牌局，新增 {new_records_count} 条玩家记录。")


if __name__ == "__main__":
    main()