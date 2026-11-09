# 178 JRDB 経路の運用手順(runbook)

JRDB(有料会員・固定長ファイル)を第 3 の writer として入れ、netkeiba を policy で止められるようにする経路の手順。
入口は `scripts/jrdb_ops.sh <command> ...`(認証情報は `config/.env.jrdb`・gitignore・値は echo しない)。
**wrapper は便利のためで安全境界ではない**: CLI 自身が policy・env・認証・制限時間帯・書込み先プロファイルを検査し、
欠ければ 1 件も送らずに exit 2 で止まる(176 と同じ)。

会員規約(個人利用・複製公表禁止): 配信ファイル・会員 URL・認証情報・ファイル由来の値を repo / fixture / ログ / 公開成果物に
置かない。**開催日 08:00〜19:00 は過去データ(年度パック・バックナンバー)を取りに行かない**(policy `jrdb_archive_blackout`・CLI が拒否)。

## 0. policy のキー(`config/source-operations.json`・すべて任意・CLI 起動時に読む=再起動不要)

| キー | 既定 | 意味 |
|---|---|---|
| `jrdb_enabled` | false | JRDB の取得と書込みを許す(false なら `jrdb-*` は何も送らず exit 2) |
| `netkeiba_enabled` | true | **false で netkeiba への要求が 0 件**(scrape CLI・ops job・live 捕捉の fetcher が作られる前に止まり SKIPPED)。コードは消さない |
| `jra_emergency_entries` | false | 公式出馬表から出走行・マスタを作る緊急モード(`jra-entries` が読む) |
| `jrdb_interval_seconds` | 5 | 会員ページへの要求間隔(下限 2) |
| `jrdb_archive_blackout` | true | 開催日 08:00〜19:00 の過去データ取得拒否 |
| `near_post_source` | netkeiba | 174 の T−3 捕捉の取得元(`jra_official` にすると公式単複ページ・netkeiba へ fallback しない) |

6 キーは `scripts/source_operations_172.py` の許可リストにあり、型(bool / 数値 / 列挙)が違えば wrapper の起動検証で落ちる。

## 1. 週の流れ

| いつ | コマンド | 書く先 |
|---|---|---|
| 木曜(前週分の正式版) | `jrdb_ops.sh fetch <前週の開催日> --kinds SED SRB` → `jrdb_ops.sh results <開催日> --profile current` | race_results / race_laps / 確定単勝オッズ / 体重(SED **正式版のみ**・速報版は fetch で止まる) |
| 木曜 | `jrdb_ops.sh fetch <開催日> --kinds KZA CZA` → `jrdb_ops.sh persons --files-dir ABS [--write]` | id_mappings(source=jrdb・mapped/conflict。名前だけの一致は書かない) |
| 前日 20:00 以降(金・土) | `jrdb_ops.sh fetch <翌開催日> --kinds BAC KYI UKC` → `jrdb_ops.sh entries <翌開催日> --profile current` | races / race_horses / horses(+来歴)。netkeiba が先に行を入れていれば `nk:X` 行に書く(176 FR-011a の別名規則) |
| 当日朝 | `jrdb_ops.sh fetch <開催日> --kinds TYB` → `jrdb_ops.sh prerace <開催日> --profile current [--venue E|W|L]` | 馬体重の対・取消(発走 15 分前の直前情報。T−3 には間に合わないので判断記録の入力にはしない) |
| 当日 T−3 | 174 `scripts/near_post_ops.sh capture <開催日>`(取得元は policy `near_post_source`) | decision_quotes |
| 夜 | `jrdb_ops.sh fetch <開催日> --kinds HJC` → `jrdb_ops.sh payouts <開催日> --profile current` | official_win_payouts(source `jrdb_result`)/ exotic_odds 6 券種 |
| 夜 | `jrdb_ops.sh fetch <開催日> --kinds SED`(速報版) | **取り込みだけ**(`fetched` で止まる・正式版が来たら `superseded`) |
| いつでも | `jrdb_ops.sh audit <開催日>` | なし(READ ONLY・§3) |

- `--profile current` は本番 DB。`jrdb_canonical`(別定義 4 列も JRDB の定義で書く)は **JRDB 正本の別 DB だけ**で、
  本番 `DATABASE_URL` に向けると CLI が拒否する。
- 同じファイル(同 sha)の再投入は未 applied のレースだけ処理する。異内容のファイルは 速報→正式・日次→バックナンバー だけ自動、
  他は `held`(承認するまで書かない)。
- 1 レースの失敗は他レースに波及しない(`jrdb_files.race_status` にレース単位で applied/failed/skipped)。

## 2. 段の順序(rollout・tasks R0a〜R9)

段 0 診断(`live source-probe`)→ 1 parity(読取専用)→ 2 writer(日次)→ 3a 修復(`jrdb_ops.sh repair COLUMN FROM TO [--dry-run]`・
基準合格列のみ)/ 3b 別 DB 再構築(`jrdb_ops.sh rebuild FROM TO --db-url URL`・現行と同じ DB は拒否・JRDB < 現行の年は exit 1)→
3.5 隔離試験(`live source-trial --date D --db-url 複製 --files-dir ABS`・2 開催日・実時間)→ 4 netkeiba 停止。

**段 4 に進む条件**(FR-015): 3.5 が対象レース全件で成立し、2 のゲート(static_mismatch 0・parity の基準)が成立していること。
段 0 の取得率は合否に使わない。

## 3. 止める・戻す・見る

### netkeiba を止める(段 4)
1. `config/source-operations.json` の `netkeiba_enabled` を `false` に(`near_post_source` は先に `jra_official` に)。
2. 以後、netkeiba の scrape CLI は `SKIPPED: netkeiba_disabled`・ops の refresh/day job は `status=skipped`・`summary.reason=netkeiba_disabled`、
   live の捕捉 fetcher は作られない(`near_post_source=netkeiba` のままなら `REFUSED: skipped:source_unavailable`)。
   **動作中の fetcher も次の要求の前に policy を読み直して止まる**(構築時だけの検査ではない)。止まった job は
   `failed`/`skipped` で終わるので、policy を戻す前に再試行しても同じ結果になる。
3. 確認: `jrdb_ops.sh audit <開催日>` の `stage.policy.netkeiba_enabled=false`、ops の `GET /ops/v1/jobs` に netkeiba の SUCCEEDED job が増えない。

### JRDB の当日ファイルが来ないとき(US4 受入 2)
1. `jrdb_ops.sh audit <開催日>` の `stage.jrdb_files_missing` と `stage.stopped` を見る(`entries: no race rows ...` なら行がない)。
2. 選択肢 A(緊急モード): policy `jra_emergency_entries: true` → `scripts/jra_ops.sh entries <開催日>`(公式出馬表から行とマスタを作る・
   既存行のあるレースは触らない・取消馬の馬番は変更情報ページから復元)。後から KYI が来ても公式所有の静的項目は書き換えない。
3. 選択肢 B(netkeiba 再開): `netkeiba_enabled: true` に戻し `near_post_source` を `netkeiba` に(174 の手順どおり)。
4. どちらも policy の編集だけで、再起動は要らない。切替の時刻と理由を `result-review.md` に残す。

### 緊急モードと netkeiba 再開を同時に選ばない(codex D25 Q2-3)
出走行の生成担当は **1 つの取得元**にする。緊急モード(公式)で行を作った日に netkeiba を再開すると、netkeiba は既存行へ
別名規則(176 FR-011a)で書き、静的項目は公式所有のまま keep・取消や騎手は時刻規則で動く。衝突すれば隔離(`quarantined_collision`)。
切替は「旧経路の job が全部終わってから」(ops の `GET /ops/v1/jobs` で RUNNING が無いことを確認)。

### 再開後に止まっていた期間の穴を埋める(codex D25 Q2-5)
`netkeiba_enabled: true` は**以後の取得を許すだけ**。止まっていた期間の穴は別に埋める:
1. 停止期間(policy を変えた時刻)を `result-review.md` に残す。
2. 日ごとに `jrdb_ops.sh audit <開催日>` で `stage.stopped` を見る(entries / results / final_odds / payouts)。
3. 埋められるもの: 出走表・結果・確定オッズ・払戻 → JRDB(`entries`/`results`/`payouts`)か ops の範囲更新
   (053 `POST /ops/v1/refresh-range`・35 日上限)を**依存順**(entries → results → payouts)に冪等再投入。
4. 埋められないもの: **T−3 判断記録(decision_quotes)と判断時点のオッズ**は後から復元できない。欠測として記録する
   (175 の採点はその日を分母から外す)。
5. 再投入後に audit を取り直し、`stopped` が空になったことで閉じる。

### 読み先の確認
停止表示と実際の送信が別ファイルを見ていないか: `stage.policy.path`/`sha256`(audit)と ops job の `summary.policy`
(SKIPPED 時)が同じ値であること。`HORSERACING_SOURCE_POLICY_PATH` は複製 DB の試験でだけ使い、本番では設定しない(絶対パス必須)。

### `held` の承認(T073)
`held` = 同じ (種別・日・variant) で内容の違うファイルが来て、新旧を規則で決められないもの(速報→正式・日次→バックナンバー以外)。
writer は `held` を読まない(`notes` に `held: <file>` が出るだけ)。

1. `jrdb_ops.sh show-held [開催日]`(READ ONLY・要求 0 件): held 行ごとに `current`(いま applied の版)と `diff`
   (`only_in_held` / `only_in_current` / `changed[].fields = {項目: [現行, held]}`・50 件で打ち切り `truncated`)。
   `diff` は**入力の差分であって製品表の変更予定ではない**(`note` に明記): 先着保持の項目は承認しても変わらない。
   `changed_fields_count` は全行の項目別件数なので、打ち切りの外に取消があっても見える。`error` があれば承認できない
   (本文が無い・現行版の本文が無い[INV-F3]・同じ鍵に applied が 2 行[INV-F1 違反]・decode 失敗)。
2. 差分が説明できる(例: 取消の反映・馬番の訂正)なら `jrdb_ops.sh apply-held FILE_ID --profile current`。
   `--yes` を付けないと差分を表示して exit 2 で止まる(書込み 0)。
3. `--yes --replaces <current の jrdb_file_id>`(現行版が無ければ `--replaces none`)で: 現行版 → `superseded`、held → `fetched`
   (race_status 空)にし、その種別の writer(`jrdb-entries` 等)を同じ日に再実行。`--replaces` が無い・見た版と違う(その間に別の版が
   applied になった)・同じ鍵に承認済み未適用の版がある(`pending_version`)なら停止する=見た差分と失効させる版が一致するときだけ通る。
   BAC/UKC(KYI の入力)や SRB(SED の入力)を承認したときは、その日・同じ variant の applied な KYI/SED も `fetched` に戻して再実行する。
   **何が変わるかは writer の規則が決める**: 先着保持の項目(距離・馬名・クラス等)は変わらず、遷移規則の項目(取消・騎手・体重)だけ動く。
   `superseded` にした版は artifacts に残る(別名 `.<sha12>.txt`)。
4. writer が失敗したら(`jobs[].status=failed`)**承認をやり直さず**、同じ日の writer(`jrdb_ops.sh entries <日>` 等)を再実行する:
   承認済みの版は `fetched` のまま残り、通常取込と同じ経路で拾われる。
5. 差分が説明できないファイルは `held` のまま置く(放置してよい。audit の `files[].status=held` に出続ける)。

### 読み方(`--json`)
- exit 0 = 全 job SUCCEEDED / SKIPPED、1 = PARTIAL(隔離・held・部分失敗)、2 = 起動検査で停止(`jrdb_disabled` / `credentials_missing` /
  `archive_blackout` / `profile_binding` / `netkeiba_disabled`)。
- `summary.files[]` の `published_at`(Last-Modified)/`fetched_at`/`fetched_at_basis` が「その値がいつ使えるようになったか」の材料
  (data-model §3a・来歴の job_id から辿る)。アーカイブは mtime しか無いので上限としてしか言えない。
- `static_mismatch` は報告で拒否ではない(176 と同じ)。`unresolved_person` は id_mappings に無い騎手・調教師コード(NULL のまま)。

## 4. 今週と来週の段取り(2026-11-12〜11-23・R0/R5/R8)

前提: R2 本番反映・R1/R4 parity・R3 人物写像・R6 修復・R7 再構築は 2026-11-09 に済み(`result-review.md`)。
**R5(`jrdb_enabled: true`)に切り替えるかどうかは利用者判断**。切り替えない週は下の「JRDB 書込み」の行を飛ばし、取得と診断だけ行う。
年度パック・旧年の単体は開催日 08:00〜19:00 に取らない(下の取得はどれも当週・前週の日次ファイルなので対象外)。

### 人物ゲート(R5 の切替条件・2026-11-09 決定前の提案)
`jrdb_ops.sh audit <開催日>` の `stage.persons`:
- `mapped`(公式 ID を書く)・`reviewed_gap`(写像が無いと確認済み=地方・外国人で DB にも正規 ID が無い人。その行の騎手/調教師 ID は NULL)・
  `unreviewed`(id_mappings に行が無い新顔)。**`unreviewed` が 0 のときだけ `ready: true`**=その日の `jrdb-entries` を流してよい。
- `unreviewed` が出たら: 木曜の KZA/CZA を取り → `jrdb_ops.sh persons --files-dir … --codes-from …`(dry-run)→ 名鑑にいれば mapped、
  いなければ名前だけの候補を確認して `--manual` で承認するか unmapped のまま書く(どちらでも `reviewed` になる)。

| いつ | やること | コマンド |
|---|---|---|
| 11/12(木)16:00 以降 | 前週 11/07・08 の SED 正式版+SRB を取得(R5 なら結果を書く) | `jrdb_ops.sh fetch 2026-11-07 --kinds SED`・同 11-08 → (R5)`jrdb_ops.sh results <日> --profile current` |
| 11/12(木) | 週次の騎手/調教師マスタ → 新顔の確認 | `jrdb_ops.sh fetch 2026-11-14 --kinds KZA CZA`(マスタの日付は配信日=木の翌々日表記)→ `persons`(dry-run) |
| 11/14(金)15:40 以降 | 11/15 の BAC/KYI/UKC を取得 → 人物ゲート → (R5)出走表を JRDB で作る | `jrdb_ops.sh fetch 2026-11-15 --kinds BAC KYI UKC` → `audit 2026-11-15`(`persons.ready`)→ `entries 2026-11-15 --profile current` |
| 11/15(土)朝 | **R0 段 0 診断**(合否に使わない): JRDB 当日ファイルの到着時刻・公式 T−3 取得率・netkeiba 依存 | `live source-probe --date 2026-11-15 --mode arrivals --until 2026-11-15T20:00`(10 分ごと HEAD)・`--mode official-odds`・`--mode dependencies` |
| 11/15(土)日中 | 174 の T−3 捕捉は従来どおり(`near_post_source: netkeiba`)・TYB 取得 | `near_post_ops.sh capture 2026-11-15`・`jrdb_ops.sh fetch 2026-11-15 --kinds TYB` → (R5)`prerace` |
| 11/15(土)15:40 以降 | 11/16 の BAC/KYI/UKC → ゲート → (R5)出走表 | 金曜と同じ手順で 11-16 |
| 11/15(土)夜 | HJC 払戻 → (R5)払戻を書く・**R2(6)**: 0025 適用後の通常運用を確認(`GET /ops/v1/jobs` に失敗が増えていない・health head 一致) | `jrdb_ops.sh fetch 2026-11-15 --kinds HJC` → `payouts 2026-11-15 --profile current`・`audit 2026-11-15` |
| 11/16(日) | 11/15 と同じ(R0 の 2 日目) | — |
| 11/19(木)16:00 以降 | 11/15・16 の SED 正式版 → 結果 | 11/12 と同じ |
| 11/21(金)〜23(月祝) | **R8 隔離試験**(2 開催日): 当日 00:00 時点の本番複製を作り、netkeiba を遮断して JRDB+公式だけで全レースが揃うか | `createdb horseracing_trial` + restore → `live source-trial --date 2026-11-22 --db-url … --files-dir …/artifacts/jrdb --arrivals artifacts/jrdb/probe/2026-11-15.jsonl`(2 日目は `--delay-kind KYI --by 3h` で緊急モード) |
| R8 合格後 | **R9**: 前の開催日で `near_post_source: jra_official` を 1 日 → `netkeiba_enabled: false` | §3「netkeiba を止める」 |

## 5. 書かないもの
- 別定義の 4 列(`first_3f`・`corner_orders`・`running_style`・`sire_line/damsire_line`)は **どの経路でも現行 DB に書かない**(FR-021a)。
- TYB・基準オッズを単複オッズの現在値に書かない(JRDB は市場値の供給元ではない)。
- `race_date < 2025-10-11`(JRA-VAN 期)には通常取込で書かない(修復 CLI だけが列単位で NULL を埋める)。
- 会員ファイルの raw・会員 URL・認証情報を repo・fixture・ログに置かない。
