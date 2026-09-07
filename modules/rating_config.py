"""评级档位配置自检（021AE 自 modules/analysis_engine.py 迁入）。

经典引擎删除后，启动自检（app.py）与红线核验（scripts/check_redlines.py R6）
仍需校验三处评级定义一致：config_weights.json / config.py / scoring_engine.py。
本模块独立成文件，不放入 R7 红线保护的 scoring_engine.py。
"""

import json
import os

from config import RATING_THRESHOLDS

_WEIGHTS_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config_weights.json'
)


def _load_weights_config():
    """读取 config_weights.json（缺失/损坏返回 None）"""
    try:
        with open(_WEIGHTS_FILE, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def _get_rating_from_config():
    """从JSON文件获取评级映射，回退到config.py"""
    config = _load_weights_config()
    if config and 'rating_mapping' in config:
        return config['rating_mapping']
    # 回退：将config.py的元组格式转换
    result = {}
    for grade, (lo, hi, label) in RATING_THRESHOLDS.items():
        result[grade] = {'min': lo, 'max': hi, 'label': label}
    return result


def validate_rating_config() -> list:
    """自检评级档位配置（启动时调用，返回问题列表，空列表表示无问题）。

    P0-1 评级边界统一：评级档位存在三处定义，必须保持一致且区间连续无重叠：
      1. config_weights.json 的 rating_mapping（运行时实际生效，支持热加载）
      2. config.py 的 RATING_THRESHOLDS（代码级兜底）
      3. scoring_engine.RATING_THRESHOLDS（v5 引擎常量）

    校验项：
      a) 每档 min <= max
      b) 相邻档位区间连续：高一段的 min == 低一段的 max + 1（无重叠、无间隙）
      c) 三处定义的 min 边界一致
    """
    issues: list = []

    rating_map = _get_rating_from_config()
    if not rating_map:
        issues.append('评级映射为空：config_weights.json 缺失且 config.RATING_THRESHOLDS 为空')
        return issues

    items = sorted(rating_map.items(), key=lambda x: x[1]['min'], reverse=True)

    # a) 单档区间合法性
    for grade, info in items:
        lo, hi = info.get('min'), info.get('max')
        if lo is None or hi is None:
            issues.append(f'评级「{grade}」缺少 min/max 定义')
            continue
        if lo > hi:
            issues.append(f'评级「{grade}」区间非法：min={lo} > max={hi}')

    # b) 区间连续性：高一段 min 应等于低一段 max+1
    for i in range(len(items) - 1):
        upper, lower = items[i], items[i + 1]
        expect = lower[1]['max'] + 1
        if upper[1]['min'] != expect:
            issues.append(
                f'评级区间不连续：{lower[0]}(max={lower[1]["max"]}) 与 {upper[0]}(min={upper[1]["min"]}) '
                f'存在重叠或间隙（期望高一段 min={expect}）'
            )

    # c) 与 config.py 兜底一致（min 边界）
    for grade, (lo, hi, label) in RATING_THRESHOLDS.items():
        if grade not in rating_map:
            issues.append(f'config.RATING_THRESHOLDS 包含评级「{grade}」但运行配置缺失')
            continue
        if rating_map[grade].get('min') != lo:
            issues.append(
                f'评级「{grade}」边界不一致：config.RATING_THRESHOLDS min={lo} '
                f'≠ 运行配置 min={rating_map[grade].get("min")}'
            )

    # c) 与 v5 引擎常量一致（min 边界）
    try:
        from modules import scoring_engine

        for grade, info in scoring_engine.RATING_THRESHOLDS.items():
            if grade not in rating_map:
                issues.append(f'scoring_engine 含评级「{grade}」但运行配置缺失')
                continue
            if rating_map[grade].get('min') != info.get('min'):
                issues.append(
                    f'评级「{grade}」边界不一致：scoring_engine min={info.get("min")} '
                    f'≠ 运行配置 min={rating_map[grade].get("min")}'
                )
    except Exception as e:  # noqa: BLE001
        issues.append(f'scoring_engine 一致性校验失败：{e}')

    return issues
