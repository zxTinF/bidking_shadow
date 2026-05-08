# -*- coding: utf-8 -*-
"""
Round-level recorder for BidKing logs.

Exports structured per-round snapshots of inferred grid layout, and
final revealed layout at game end (S2C_45).
"""

import copy
import io
import json
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .handlers import handle_s2c33, handle_s2c37, handle_s2c39, handle_s2c45
from .item_db import load_csv, query_item
from .log_parser import extract_event, iter_log_lines
from .models import CsvItem, GameState, ItemKnowledge


GAME_UID_EPOCH_UTC = datetime(2000, 1, 1, tzinfo=timezone.utc)


def _shape_wh(shape: Optional[int]) -> Tuple[Optional[int], Optional[int]]:
    if shape is None:
        return None, None
    s = str(shape)
    if len(s) == 2 and s.isdigit():
        return int(s[0]), int(s[1])
    return None, None


def _item_sort_key(uid: str, k: ItemKnowledge) -> Tuple[int, int, str]:
    if k.box_id is None:
        return (1, 10**9, uid)
    return (0, k.box_id, uid)


def _snapshot_grid(
    state: GameState,
    csv_index: Dict[int, CsvItem],
    csv_items: List[CsvItem],
) -> Dict[str, Any]:
    items: List[Dict[str, Any]] = []
    known_cells = 0

    sorted_items = sorted(
        state.items.items(),
        key=lambda pair: _item_sort_key(pair[0], pair[1]),
    )

    for uid, k in sorted_items:
        w, h = _shape_wh(k.shape)
        cells = (w * h) if (w is not None and h is not None) else None
        if cells is not None:
            known_cells += cells
        row = (k.box_id // 10) if k.box_id is not None else None
        col = (k.box_id % 10) if k.box_id is not None else None

        best, cand_count, unique, est_price, est_label = query_item(
            k.shape,
            k.quality,
            k.categories,
            k.item_cid,
            csv_index,
            csv_items,
            k.excluded_categories,
            k.excluded_qualities,
        )

        inferred_best = None
        if best is not None:
            inferred_best = {
                "item_id": best.item_id,
                "name": best.name,
                "quality": best.quality,
                "shape": best.shape,
                "base_value": best.base_value,
            }

        item_name = csv_index[k.item_cid].name if k.item_cid in csv_index else None
        items.append(
            {
                "uid": uid,
                "box_id": k.box_id,
                "row": row,
                "col": col,
                "box_id_confirmed": k.box_id_confirmed,
                "shape": k.shape,
                "width": w,
                "height": h,
                "cells": cells,
                "quality": k.quality,
                "categories": sorted(k.categories),
                "excluded_categories": sorted(k.excluded_categories),
                "excluded_qualities": sorted(k.excluded_qualities),
                "item_cid": k.item_cid,
                "item_name": item_name,
                "price": k.price,
                "inferred_candidate_count": cand_count,
                "inferred_unique": unique,
                "inferred_best": inferred_best,
                "inferred_est_price": est_price,
                "inferred_est_label": est_label,
            }
        )

    return {
        "map_id": state.map_id,
        "round": state.current_round,
        "item_count": len(items),
        "known_shape_cells": known_cells,
        "items": items,
    }


def _looks_like_box(d: Dict[str, Any]) -> bool:
    keys = {
        "ItemUid", "BoxId", "ItemSlotType", "ItemQuility",
        "ItemCid", "ItemPrice", "ItemType",
        # Nested box schema in Player.log S2C_45:
        # { "Position": {...}, "Item": {...} } (BoxId/Position fields may omit zero values)
        "Position", "Item",
    }
    return any(k in d for k in keys)


def _iter_box_dicts(obj: Any) -> Iterable[Dict[str, Any]]:
    if isinstance(obj, dict):
        if _looks_like_box(obj):
            yield obj
        for v in obj.values():
            yield from _iter_box_dicts(v)
    elif isinstance(obj, list):
        for it in obj:
            yield from _iter_box_dicts(it)


def _extract_true_layout(data: Dict[str, Any], csv_index: Dict[int, CsvItem]) -> Dict[str, Any]:
    gd = data.get("GameData", {})
    sc = gd.get("StockContainer", {})
    true_items: List[Dict[str, Any]] = []

    for box in _iter_box_dicts(sc):
        # Compatible with both schemas:
        # 1) flat: ItemUid / ItemCid / ItemSlotType / ItemQuility
        # 2) nested: Item.{Uid,Cid,Quality,BoxPositionData}
        item = box.get("Item", {})
        if not isinstance(item, dict):
            item = {}

        uid = box.get("ItemUid") or item.get("Uid")
        box_id = box.get("BoxId")
        shape = box.get("ItemSlotType")
        quality = box.get("ItemQuility", box.get("Quality"))
        if quality is None:
            quality = item.get("Quality")
        item_cid = box.get("ItemCid")
        if item_cid is None:
            item_cid = item.get("Cid")
        item_price = box.get("ItemPrice")
        item_types = box.get("ItemType", [])

        # Empty slots have no item info; skip to keep final reveal concise/accurate.
        if uid in (None, "") and item_cid is None:
            continue

        # Some zero-values are omitted in logs (e.g. BoxId=0, X=0, Y=0),
        # so infer box_id from Position / BoxPositionData when missing.
        if not isinstance(box_id, int):
            pos = box.get("Position", {})
            if isinstance(pos, dict):
                px = pos.get("X", 0)
                py = pos.get("Y", 0)
                if isinstance(px, int) and isinstance(py, int):
                    box_id = py * 10 + px
            if not isinstance(box_id, int):
                pos_list = item.get("BoxPositionData", [])
                if isinstance(pos_list, list) and pos_list:
                    p0 = pos_list[0]
                    if isinstance(p0, dict):
                        px = p0.get("X", 0)
                        py = p0.get("Y", 0)
                        if isinstance(px, int) and isinstance(py, int):
                            box_id = py * 10 + px

        # Infer shape from occupied cells when ItemSlotType is not provided.
        if shape is None:
            pos_list = item.get("BoxPositionData", [])
            if isinstance(pos_list, list) and pos_list:
                xs: List[int] = []
                ys: List[int] = []
                for pos in pos_list:
                    if not isinstance(pos, dict):
                        continue
                    x = pos.get("X", 0)
                    y = pos.get("Y", 0)
                    if isinstance(x, int) and isinstance(y, int):
                        xs.append(x)
                        ys.append(y)
                if xs and ys:
                    w_inf = max(xs) - min(xs) + 1
                    h_inf = max(ys) - min(ys) + 1
                    if 1 <= w_inf <= 9 and 1 <= h_inf <= 9:
                        shape = w_inf * 10 + h_inf

        row = (box_id // 10) if isinstance(box_id, int) else None
        col = (box_id % 10) if isinstance(box_id, int) else None
        w, h = _shape_wh(shape if isinstance(shape, int) else None)
        name = csv_index[item_cid].name if item_cid in csv_index else None

        true_items.append(
            {
                "uid": uid,
                "box_id": box_id,
                "row": row,
                "col": col,
                "shape": shape,
                "width": w,
                "height": h,
                "quality": quality,
                "item_cid": item_cid,
                "item_name": name,
                "item_price": item_price,
                "item_types": item_types if isinstance(item_types, list) else [],
            }
        )

    true_items.sort(
        key=lambda x: (
            1 if x.get("box_id") is None else 0,
            x.get("box_id") if isinstance(x.get("box_id"), int) else 10**9,
            str(x.get("uid", "")),
        )
    )
    return {
        "winner_uid": data.get("WinUserUid", ""),
        "completed_round": gd.get("Round"),
        "revealed_item_count": len(true_items),
        "items": true_items,
    }


def _round_record(
    stage: str,
    state: GameState,
    csv_index: Dict[int, CsvItem],
    csv_items: List[CsvItem],
    completed_round: Optional[int] = None,
) -> Dict[str, Any]:
    return {
        "stage": stage,
        "current_round": state.current_round,
        "completed_round": completed_round,
        "grid_snapshot": _snapshot_grid(state, csv_index, csv_items),
    }


def _collect_round_records(
    log_path: str,
    csv_path: str,
    include_item_events: bool = False,
    last_game_only: bool = True,
    skip_game_uids: Optional[set] = None,
    ended_only: bool = False,
) -> Dict[str, Any]:
    """
    Export structured round records to JSON.

    Records include:
      - round-level inferred grid snapshots (S2C_33 / S2C_37)
      - optional realtime item snapshots (S2C_39)
      - final revealed true layout from S2C_45.StockContainer
    """
    csv_index, csv_items = load_csv(csv_path)
    silent = io.StringIO()

    all_games: List[Dict[str, Any]] = []
    cur_game: Optional[Dict[str, Any]] = None
    state = GameState()
    game_active = False
    skip_current_game = False
    skip_game_uids = skip_game_uids or set()
    seq = 0

    for line in iter_log_lines(log_path, tail=False):
        if line is None:
            break
        result = extract_event(line)
        if not result:
            continue
        event_type, data = result
        seq += 1

        if event_type == "S2C_33_game_start_notify":
            if game_active and cur_game is not None and not skip_current_game and not ended_only:
                cur_game["ended"] = False
                all_games.append(cur_game)

            gd = data.get("GameData", {})
            game_uid = str(gd.get("Uid", "") or "")
            skip_current_game = game_uid in skip_game_uids
            game_active = True
            cur_game = None
            if skip_current_game:
                continue

            state = GameState()
            handle_s2c33(data, state, csv_index, csv_items, silent)
            cur_game = {
                "game_uid": state.uid,
                "map_id": state.map_id,
                "started_seq": seq,
                "ended": False,
                "players_final": {},
                "round_records": [
                    _round_record("round_start", state, csv_index, csv_items, completed_round=0),
                ],
                "final_reveal": None,
            }
            continue

        if not game_active:
            continue
        if skip_current_game:
            if event_type == "S2C_45_game_over_notify":
                game_active = False
                skip_current_game = False
            continue
        if cur_game is None:
            continue

        if event_type == "S2C_37_game_next_round_notify":
            gd = data.get("GameData", {})
            completed_round = gd.get("Round")
            handle_s2c37(data, state, csv_index, csv_items, silent)
            cur_game["round_records"].append(
                _round_record(
                    "round_settlement",
                    state,
                    csv_index,
                    csv_items,
                    completed_round=completed_round,
                )
            )
            continue

        if event_type == "S2C_39_game_use_item":
            handle_s2c39(data, state, csv_index, csv_items, silent)
            if include_item_events:
                cur_game["round_records"].append(
                    _round_record(
                        "realtime_item_event",
                        state,
                        csv_index,
                        csv_items,
                        completed_round=state.current_round,
                    )
                )
            continue

        if event_type == "S2C_45_game_over_notify":
            true_layout = _extract_true_layout(data, csv_index)
            handle_s2c45(data, state, csv_index, csv_items, silent)
            cur_game["round_records"].append(
                _round_record(
                    "game_over_inference_final",
                    state,
                    csv_index,
                    csv_items,
                    completed_round=true_layout.get("completed_round"),
                )
            )
            cur_game["final_reveal"] = true_layout
            cur_game["players_final"] = copy.deepcopy(state.players)
            cur_game["ended"] = True
            cur_game["ended_seq"] = seq
            all_games.append(cur_game)
            game_active = False
            cur_game = None

    if game_active and cur_game is not None and not ended_only:
        cur_game["players_final"] = copy.deepcopy(state.players)
        cur_game["ended"] = False
        all_games.append(cur_game)

    games = [all_games[-1]] if (last_game_only and all_games) else all_games
    payload = {
        "log_path": log_path,
        "csv_path": csv_path,
        "game_count": len(games),
        "games": games,
    }

    return payload


def export_round_records(
    log_path: str,
    csv_path: str,
    output_path: str,
    include_item_events: bool = False,
    last_game_only: bool = True,
) -> Dict[str, Any]:
    payload = _collect_round_records(
        log_path=log_path,
        csv_path=csv_path,
        include_item_events=include_item_events,
        last_game_only=last_game_only,
    )
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return payload


def _load_existing_record_manifest(records_dir: str) -> Dict[str, Any]:
    manifest_path = os.path.join(records_dir, "manifest.json")
    if not os.path.exists(manifest_path):
        return {}
    try:
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    return manifest if isinstance(manifest, dict) else {}


def _existing_exported_game_uids(records_dir: str) -> set:
    manifest = _load_existing_record_manifest(records_dir)
    exported = set()
    for game in manifest.get("games", []):
        if not isinstance(game, dict):
            continue
        if not game.get("ended", False):
            continue
        filename = str(game.get("file", "") or "")
        if filename and not os.path.exists(os.path.join(records_dir, filename)):
            continue
        game_uid = str(game.get("game_uid", "") or "")
        if game_uid:
            exported.add(game_uid)
    return exported


def _safe_file_token(s: str) -> str:
    s = s.strip().replace(":", "_")
    s = re.sub(r"[^0-9A-Za-z_\-]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s or "unknown"


def extract_game_timestamp(raw_uid: str) -> str:
    digit_groups = re.findall(r"\d+", raw_uid)
    if digit_groups:
        return max(digit_groups, key=len)
    return ""


def _format_local_dt(dt_local: datetime, filename: bool = False) -> str:
    if filename:
        return dt_local.strftime("%Y%m%d_%H%M%S")
    return dt_local.strftime("%Y-%m-%d %H:%M:%S")


def _format_game_real_time_from_anchor(
    timestamp: str,
    anchor_timestamp: Optional[str],
    anchor_time: Optional[datetime],
    *,
    filename: bool = False,
) -> str:
    if not timestamp.isdigit() or not anchor_timestamp or not anchor_timestamp.isdigit():
        return ""
    if anchor_time is None:
        return ""
    try:
        delta_us = int(anchor_timestamp) - int(timestamp)
        dt_local = anchor_time - timedelta(microseconds=delta_us)
    except (OverflowError, ValueError):
        return ""
    return _format_local_dt(dt_local.astimezone(), filename=filename)


def format_game_real_time(
    timestamp: str,
    anchor_timestamp: Optional[str] = None,
    anchor_time: Optional[datetime] = None,
) -> str:
    """
    Format a game UID-like timestamp.

    BidKing game UIDs advance like microsecond counters, but they are not based
    on the Unix epoch or the previously assumed 2000-01-01 epoch. Use a known
    log/export time as the anchor; without one, return an empty string instead
    of inventing a wrong calendar date.
    """
    return _format_game_real_time_from_anchor(
        timestamp,
        anchor_timestamp,
        anchor_time,
        filename=False,
    )


def format_game_real_time_filename(
    timestamp: str,
    anchor_timestamp: Optional[str] = None,
    anchor_time: Optional[datetime] = None,
) -> str:
    return _format_game_real_time_from_anchor(
        timestamp,
        anchor_timestamp,
        anchor_time,
        filename=True,
    )


def _format_game_real_time_legacy_epoch(timestamp: str, *, filename: bool = False) -> str:
    if not timestamp.isdigit():
        return ""
    try:
        dt_utc = GAME_UID_EPOCH_UTC + timedelta(microseconds=int(timestamp))
        dt_local = dt_utc.astimezone()
    except (OverflowError, ValueError):
        return ""
    return _format_local_dt(dt_local, filename=filename)


def _game_timestamp_prefix(game: Dict[str, Any]) -> str:
    raw_uid = str(game.get("game_uid", "") or "")
    timestamp = extract_game_timestamp(raw_uid)
    if timestamp:
        return timestamp
    started_seq = game.get("started_seq")
    if isinstance(started_seq, int) and started_seq > 0:
        return str(started_seq)
    return "unknown_time"


def _anchor_time_from_log(log_path: Optional[str]) -> datetime:
    if log_path:
        try:
            if os.path.exists(log_path):
                return datetime.fromtimestamp(os.path.getmtime(log_path)).astimezone()
        except OSError:
            pass
    return datetime.now().astimezone()


def _game_time_anchor(
    log_path: Optional[str],
    games_sorted: List[Dict[str, Any]],
) -> Tuple[Optional[str], Optional[datetime]]:
    numeric_timestamps = [
        _game_timestamp_prefix(game)
        for game in games_sorted
        if _game_timestamp_prefix(game).isdigit()
    ]
    if not numeric_timestamps:
        return None, None
    return max(numeric_timestamps, key=int), _anchor_time_from_log(log_path)


def _game_manifest_entry(
    filename: str,
    game: Dict[str, Any],
    order: int,
    *,
    anchor_timestamp: Optional[str],
    anchor_time: Optional[datetime],
) -> Dict[str, Any]:
    timestamp = _game_timestamp_prefix(game)
    real_time = format_game_real_time(timestamp, anchor_timestamp, anchor_time)
    map_id = int(game.get("map_id", 0) or 0)
    game_uid = str(game.get("game_uid", "") or "")
    started_seq = int(game.get("started_seq", 0) or 0)
    ended = bool(game.get("ended", False))
    return {
        "file": filename,
        "timestamp": timestamp,
        "real_time": real_time,
        "game_uid": game_uid,
        "map_id": map_id,
        "started_seq": started_seq,
        "ended": ended,
        "order": order,
        "real_time_source": "log_mtime_anchor" if real_time else "",
        "label": f"{timestamp} | {real_time or 'unknown_time'} | 地图 {map_id} | UID {game_uid}",
    }


def _unique_record_filename(
    records_dir: str,
    base_filename: str,
    started_seq: int,
    used_names: set,
) -> str:
    candidate = base_filename
    if candidate not in used_names and not os.path.exists(os.path.join(records_dir, candidate)):
        return candidate

    stem, ext = os.path.splitext(base_filename)
    seq_suffix = f"_seq{started_seq}" if started_seq > 0 else "_dup"
    candidate = f"{stem}{seq_suffix}{ext}"
    n = 2
    while candidate in used_names or os.path.exists(os.path.join(records_dir, candidate)):
        candidate = f"{stem}{seq_suffix}_{n}{ext}"
        n += 1
    return candidate


def export_round_records_to_directory(
    log_path: str,
    csv_path: str,
    records_dir: str = "records",
    include_item_events: bool = False,
    last_game_only: bool = False,
) -> Dict[str, Any]:
    """
    Export one JSON per game into `records_dir`, ordered by log sequence.

    File name pattern:
      <timestamp>_map<map_id>_uid_<game_uid>.json
    """
    os.makedirs(records_dir, exist_ok=True)
    existing_manifest = _load_existing_record_manifest(records_dir)
    existing_games = [
        game
        for game in existing_manifest.get("games", [])
        if isinstance(game, dict)
        and game.get("ended", False)
        and str(game.get("file", "") or "")
        and os.path.exists(os.path.join(records_dir, str(game.get("file", "") or "")))
    ]
    existing_uids = {
        str(game.get("game_uid", "") or "")
        for game in existing_games
        if str(game.get("game_uid", "") or "")
    }
    payload = _collect_round_records(
        log_path=log_path,
        csv_path=csv_path,
        include_item_events=include_item_events,
        last_game_only=last_game_only,
        skip_game_uids=existing_uids,
        ended_only=True,
    )
    games = payload.get("games", [])

    # 按 started_seq 排序，确保文件名按时间顺序（日志顺序）可排序。
    games_sorted = sorted(games, key=lambda g: int(g.get("started_seq", 0)))
    anchor_timestamp, anchor_time = _game_time_anchor(log_path, games_sorted)
    written_files: List[str] = []
    manifest_games: List[Dict[str, Any]] = list(existing_games)
    used_names: set = {
        str(game.get("file", "") or "")
        for game in existing_games
        if str(game.get("file", "") or "")
    }
    for idx, game in enumerate(games_sorted, start=1):
        map_id = game.get("map_id", 0)
        game_uid = _safe_file_token(str(game.get("game_uid", "")))
        timestamp = _safe_file_token(_game_timestamp_prefix(game))
        real_time = _safe_file_token(
            format_game_real_time_filename(timestamp, anchor_timestamp, anchor_time)
        )
        base_filename = f"{timestamp}_{real_time}_map{map_id}_uid_{game_uid}.json"
        filename = _unique_record_filename(
            records_dir=records_dir,
            base_filename=base_filename,
            started_seq=int(game.get("started_seq", 0) or 0),
            used_names=used_names,
        )
        out_path = os.path.join(records_dir, filename)
        game_payload = {
            "schema": "bidking-round-record-v1",
            "log_path": payload.get("log_path"),
            "csv_path": payload.get("csv_path"),
            "order": idx,
            "game": game,
        }
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(game_payload, f, ensure_ascii=False, indent=2)
        written_files.append(out_path)
        used_names.add(filename)
        manifest_games.append(
            _game_manifest_entry(
                filename,
                game,
                len(manifest_games) + 1,
                anchor_timestamp=anchor_timestamp,
                anchor_time=anchor_time,
            )
        )

    def _manifest_sort_key(game: Dict[str, Any]) -> Tuple[int, int, str]:
        timestamp = str(game.get("timestamp", "") or "")
        ts_value = int(timestamp) if timestamp.isdigit() else 0
        started_seq = int(game.get("started_seq", 0) or 0)
        return ts_value, started_seq, str(game.get("file", "") or "")

    manifest_games = sorted(manifest_games, key=_manifest_sort_key)
    for order, game in enumerate(manifest_games, start=1):
        game["order"] = order
    manifest_files = [
        str(game.get("file", "") or "")
        for game in manifest_games
        if str(game.get("file", "") or "")
    ]
    existing_anchor = (
        existing_manifest.get("real_time_anchor", {})
        if isinstance(existing_manifest.get("real_time_anchor", {}), dict)
        else {}
    )
    real_time_anchor = {
        "timestamp": anchor_timestamp or str(existing_anchor.get("timestamp", "") or ""),
        "time": (
            _format_local_dt(anchor_time)
            if anchor_time is not None
            else str(existing_anchor.get("time", "") or "")
        ),
        "source": (
            "log_mtime"
            if anchor_time is not None
            else str(existing_anchor.get("source", "") or "")
        ),
    }
    manifest = {
        "schema": "bidking-round-record-manifest-v2",
        "source_log": payload.get("log_path"),
        "csv_path": payload.get("csv_path"),
        "game_count": len(manifest_games),
        "files": manifest_files,
        "real_time_anchor": real_time_anchor,
        "games": manifest_games,
    }
    manifest_path = os.path.join(records_dir, "manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    return {
        "records_dir": records_dir,
        "game_count": len(written_files),
        "existing_count": len(existing_games),
        "total_game_count": len(manifest_games),
        "skipped_existing_count": len(existing_uids),
        "files": written_files,
        "manifest": manifest_path,
    }


def _stage_to_round_label(
    stage: str,
    current_round: int,
    completed_round: Optional[int] = None,
) -> str:
    """
    将内部阶段名转换为中文回放标签。
    """
    _ = completed_round
    if stage == "realtime_item_event":
        return f"第 {current_round} 回合（道具）"
    if stage == "game_over_true_reveal":
        return "游戏结束（揭晓）"
    if stage == "game_over_inference_final":
        return "游戏结束"
    if stage in {"round_start", "round_settlement", "snapshot"}:
        return f"第 {current_round} 回合"
    if stage:
        return f"第 {current_round} 回合（{stage}）"
    return f"第 {current_round} 回合"


def load_round_record_game_for_grid(
    record_path: str,
    csv_path: str,
) -> Tuple[List[Tuple[str, GameState]], Dict[int, CsvItem], List[CsvItem]]:
    """
    Load a single game record JSON and convert it to GridWindow snapshots.
    """
    csv_index, csv_items = load_csv(csv_path)
    with open(record_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    game = data.get("game") if isinstance(data, dict) else None
    if not isinstance(game, dict):
        # 兼容早期 round_records.json 结构：{"games":[...]}
        games = data.get("games", []) if isinstance(data, dict) else []
        if not games:
            raise ValueError("记录文件中未找到 game/games 数据。")
        game = games[0]

    map_id = int(game.get("map_id", 0))
    game_uid = str(game.get("game_uid", ""))
    round_records = game.get("round_records", [])
    if not isinstance(round_records, list) or not round_records:
        raise ValueError("记录文件中 round_records 为空，无法回放。")

    snapshots: List[Tuple[str, GameState]] = []
    for rr in round_records:
        grid = rr.get("grid_snapshot", {}) if isinstance(rr, dict) else {}
        stage = str(rr.get("stage", "snapshot")) if isinstance(rr, dict) else "snapshot"
        cur_round = int(rr.get("current_round", grid.get("round", 1) or 1))
        completed_round = rr.get("completed_round") if isinstance(rr, dict) else None
        item_rows = grid.get("items", []) if isinstance(grid, dict) else []

        state = GameState()
        state.uid = game_uid
        state.map_id = map_id
        state.current_round = cur_round

        if isinstance(item_rows, list):
            for item in item_rows:
                if not isinstance(item, dict):
                    continue
                uid = item.get("uid")
                if not uid:
                    continue
                k = state.get_or_create(str(uid))
                k.box_id = item.get("box_id")
                k.box_id_confirmed = bool(item.get("box_id_confirmed", False))
                k.shape = item.get("shape")
                k.quality = item.get("quality")
                cats = item.get("categories", [])
                if isinstance(cats, list):
                    k.categories = {int(c) for c in cats if isinstance(c, int)}
                exc_cats = item.get("excluded_categories", [])
                if isinstance(exc_cats, list):
                    k.excluded_categories = {int(c) for c in exc_cats if isinstance(c, int)}
                exc_qs = item.get("excluded_qualities", [])
                if isinstance(exc_qs, list):
                    k.excluded_qualities = {int(q) for q in exc_qs if isinstance(q, int)}
                k.item_cid = item.get("item_cid")
                k.price = item.get("price")

        label = _stage_to_round_label(
            stage=stage,
            current_round=cur_round,
            completed_round=completed_round if isinstance(completed_round, int) else None,
        )
        snapshots.append((label, copy.deepcopy(state)))

    # Append final revealed truth layout (if available) as a dedicated replay snapshot.
    final_reveal = game.get("final_reveal", {})
    reveal_items = final_reveal.get("items", []) if isinstance(final_reveal, dict) else []
    if isinstance(reveal_items, list) and reveal_items:
        reveal_state = GameState()
        reveal_state.uid = game_uid
        reveal_state.map_id = map_id
        reveal_round = final_reveal.get("completed_round")
        if isinstance(reveal_round, int):
            reveal_state.current_round = reveal_round
        elif snapshots:
            reveal_state.current_round = snapshots[-1][1].current_round
        else:
            reveal_state.current_round = 1

        for idx, item in enumerate(reveal_items):
            if not isinstance(item, dict):
                continue
            uid = item.get("uid")
            if not uid:
                uid = f"reveal_{idx}"
            k = reveal_state.get_or_create(str(uid))
            k.box_id = item.get("box_id")
            k.box_id_confirmed = isinstance(k.box_id, int)
            k.shape = item.get("shape")
            k.quality = item.get("quality")
            k.item_cid = item.get("item_cid")
            k.price = item.get("item_price")
            item_types = item.get("item_types", [])
            if isinstance(item_types, list):
                k.categories = {int(c) for c in item_types if isinstance(c, int)}

        reveal_label = _stage_to_round_label(
            stage="game_over_true_reveal",
            current_round=reveal_state.current_round,
            completed_round=reveal_state.current_round,
        )
        snapshots.append((reveal_label, copy.deepcopy(reveal_state)))

    return snapshots, csv_index, csv_items


def read_last_round_from_log(
    log_path: str,
    csv_path: str,
) -> Dict[str, Any]:
    """
    Read the last game's last completed round details from log.
    """
    payload = _collect_round_records(
        log_path=log_path,
        csv_path=csv_path,
        include_item_events=True,
        last_game_only=True,
    )
    games = payload.get("games", [])
    if not games:
        raise ValueError("日志中未找到对局数据。")
    game = games[-1]
    round_records = game.get("round_records", [])
    if not round_records:
        raise ValueError("对局中未找到回合记录。")

    final_reveal = game.get("final_reveal") or {}
    last_completed_round = final_reveal.get("completed_round")
    if last_completed_round is None:
        completed_candidates = [
            rr.get("completed_round")
            for rr in round_records
            if isinstance(rr, dict) and rr.get("completed_round") is not None
        ]
        last_completed_round = max(completed_candidates) if completed_candidates else None

    rr_same_completed = [
        rr for rr in round_records
        if isinstance(rr, dict) and rr.get("completed_round") == last_completed_round
    ]
    settlement_rr = None
    for rr in rr_same_completed:
        if rr.get("stage") == "round_settlement":
            settlement_rr = rr
    final_infer_rr = None
    for rr in rr_same_completed:
        if rr.get("stage") == "game_over_inference_final":
            final_infer_rr = rr
    if final_infer_rr is None and rr_same_completed:
        final_infer_rr = rr_same_completed[-1]

    return {
        "schema": "bidking-last-round-v1",
        "log_path": log_path,
        "csv_path": csv_path,
        "game_uid": game.get("game_uid", ""),
        "map_id": game.get("map_id", 0),
        "ended": game.get("ended", False),
        "last_completed_round": last_completed_round,
        "winner_uid": final_reveal.get("winner_uid", ""),
        "players_final": game.get("players_final", {}),
        "round_settlement_snapshot": settlement_rr,
        "game_over_inference_snapshot": final_infer_rr,
        "final_reveal": final_reveal,
    }


def export_last_round_from_log(
    log_path: str,
    csv_path: str,
    output_path: str,
) -> Dict[str, Any]:
    """
    Export the last completed round details of the last game to a JSON file.
    """
    data = read_last_round_from_log(log_path, csv_path)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return data
