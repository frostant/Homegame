import streamlit as st
import pandas as pd
from pathlib import Path

st.set_page_config(
    page_title="Nova88 HomeGame 战绩记录",
    page_icon="🃏",
    layout="wide",
)

# === 路径配置（与 OCR / 导入脚本保持一致） ===
DATA_DIR = Path("data")
PLAYERS_CSV = DATA_DIR / "players.csv"
SESSIONS_CSV = DATA_DIR / "sessions.csv"
SESSION_PLAYERS_CSV = DATA_DIR / "session_players.csv"


# === 基础工具函数 ===

def ensure_data_dir():
    DATA_DIR.mkdir(exist_ok=True)


def load_or_create_csv(path: Path, columns):
    """读取 CSV，如果不存在则返回给定列的空 DataFrame。

    会保证返回的 DataFrame 至少包含 columns 中的字段，多余字段会保留在内存中，
    但下游逻辑只依赖这些核心字段。
    """
    if path.exists():
        df = pd.read_csv(path)
        # 补齐缺失列
        for col in columns:
            if col not in df.columns:
                df[col] = None
        return df
    else:
        return pd.DataFrame(columns=columns)


def save_df(df: pd.DataFrame, path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)


def get_next_id(df: pd.DataFrame, id_col: str) -> int:
    """获取下一个自增 ID（主要用于 players 表）。"""
    if df.empty or id_col not in df.columns:
        return 1
    try:
        return int(pd.to_numeric(df[id_col], errors="coerce").max()) + 1
    except Exception:
        return 1


# === 数据加载：统一到 OCR 导入后的 schema ===

def load_all_data():
    """加载 players / sessions / session_players 三张表。

    sessions.csv 期望字段（由 import_ocr_sessions.py 维护）：
        session_id, session_date, session_name, per_hand_amount,
        total_profit, is_balanced, raw_file

    session_players.csv 期望字段：
        session_id, player_id, alias, name, hands, amount, profit

    players.csv：
        player_id, name, note
    """
    ensure_data_dir()

    players = load_or_create_csv(
        PLAYERS_CSV, ["player_id", "name", "note"]
    )

    sessions = load_or_create_csv(
        SESSIONS_CSV,
        [
            "session_id",
            "session_date",
            "session_name",
            "per_hand_amount",
            "total_profit",
            "is_balanced",
            "raw_file",
        ],
    )

    session_players = load_or_create_csv(
        SESSION_PLAYERS_CSV,
        [
            "session_id",
            "player_id",
            "alias",
            "name",
            "hands",
            "amount",
            "profit",
        ],
    )

    # 类型规范化：玩家 ID
    if not players.empty:
        players["player_id"] = pd.to_numeric(
            players["player_id"], errors="coerce"
        ).astype("Int64")

    # sessions：ID & 数值列
    if not sessions.empty:
        sessions["session_id"] = pd.to_numeric(
            sessions["session_id"], errors="coerce"
        ).astype("Int64")
        sessions["per_hand_amount"] = pd.to_numeric(
            sessions["per_hand_amount"], errors="coerce"
        )
        sessions["total_profit"] = pd.to_numeric(
            sessions["total_profit"], errors="coerce"
        )
        # is_balanced 可能是 bool / 0/1 / 字符串，这里先不强转，展示时再处理

    # session_players：ID & 数值列
    if not session_players.empty:
        session_players["session_id"] = pd.to_numeric(
            session_players["session_id"], errors="coerce"
        ).astype("Int64")
        # player_id 可能有空（未识别归属），用 Int64 允许 NA
        session_players["player_id"] = pd.to_numeric(
            session_players["player_id"], errors="coerce"
        ).astype("Int64")
        for col in ["hands", "amount", "profit"]:
            session_players[col] = pd.to_numeric(
                session_players[col], errors="coerce"
            )

    return players, sessions, session_players


def save_all_data(players, sessions, session_players):
    """目前主要用于保存 players（玩家管理）。sessions 与 session_players 以导入脚本为准。"""
    save_df(players, PLAYERS_CSV)
    save_df(sessions, SESSIONS_CSV)
    save_df(session_players, SESSION_PLAYERS_CSV)


# === 初始化 session_state ===

if "players" not in st.session_state:
    p, s, sp = load_all_data()
    st.session_state.players = p
    st.session_state.sessions = s
    st.session_state.session_players = sp


# === 页面：总览 ===

def page_overview():
    st.title("🃏 Home Game 战绩总览（OCR 数据版）")

    players = st.session_state.players
    sessions = st.session_state.sessions
    session_players = st.session_state.session_players

    col1, col2, col3 = st.columns(3)
    col1.metric("玩家数量", len(players))
    col2.metric("牌局场次", len(sessions))
    total_profit = session_players["profit"].sum() if not session_players.empty else 0
    col3.metric("所有玩家盈亏总和", f"{total_profit:.0f}")

    st.markdown("---")

    st.subheader("玩家盈亏一览（Top 10，按 OCR 导入后的 profit 聚合）")

    if session_players.empty:
        st.info("暂无战绩数据，请先通过脚本导入 OCR 结果。")
        return

    # 使用 canonical name 列进行聚合
    summary = (
        session_players.groupby("name")["profit"]
        .sum()
        .reset_index()
        .sort_values("profit", ascending=False)
    )

    st.dataframe(summary.head(10), use_container_width=True)

    st.subheader("牌局列表（最近 10 场）")
    if not sessions.empty:
        # 尝试按日期排序
        sessions_display = sessions.copy()
        # 规范日期列
        if "session_date" in sessions_display.columns:
            sessions_display["session_date_parsed"] = pd.to_datetime(
                sessions_display["session_date"], errors="coerce"
            )
            sessions_display = sessions_display.sort_values(
                ["session_date_parsed", "session_id"], ascending=[False, False]
            )
        else:
            sessions_display = sessions_display.sort_values(
                "session_id", ascending=False
            )

        show_cols = [
            "session_id",
            "session_date",
            "session_name",
            "per_hand_amount",
            "total_profit",
            "is_balanced",
            "raw_file",
        ]
        show_cols = [c for c in show_cols if c in sessions_display.columns]

        st.dataframe(
            sessions_display[show_cols].head(10),
            use_container_width=True,
        )
    else:
        st.write("暂无牌局。")


# === 页面：玩家管理（仍然手动维护 players.csv） ===

def page_players():
    st.title("👥 玩家管理（真实身份 / 正式昵称）")

    players = st.session_state.players

    with st.form("add_player_form"):
        st.subheader("新增玩家")
        name = st.text_input("玩家昵称 *")
        note = st.text_input("备注（可选，例如微信名、打法风格）")
        submitted = st.form_submit_button("新增玩家")

        if submitted:
            if not name.strip():
                st.error("玩家昵称不能为空。")
            elif name in players.get("name", pd.Series([], dtype=str)).values:
                st.warning("已有同名玩家。")
            else:
                new_players = players.copy()
                new_id = get_next_id(new_players, "player_id")
                new_players.loc[len(new_players)] = {
                    "player_id": new_id,
                    "name": name.strip(),
                    "note": note.strip(),
                }
                st.session_state.players = new_players
                save_all_data(
                    st.session_state.players,
                    st.session_state.sessions,
                    st.session_state.session_players,
                )
                st.success(f"已新增玩家：{name}")

    st.markdown("---")

    st.subheader("玩家列表")
    if players.empty:
        st.info("暂无玩家，请先新增。")
    else:
        st.dataframe(players, use_container_width=True)


# === 页面：牌局列表 & 详情（基于 OCR 导入） ===

def page_sessions():
    st.title("🎲 牌局列表与详情（OCR 导入）")

    players = st.session_state.players
    sessions = st.session_state.sessions
    session_players = st.session_state.session_players

    if sessions.empty:
        st.info("暂无牌局数据，请先运行 OCR + 导入脚本生成 sessions.csv / session_players.csv。")
        return

    st.subheader("牌局列表")

    sessions_display = sessions.copy()
    if "session_date" in sessions_display.columns:
        sessions_display["session_date_parsed"] = pd.to_datetime(
            sessions_display["session_date"], errors="coerce"
        )
        sessions_display = sessions_display.sort_values(
            ["session_date_parsed", "session_id"], ascending=[False, False]
        )

    show_cols = [
        "session_id",
        "session_date",
        "session_name",
        "per_hand_amount",
        "total_profit",
        "is_balanced",
        "raw_file",
    ]
    show_cols = [c for c in show_cols if c in sessions_display.columns]

    st.dataframe(sessions_display[show_cols], use_container_width=True)

    st.markdown("---")

    st.subheader("查看某一场牌局详情")

    # 选择牌局
    label_map = {}
    for _, row in sessions_display.iterrows():
        sid = int(row["session_id"]) if pd.notna(row["session_id"]) else -1
        date_str = str(row.get("session_date", ""))
        name_str = str(row.get("session_name", ""))
        label = f"#{sid}  |  {date_str}  |  {name_str}"
        label_map[label] = sid

    if not label_map:
        st.info("当前没有可选牌局。")
        return

    selected_label = st.selectbox("选择牌局", options=list(label_map.keys()))
    selected_session_id = label_map[selected_label]

    # 当前牌局信息
    session_row = sessions_display[sessions_display["session_id"] == selected_session_id]
    if not session_row.empty:
        sr = session_row.iloc[0]
        per_hand = sr.get("per_hand_amount", None)
        total_profit = sr.get("total_profit", None)
        is_balanced = sr.get("is_balanced", None)

        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Session ID", selected_session_id)
        col2.metric("一手筹码量", f"{per_hand:.0f}" if pd.notna(per_hand) else "未知")
        col3.metric(
            "牌局总盈亏",
            f"{total_profit:.0f}" if pd.notna(total_profit) else "未知",
        )
        col4.metric("盈亏是否平衡", str(is_balanced))

    st.markdown("---")

    st.subheader("该场牌局的玩家明细")
    sp_current = session_players[
        session_players["session_id"] == selected_session_id
    ].copy()

    if sp_current.empty:
        st.write("该场牌局暂无玩家记录。")
    else:
        # 尝试补全玩家主表信息（如果 player_id 已经对齐）
        if not players.empty and "player_id" in sp_current.columns:
            sp_current = sp_current.merge(
                players[["player_id", "name"]].rename(
                    columns={"name": "player_name_official"}
                ),
                on="player_id",
                how="left",
            )

        # 计算当前局总盈亏
        sum_profit = sp_current["profit"].sum()
        st.caption(f"该场牌局玩家盈亏合计：{sum_profit:.0f}")

        show_cols = [
            "alias",  # 截图中的昵称
            "name",   # OCR 归一化后的名字（canonical / 或 alias）
            "player_name_official",  # players.csv 中的主昵称（若有）
            "hands",
            "amount",
            "profit",
        ]
        show_cols = [c for c in show_cols if c in sp_current.columns]

        st.dataframe(sp_current[show_cols], use_container_width=True)


# === 页面：玩家战绩查询（基于 OCR profit 聚合） ===

def page_player_stats():
    st.title("📊 玩家战绩查询（基于 OCR 导入数据）")

    players = st.session_state.players
    sessions = st.session_state.sessions
    session_players = st.session_state.session_players

    if players.empty or session_players.empty:
        st.info("暂无战绩数据或玩家信息。")
        return

    player_name = st.selectbox(
        "选择玩家（按 players.csv 中的正式昵称）",
        options=players["name"].tolist(),
    )

    player_row = players[players["name"] == player_name].iloc[0]
    player_id = int(player_row["player_id"])

    # 只看当前 player_id 的记录
    sp = session_players[session_players["player_id"] == player_id].copy()
    if sp.empty:
        st.info("该玩家暂无 OCR 导入战绩。")
        return

    merged = sp.merge(sessions, on="session_id", how="left")

    total_profit = sp["profit"].sum()
    max_win = sp["profit"].max()
    max_loss = sp["profit"].min()
    num_sessions = sp["session_id"].nunique()
    total_hands = sp["hands"].sum()

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("总场次", int(num_sessions))
    col2.metric("总手数", f"{total_hands:.0f}")
    col3.metric("总盈亏", f"{total_profit:.0f}")
    avg_per_session = total_profit / num_sessions if num_sessions > 0 else 0
    col4.metric("场均盈亏", f"{avg_per_session:.1f}")

    col5, col6 = st.columns(2)
    col5.metric("最大单场赢", f"{max_win:.0f}")
    col6.metric("最大单场输", f"{max_loss:.0f}")

    st.markdown("---")

    st.subheader("每场战绩列表")
    merged_sorted = merged.copy()
    if "session_date" in merged_sorted.columns:
        merged_sorted["session_date_parsed"] = pd.to_datetime(
            merged_sorted["session_date"], errors="coerce"
        )
        merged_sorted = merged_sorted.sort_values("session_date_parsed")

    show_cols = [
        "session_date",
        "session_name",
        "per_hand_amount",
        "hands",
        "amount",
        "profit",
    ]
    show_cols = [c for c in show_cols if c in merged_sorted.columns]

    st.dataframe(merged_sorted[show_cols], use_container_width=True)

    st.subheader("累计盈亏曲线")
    if not merged_sorted.empty and "session_date" in merged_sorted.columns:
        merged_sorted["session_date_parsed"] = pd.to_datetime(
            merged_sorted["session_date"], errors="coerce"
        )
        merged_sorted = merged_sorted.sort_values("session_date_parsed")
        merged_sorted["cum_profit"] = merged_sorted["profit"].cumsum()
        chart_data = merged_sorted[["session_date_parsed", "cum_profit"]].set_index(
            "session_date_parsed"
        )
        st.line_chart(chart_data)


# === 主入口 ===

def main():
    st.sidebar.title("导航")
    page = st.sidebar.radio(
        "选择页面",
        ("总览", "玩家管理", "牌局录入 / 列表", "玩家战绩查询"),
    )

    if page == "总览":
        page_overview()
    elif page == "玩家管理":
        page_players()
    elif page == "牌局录入 / 列表":
        page_sessions()
    elif page == "玩家战绩查询":
        page_player_stats()


if __name__ == "__main__":
    main()
