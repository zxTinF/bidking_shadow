# -*- coding: utf-8 -*-
from importlib.machinery import SourcelessFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path

_CACHE_FILE = 'hidden_validation.cpython-312.pyc.2564297052080'

cache_path = Path(__file__).with_name('__pycache__') / _CACHE_FILE
module_name = f"{__package__}._cached_{Path(__file__).stem}"
loader = SourcelessFileLoader(module_name, str(cache_path))
spec = spec_from_loader(module_name, loader)
if spec is None:
    raise ImportError(f"Could not create spec for {cache_path}")
module = module_from_spec(spec)
loader.exec_module(module)
for _name in dir(module):
    if _name.startswith('__') and _name not in {'__all__', '__doc__'}:
        continue
    globals()[_name] = getattr(module, _name)


def _is_zero_gap_top_open_singleton(analysis) -> bool:
    if len(analysis.snapshot.forced_hidden_cells) != 1:
        return False
    if analysis.snapshot.estimated_total_cells != len(analysis.snapshot.occupied_cells):
        return False
    if len(analysis.regions) != 1:
        return False
    region = analysis.regions[0]
    return region.kind == 'top_open' and len(region.cells) == 1


def validate_hidden_q56_records(*, records_dir: str, csv_path: str, max_files=None):
    csv_index, csv_items = load_csv(csv_path)
    filenames = sorted(
        name
        for name in os.listdir(records_dir)
        if name.endswith('.json')
        and name != 'manifest.json'
        and '_map' in name
    )
    if max_files is not None:
        filenames = filenames[:max_files]

    results = []
    hit_count = 0
    miss_count = 0
    skipped_count = 0

    for filename in filenames:
        record_path = os.path.join(records_dir, filename)
        try:
            snapshots, _csv_index, _csv_items = load_round_record_game_for_grid(
                record_path,
                csv_path,
            )
        except Exception as exc:
            skipped_count += 1
            results.append({
                'file': filename,
                'status': 'skipped',
                'reason': f'记录格式不支持: {exc}',
            })
            continue
        round4_state = _find_round4_snapshot(snapshots)
        truth_state = _find_truth_snapshot(snapshots)
        if round4_state is None or truth_state is None:
            skipped_count += 1
            results.append({
                'file': filename,
                'status': 'skipped',
                'reason': '缺少第4回合或最终揭晓快照',
            })
            continue

        analysis = analyze_hidden_q56(
            map_id=round4_state.map_id,
            round_no=round4_state.current_round,
            estimated_total_cells=_estimate_total_cells_from_state(round4_state),
            known_items=_observed_items_from_state(round4_state, csv_items),
            csv_items=csv_items,
        )
        if not analysis.snapshot.forced_hidden_cells:
            skipped_count += 1
            results.append({
                'file': filename,
                'status': 'skipped',
                'reason': '当前第4回合没有形成强制隐藏区域',
            })
            continue

        truth_blocks = _true_hidden_blocks_covering_forced_cells(
            truth_state,
            analysis.snapshot.forced_hidden_cells,
            csv_index,
        )
        if _is_zero_gap_top_open_singleton(analysis) and not truth_blocks:
            skipped_count += 1
            results.append({
                'file': filename,
                'status': 'skipped',
                'reason': '零剩余容量的顶边单格，按误报过滤',
                'forced_hidden_cells': len(analysis.snapshot.forced_hidden_cells),
                'plan_count': len(analysis.plans),
            })
            continue

        exact_hit = any(_plan_matches_truth(plan, truth_blocks) for plan in analysis.plans)
        if exact_hit:
            hit_count += 1
            status = 'hit'
        else:
            miss_count += 1
            status = 'miss'
        results.append({
            'file': filename,
            'status': status,
            'forced_hidden_cells': len(analysis.snapshot.forced_hidden_cells),
            'plan_count': len(analysis.plans),
            'truth_block_count': len(truth_blocks),
            'truth_blocks': truth_blocks,
        })

    return {
        'records_dir': records_dir,
        'checked': len(filenames),
        'hit': hit_count,
        'miss': miss_count,
        'skipped': skipped_count,
        'results': results,
    }
