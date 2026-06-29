# Phase 1 Data Model: 低コスト特徴拡充 (030)

スキーマ変更なし（head 0006）。既存テーブルの未活用列を消費。新 DB テーブルなし。

## 既存 DB（消費）
| 列 | 用途 | カバレッジ |
|----|----|----|
| race_horses.jockey_weight | 斤量(handicap)。**loader に新規ロード** | 100% |
| race_horses.weight | 馬体重(ratio 分母, 既存ロード) | — |
| race_results.finish_order | 複勝(top2/top3) 判定 | 99.7% |
| race_horses.jockey_id/trainer_id | 人拡充・コンビ・乗り替わり(既存) | 100% |
| races.venue_code | course_aptitude(既存) | — |
| races.race_date | season(month/季節, 既存) | 100% |

odds/popularity/running_style/corner_orders は**特徴にしない**（市場 or 結果由来）。

## 特徴列契約（registry 追記）
### handicap group
| 列 | source | timing | missing | 配置 |
|----|----|----|----|----|
| carried_weight | race_horses | pre_entry | NULL | STATIC |
| carried_weight_ratio | race_horses | pre_entry | NULL | STATIC (馬体重欠損→NaN) |
| carried_weight_rel | race_horses | pre_entry | NULL | STATIC (レース内平均差) |
| carried_weight_change | history | pre_entry | NULL | materialized (直前 started race as-of) |

### season group (STATIC)
| race_month | races | pre_entry | NULL | 1-12 |
| race_season | races | pre_entry | NULL | 季節区分(春0/夏1/秋2/冬3 等) |

### place_rate group (materialized, as-of 自馬)
| place_rate | history | pre_entry | NULL | top2 率 strictly-before・同日除外 |
| show_rate | history | pre_entry | NULL | top3 率 |
| dist_band_place_rate | history | pre_entry | NULL | 距離帯条件付き top2 率 |

### human_form_plus group (materialized, as-of 跨馬・対象行+同日除外)
| jockey_place_rate / trainer_place_rate | history | pre_entry | NULL | 複勝率 |
| jockey_recent_win_rate | history | pre_entry | NULL | 直近 N rolling |
| jockey_surface_win_rate | history | pre_entry | NULL | (jockey, track_type) |
| jt_combo_win_rate | history | pre_entry | NULL | (jockey_id, trainer_id) コンビ |
| jockey_change | history | pre_entry | NULL | 今走≠直前 started race 騎手→1, 同0, debut NaN |

### course_aptitude group (materialized, as-of 自馬)
| venue_win_rate / venue_place_rate | history | pre_entry | NULL | (horse_id, venue_code) as-of, 母数<min_starts→NaN |

- 静的群(handicap 3 + season 2) → `STATIC_COLUMNS`（materialize しない）。as-of 群 → materialized_columns 自動収録。
- 全列 float64 固定。odds/payout/dividend トークン無し。

## Manifest(025)
FEATURE_VERSION=features-008。source_fingerprint は race_horses 全ロード列(jockey_weight 追加分含む)・races・race_results を自動ハッシュ（新ソース無し）。

## Validation(FR 対応)
- strictly-before/対象行・同日除外(FR-005)、running_style 非使用(FR-006)、float64+NaN 維持(FR-007)、parity(FR-008)、bump 波及(FR-010)、per-group 事前登録(FR-011)、no-schema-change(FR-012)。
