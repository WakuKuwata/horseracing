# Phase 1 Data Model: 血統適性 as-of 特徴 (026)

スキーマ変更なし（migration head=0006 不変）。下記は **in-memory データフロー上のエンティティ** と **特徴列契約**であり、新 DB テーブルではない。

## 既存 DB（消費するのみ）

### horses（既存・migration 0001）
| 列 | 型 | 026 での用途 | 実カバレッジ |
|----|----|------|------|
| horse_id | text PK | 馬→血統リンク・自馬除外キー | — |
| sire_name | text? | **sire 集計キー** | ~100% |
| damsire_name | text? | damsire 集計キー（任意 group） | ~100% |
| dam_name | text? | 不使用（fingerprint には含める） | ~100% |
| sire_id / dam_id / damsire_id | text? | 不使用（deferred、fingerprint には含める） | ~0% |

## In-memory エンティティ

### Frames（拡張）
既存 `races`/`race_horses`/`race_results` に **optional** `horses` を追加。
- `horses`: columns = [horse_id, sire_name, dam_name, damsire_name, sire_id, dam_id, damsire_id]
- default = 空 DataFrame（後方互換: 既存 `Frames(...)` 呼び出し・make_frames）。

### runs（pedigree builder 内部）
race_horses ⨝ races(date,distance,track_type) ⨝ race_results(finish_order,status) ⨝ horses(sire_name,damsire_name)。
派生: `is_finished`, `is_win`, `finish_for_avg`(NaN if not finished), `dist_band`(020 `_DIST_BINS`), `track_type`。

### Sire-aptitude record（per (race_id, horse_id) 出力）
他産駒（自馬除外）・strictly-before の集計値（§ algorithm）。

## 特徴列契約（registry 追記）

### sire_aptitude group（必須）
| 列 | source | timing | missing | 定義（他産駒・strictly-before） |
|----|--------|--------|---------|------|
| sire_win_rate | pedigree | pre_entry | NULL | (Σwins_sire−Σwins_self)/(cnt_sire−cnt_self), 分母0→NaN |
| sire_avg_finish | pedigree | pre_entry | NULL | (Σfin_sire−Σfin_self)/(cnt_sire−cnt_self) |
| sire_starts | pedigree | pre_entry | ZERO_OK | cnt_sire−cnt_self（他産駒 finished 数＝信頼度） |
| sire_dist_band_win_rate | pedigree | pre_entry | NULL | 対象レース dist_band 条件付き他産駒勝率, 他産駒cnt<min_starts→NaN |
| sire_surface_win_rate | pedigree | pre_entry | NULL | 対象レース track_type 条件付き他産駒勝率, 同上 |

### damsire_aptitude group（任意・ablation-gated）
| 列 | source | timing | missing | 定義 |
|----|--------|--------|---------|------|
| damsire_win_rate | pedigree | pre_entry | NULL | damsire 他産駒勝率（全体のみ） |
| damsire_avg_finish | pedigree | pre_entry | NULL | damsire 他産駒平均着順（全体のみ） |

- 全列 STATIC_COLUMNS に含めない → `materialized_columns()` 自動収録。
- leak-guard: 列名に odds/payout/dividend を含まない。timing=pre_entry（post_result でない）。

## Manifest（025 拡張）
- `feature_version`: features-007（bump）。
- `source_fingerprint`: races/race_horses/race_results に加え **horses の血統列**（sire_name/dam_name/damsire_name/sire_id/dam_id/damsire_id）を含む。horses は `through` までの kept-race 出走馬に restrict してハッシュ（未来馬で誤発火しない）。
- `materialized_columns`: sire/damsire 列を含む（registry 自動導出）。

## Validation rules（FR 対応）
- 自馬除外（FR-004）: 集計母集団から同一 horse_id を控除。
- 同日除外（FR-005）: cumsum−当日。
- Unknown 維持（FR-006）: 分母0・min_starts 未満・sire_name 欠損 → NaN（0 補完しない）。
- 単一実装（FR-007）: build_asof_features 経由のみ（in-memory/fallback/materialize 共有）。
- パリティ（FR-009）: 血統列含め materialize==in-memory bit 一致。
- staleness（FR-010）: 血統列込み fingerprint で fail-closed。
