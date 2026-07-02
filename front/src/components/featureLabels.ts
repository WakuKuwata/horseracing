// Feature 040: single source of truth for feature-name → Japanese display label.
// `derived: true` marks model-internal engineered features (e.g. OOF target encoding) that must
// carry a "導出特徴" badge so a raw internal column name is never shown as if it were an observation.
// Unknown feature names fail OPEN: shown as-is (never hidden — auditability > tidiness).

export type FeatureLabel = { label: string; derived?: boolean };

export const FEATURE_LABELS: Record<string, FeatureLabel> = {
  // --- target-encoded (model-internal) ---
  te_jockey_id: { label: "騎手成績（統計）", derived: true },
  te_trainer_id: { label: "調教師成績（統計）", derived: true },
  // --- static / race conditions ---
  venue_code: { label: "競馬場" },
  distance: { label: "距離" },
  track_type: { label: "馬場種別（芝/ダート）" },
  going: { label: "馬場状態" },
  weather: { label: "天候" },
  race_class: { label: "クラス" },
  race_number: { label: "レース番号" },
  race_month: { label: "開催月" },
  race_season: { label: "季節" },
  field_size: { label: "出走頭数" },
  // --- horse attributes ---
  age: { label: "馬齢" },
  sex: { label: "性別" },
  frame: { label: "枠番" },
  horse_number: { label: "馬番" },
  jockey_id: { label: "騎手" },
  trainer_id: { label: "調教師" },
  weight: { label: "馬体重" },
  weight_diff: { label: "馬体重増減" },
  // --- handicap (斤量) ---
  carried_weight: { label: "斤量" },
  carried_weight_ratio: { label: "斤量/馬体重比" },
  carried_weight_rel: { label: "斤量（レース内相対）" },
  carried_weight_change: { label: "斤量変化" },
  // --- career history (as-of) ---
  career_starts: { label: "通算出走数" },
  days_since_last: { label: "前走からの間隔" },
  prev_finish: { label: "前走着順" },
  prev_last3f: { label: "前走上がり3F" },
  avg_finish: { label: "平均着順" },
  win_rate: { label: "勝率（通算）" },
  place_rate: { label: "連対率" },
  show_rate: { label: "複勝率" },
  cancel_count: { label: "取消回数" },
  exclude_count: { label: "除外回数" },
  stop_count: { label: "競走中止回数" },
  prev_was_cancel: { label: "前走取消" },
  prev_was_exclude: { label: "前走除外" },
  prev_was_stop: { label: "前走中止" },
  has_past_race: { label: "過去出走あり" },
  is_debut: { label: "新馬" },
  past_race_count: { label: "過去出走数" },
  is_low_history: { label: "少履歴馬" },
  // --- recent form ---
  avg_last3_finish: { label: "直近3走平均着順" },
  recent_win_rate: { label: "直近勝率" },
  // --- aptitude ---
  dist_band_win_rate: { label: "距離帯別勝率" },
  dist_band_avg_finish: { label: "距離帯別平均着順" },
  dist_band_place_rate: { label: "距離帯別連対率" },
  surface_win_rate: { label: "芝ダ別勝率" },
  class_transition: { label: "クラス変動" },
  venue_win_rate: { label: "当該競馬場勝率" },
  venue_place_rate: { label: "当該競馬場連対率" },
  // --- human form ---
  jockey_win_rate: { label: "騎手勝率" },
  trainer_win_rate: { label: "調教師勝率" },
  jockey_place_rate: { label: "騎手連対率" },
  trainer_place_rate: { label: "調教師連対率" },
  jockey_recent_win_rate: { label: "騎手直近勝率" },
  jockey_surface_win_rate: { label: "騎手芝ダ別勝率" },
  jt_combo_win_rate: { label: "騎手×調教師 コンビ勝率" },
  jockey_change: { label: "乗り替わり" },
  // --- pace / time (as-of) ---
  rel_last3f_avg: { label: "上がり3F（相対・平均）" },
  rel_last3f_best: { label: "上がり3F（相対・最良）" },
  rel_time_avg: { label: "走破時計（相対・平均）" },
  finish_diff_avg: { label: "着差（平均）" },
  finish_diff_best: { label: "着差（最良）" },
  rel_corner_pos_avg: { label: "コーナー通過位置（相対）" },
  front_runner_rate: { label: "逃げ・先行率" },
  closer_rate: { label: "差し・追込率" },
  // --- pace scenario (field composition) ---
  field_front_rate_ex_self: { label: "他馬の先行率（展開）" },
  field_closer_rate_ex_self: { label: "他馬の差し率（展開）" },
  pace_imbalance_ex_self: { label: "展開の偏り" },
  front_pressure: { label: "先行争い圧力" },
  closer_setup: { label: "差し有利度" },
  style_mismatch: { label: "脚質ミスマッチ" },
  field_style_coverage: { label: "脚質判明率" },
  // --- pedigree (sire / damsire) ---
  sire_win_rate: { label: "父産駒 勝率" },
  sire_avg_finish: { label: "父産駒 平均着順" },
  sire_starts: { label: "父産駒 出走数" },
  sire_dist_band_win_rate: { label: "父産駒 距離帯別勝率" },
  sire_surface_win_rate: { label: "父産駒 芝ダ別勝率" },
  damsire_win_rate: { label: "母父産駒 勝率" },
  damsire_avg_finish: { label: "母父産駒 平均着順" },
  sire_debut_win_rate: { label: "父産駒 新馬戦勝率" },
  debut_x_sire_win_rate: { label: "新馬×父勝率" },
  debut_x_sire_dist_band_win_rate: { label: "新馬×父距離適性" },
  lowhist_x_sire_win_rate: { label: "少履歴×父勝率" },
  lowhist_x_sire_dist_band_win_rate: { label: "少履歴×父距離適性" },
  // --- condition change ---
  dist_change: { label: "距離替わり" },
  surface_switch: { label: "芝ダ替わり" },
  going_change: { label: "馬場状態変化" },
  dist_extension: { label: "距離延長" },
  dist_shortening: { label: "距離短縮" },
  dist_ext_x_closing: { label: "距離延長×末脚" },
  dist_short_x_speed: { label: "距離短縮×時計" },
};

/** Display label for a feature name; unknown names fail open (shown as-is, not hidden). */
export function featureLabel(name: string): FeatureLabel {
  return FEATURE_LABELS[name] ?? { label: name };
}
