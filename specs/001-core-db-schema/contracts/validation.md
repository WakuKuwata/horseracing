# Contract: 再利用バリデータ

`horseracing_db.validation` が公開する、下流 (特に取込 feature) が import して使う純関数。
DB 制約に置かないロジックを一元化し、ポリシーの二重実装を防ぐ。

## 公開シグネチャ (安定)

```python
def is_valid_race_id(race_id: str) -> bool:
    """race_id が ^[0-9]{12}$ を満たすか。"""

def is_in_ingest_scope(race_date: datetime.date) -> bool:
    """取込対象 (2007-01-01 以降) か。2007 境界の唯一の正本 (FR-024 / R8)。"""
```

## 契約上の保証

- `is_in_ingest_scope` は 2007 境界の **唯一の判定点**。取込 feature はこの関数を使い、独自に
  日付比較を書かない (ポリシー二重化の禁止)。
- 両関数とも副作用なし・DB 非依存 (純関数)。ユニットテストで境界値を検証する:
  - `is_valid_race_id`: 12桁数字=true、11/13桁・英字混入・空=false。
  - `is_in_ingest_scope`: 2007-01-01=true、2006-12-31=false。

## 非ゴール

- 取込時の実バリデーション統合 (ジョブ失敗化・記録) は取込 feature の責務。本 feature は純関数 +
  ユニットテストまで。
