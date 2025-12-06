import json
import csv
from pathlib import Path
from typing import Dict, List, Set, Tuple, Optional
import difflib


# ===== 路径配置 =====
PROJECT_ROOT = Path(__file__).resolve().parents[2]  # poker_tracker/
DATA_DIR = PROJECT_ROOT / "data"
OCR_DIR = DATA_DIR / "ocr_data"

PLAYERS_CSV = DATA_DIR / "players.csv"
ALIAS_MAP_CSV = DATA_DIR / "alias_map.csv"


# ===== 工具函数 =====

def load_players() -> List[Dict]:
    """读取 players.csv，返回玩家列表。要求至少包含 player_id,name 两列。"""
    players: List[Dict] = []
    if not PLAYERS_CSV.exists():
        print(f"⚠️ players.csv 不存在，将从空列表开始：{PLAYERS_CSV}")
        return players

    with PLAYERS_CSV.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            # 确保 player_id 为 int
            try:
                row["player_id"] = int(row["player_id"])
            except Exception:
                continue
            players.append(row)
    return players


def save_players(players: List[Dict]) -> None:
    """写回 players.csv，保留常见列字段。"""
    if not players:
        print("⚠️ 没有任何玩家记录，players.csv 不会被写入。")
        return

    # 统一列名（如果没有 note 字段，补一个）
    fieldnames = ["player_id", "name", "note"]
    for p in players:
        if "note" not in p:
            p["note"] = ""

    with PLAYERS_CSV.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for p in players:
            writer.writerow({
                "player_id": p["player_id"],
                "name": p.get("name", ""),
                "note": p.get("note", ""),
            })
    print(f"✅ 已写回 players.csv：{PLAYERS_CSV}")


def load_alias_map() -> List[Dict]:
    """读取 alias_map.csv，返回 alias 列表。"""
    aliases: List[Dict] = []
    if not ALIAS_MAP_CSV.exists():
        print(f"⚠️ alias_map.csv 不存在，将从空列表开始：{ALIAS_MAP_CSV}")
        return aliases

    with ALIAS_MAP_CSV.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                row["player_id"] = int(row["player_id"])
            except Exception:
                continue
            # is_primary 转成 int，默认 0
            try:
                row["is_primary"] = int(row.get("is_primary", 0))
            except Exception:
                row["is_primary"] = 0
            aliases.append(row)
    return aliases


def save_alias_map(aliases: List[Dict]) -> None:
    """写回 alias_map.csv。"""
    if not aliases:
        print("⚠️ 没有任何 alias 记录，alias_map.csv 不会被写入。")
        return

    fieldnames = ["alias", "player_id", "is_primary"]
    with ALIAS_MAP_CSV.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for a in aliases:
            writer.writerow({
                "alias": a["alias"],
                "player_id": a["player_id"],
                "is_primary": a.get("is_primary", 0),
            })
    print(f"✅ 已写回 alias_map.csv：{ALIAS_MAP_CSV}")


def load_all_names_from_ocr() -> Set[str]:
    """扫描 ocr_data 下所有 json，提取 rows[].name 集合。"""
    names: Set[str] = set()

    if not OCR_DIR.exists():
        print(f"⚠️ OCR 结果目录不存在：{OCR_DIR}")
        return names

    json_files = sorted(OCR_DIR.glob("*.json"))
    if not json_files:
        print(f"⚠️ {OCR_DIR} 下没有 json 文件。")
        return names

    print(f"🔍 在 {OCR_DIR} 下找到 {len(json_files)} 个 JSON 文件，开始收集昵称...")
    for jf in json_files:
        try:
            with jf.open("r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            print(f"  ⚠️ 读取 {jf.name} 失败：{e}")
            continue

        rows = data.get("rows") or []
        if not isinstance(rows, list):
            continue

        for r in rows:
            if not isinstance(r, dict):
                continue
            name = str(r.get("name", "")).strip()
            if name:
                names.add(name)

    print(f"✅ 共收集到 {len(names)} 个不同昵称（包含已映射与未映射）")
    return names


# ===== 相似度推荐逻辑 =====

def build_player_label_index(players: List[Dict], alias_list: List[Dict]) -> Dict[int, List[str]]:
    """
    为每个 player_id 构建一个 label 列表，包含：
    - players.csv 内的 name
    - alias_map.csv 内的 alias
    用于后续做字符串相似度匹配。
    """
    pid_to_labels: Dict[int, List[str]] = {}

    for p in players:
        pid = p["player_id"]
        labels = pid_to_labels.setdefault(pid, [])
        name = str(p.get("name", "")).strip()
        if name:
            labels.append(name)

    for a in alias_list:
        pid = a["player_id"]
        labels = pid_to_labels.setdefault(pid, [])
        alias = str(a.get("alias", "")).strip()
        if alias and alias not in labels:
            labels.append(alias)

    return pid_to_labels


def suggest_player_id_for_alias(alias: str, pid_to_labels: Dict[int, List[str]]) -> Optional[int]:
    """
    使用字符串相似度在所有玩家标签中为 alias 推荐一个最可能的 player_id。
    返回 player_id 或 None。
    """
    alias_norm = alias.strip().lower()
    if not alias_norm:
        return None

    best_pid = None
    best_score = 0.0

    for pid, labels in pid_to_labels.items():
        for label in labels:
            label_norm = str(label).strip().lower()
            if not label_norm:
                continue
            score = difflib.SequenceMatcher(a=alias_norm, b=label_norm).ratio()
            if score > best_score:
                best_score = score
                best_pid = pid

    # 你可以根据需要调整这个阈值
    if best_score >= 0.6:
        return best_pid
    return None


# ===== 主流程 =====

def main():
    print("=== 德扑昵称归一化 / alias 映射管理工具 ===\n")

    players = load_players()
    aliases = load_alias_map()
    all_names = load_all_names_from_ocr()

    # 已经在 alias_map 中的别名（不重复处理）
    existing_aliases = {str(a["alias"]).strip() for a in aliases}

    # 需要处理的新的昵称集合
    new_aliases = sorted(name for name in all_names if name not in existing_aliases)

    if not new_aliases:
        print("🎉 没有新的昵称需要处理，alias_map 已经覆盖所有 OCR 名字。")
        return

    print(f"🆕 本次共有 {len(new_aliases)} 个新昵称需要归一化处理。\n")

    # 构建 player_id -> labels（用于推荐）
    pid_to_labels = build_player_label_index(players, aliases)

    # 为了方便选择，打印现有玩家列表
    def print_players_brief():
        print("\n当前已有玩家列表：")
        for p in sorted(players, key=lambda x: x["player_id"]):
            print(f"  [player_id={p['player_id']}] {p.get('name', '')}")
        print()

    print_players_brief()

    # 计算当前最大 player_id，后面新建玩家时递增
    max_pid = max([p["player_id"] for p in players], default=0)

    for alias in new_aliases:
        print("=" * 60)
        print(f"要处理的昵称：{alias}")

        # 推荐一个 player_id
        suggested_pid = suggest_player_id_for_alias(alias, pid_to_labels)
        if suggested_pid is not None:
            suggested_player = next((p for p in players if p["player_id"] == suggested_pid), None)
            if suggested_player:
                print(f"🤖 推荐归属玩家：[{suggested_pid}] {suggested_player.get('name', '')}")
        else:
            print("🤖 暂无明显推荐玩家（相似度不足）")

        print("\n你可以选择：")
        print("  1. 直接输入一个已有 player_id，将该昵称归到该玩家名下")
        print("  2. 按回车(Enter) 接受推荐（如果有推荐）")
        print("  3. 输入 0 表示创建一个新玩家，并将该昵称作为该玩家的主名/别名")
        print("  4. 输入 s 或 skip 跳过该昵称（本次不写入映射）")

        # 显示一遍现有玩家列表，方便查看 id
        print_players_brief()

        user_input = input(f"请为昵称 \"{alias}\" 选择 player_id（回车=接受推荐）：").strip()

        if user_input.lower() in {"s", "skip"}:
            print("⏭ 跳过该昵称，本次不处理。\n")
            continue

        if user_input == "":
            # 回车：接受推荐
            if suggested_pid is None:
                print("⚠️ 当前没有推荐玩家，请手动输入 player_id 或 0 创建新玩家。\n")
                user_input = input(f"请为昵称 \"{alias}\" 手动输入 player_id（0=新建）：").strip()
            else:
                chosen_pid = suggested_pid
        else:
            # 用户有输入
            try:
                chosen_pid = int(user_input)
            except ValueError:
                print("⚠️ 输入非法，跳过该昵称。\n")
                continue

        # 处理用户选择
        if isinstance(chosen_pid, int) and chosen_pid == 0:
            # 新建玩家
            max_pid += 1
            new_pid = max_pid
            print(f"🆕 准备创建新玩家，player_id = {new_pid}")
            default_name = alias
            new_name = input(f"请输入该玩家的主名称（回车使用别名 \"{alias}\"）：").strip()
            if not new_name:
                new_name = default_name

            # 添加到 players 列表
            players.append({
                "player_id": new_pid,
                "name": new_name,
                "note": "",
            })

            # 更新索引
            pid_to_labels.setdefault(new_pid, []).append(new_name)

            # 把当前 alias 也写入 alias_map，标记 is_primary=0（主名在 players.csv 里）
            aliases.append({
                "alias": alias,
                "player_id": new_pid,
                "is_primary": 0,
            })
            pid_to_labels[new_pid].append(alias)

            print(f"✅ 已创建新玩家 [{new_pid}] {new_name}，并添加 alias：{alias}\n")
        elif isinstance(chosen_pid, int):
            # 归到已有玩家
            target_player = next((p for p in players if p["player_id"] == chosen_pid), None)
            if not target_player:
                print(f"⚠️ 未找到 player_id={chosen_pid} 对应的玩家，跳过该昵称。\n")
                continue

            aliases.append({
                "alias": alias,
                "player_id": chosen_pid,
                "is_primary": 0,
            })
            pid_to_labels.setdefault(chosen_pid, []).append(alias)
            print(f"✅ 已将昵称 \"{alias}\" 归属到玩家 [{chosen_pid}] {target_player.get('name', '')}\n")
        else:
            print("⚠️ 未能解析选择，跳过该昵称。\n")

    # 所有新昵称处理完毕，写回两个 CSV
    save_players(players)
    save_alias_map(aliases)
    print("\n🎉 所有新昵称处理流程结束。")


if __name__ == "__main__":
    main()