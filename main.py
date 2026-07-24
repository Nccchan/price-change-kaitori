#!/usr/bin/env python3
"""
トレーディングカード買取価格更新ツール

競合価格表画像を解析し、Google Sheets の現行価格と比較して自動更新します。

使用例:
  python main.py -i homura_pokemon.png -g pokemon -d 3/3
  python main.py -i macho_db.jpg -g dragonball --dry-run
  python main.py -i img1.png -i img2.png -g onepiece -d 3/3 --yes
  python main.py -i homura.png -g onepiece --margin-box 300 --margin-carton 1500
  python main.py --from-json data/homura_3_5_pokemon.json -g pokemon -d 3/5 --dry-run
  python main.py -g pokemon --fetch-web --competitor homura --dry-run
"""
import sys
import os
from typing import Optional

import click
from dotenv import load_dotenv

load_dotenv()


def _find_previous_json(current_path: str, game: str) -> Optional[str]:
    """
    同じ競合・ゲームの前回データJSONを探す。
    同じディレクトリにある `{competitor}_*_{game}.json` のうち、
    現在のファイルを除いて最も新しいファイルを返す。
    """
    import glob
    import re
    current_path = os.path.abspath(current_path)
    directory = os.path.dirname(current_path)
    current_name = os.path.basename(current_path)

    # ファイル名から競合名を推定: {competitor}_{date...}_{game}.json
    # 例: homura_3_5_pokemon.json → competitor=homura, game=pokemon
    pattern_str = rf"^(.+?)_(.+)_{re.escape(game)}\.json$"
    m = re.match(pattern_str, current_name)
    if not m:
        return None
    competitor_key = m.group(1)

    # 同じ competitor + game のファイルを列挙
    glob_pattern = os.path.join(directory, f"{competitor_key}_*_{game}.json")
    candidates = [
        p for p in glob.glob(glob_pattern)
        if os.path.abspath(p) != current_path
    ]
    if not candidates:
        return None

    # 最も更新日時が新しいものを返す
    return max(candidates, key=os.path.getmtime)


@click.command(context_settings={"help_option_names": ["-h", "--help"]})
@click.option(
    "--image", "-i",
    multiple=True,
    required=False,
    metavar="PATH_OR_URL",
    help="競合価格表画像ファイルパスまたはURL（複数指定可）",
)
@click.option(
    "--from-json",
    default=None,
    metavar="FILE",
    help="解析済みJSONファイルから競合データを読み込む（画像解析をスキップ）",
)
@click.option(
    "--fetch-web",
    is_flag=True,
    default=False,
    help="競合ウェブサイトから価格を自動取得（--image / --from-json の代替）",
)
@click.option(
    "--game", "-g",
    type=click.Choice(["pokemon", "onepiece", "dragonball", "yugioh"], case_sensitive=False),
    required=True,
    help="カードゲーム種別",
)
@click.option(
    "--date", "-d",
    default=None,
    metavar="DATE",
    help="競合価格の日付（例: 3/3, 2026/03/03）",
)
@click.option(
    "--competitor", "-c",
    default=None,
    metavar="NAME",
    help="競合名（homura / macho）。省略時は画像から自動判定",
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="スプレッドシートを更新せず、比較レポートのみ出力",
)
@click.option(
    "--yes", "-y",
    is_flag=True,
    default=False,
    help="確認プロンプトをスキップして自動更新",
)
@click.option(
    "--margin-box",
    type=int,
    default=None,
    metavar="YEN",
    help="BOX マージン上書き（円）",
)
@click.option(
    "--margin-carton",
    type=int,
    default=None,
    metavar="YEN",
    help="カートン マージン上書き（円）",
)
@click.option(
    "--output", "-o",
    default=None,
    metavar="FILE",
    help="レポートをファイルに保存（省略時は標準出力）",
)
@click.option(
    "--spreadsheet-id",
    default=None,
    metavar="ID",
    help="Google Sheets スプレッドシート ID（環境変数 SPREADSHEET_ID より優先）",
)
@click.option(
    "--only-name",
    default=None,
    metavar="PRODUCT_NAME",
    help="指定商品名だけを処理する（1弾検証用・完全一致）",
)
@click.option(
    "--approve-price-decreases",
    is_flag=True,
    default=False,
    help="承認済みの大幅値下げを保留せず反映する",
)
@click.option(
    "--approve-price-increases",
    is_flag=True,
    default=False,
    help="承認済みの5%超値上げを保留せず反映する",
)
def main(
    image: tuple,
    from_json: Optional[str],
    fetch_web: bool,
    game: str,
    date: Optional[str],
    competitor: Optional[str],
    dry_run: bool,
    yes: bool,
    margin_box: Optional[int],
    margin_carton: Optional[int],
    output: Optional[str],
    spreadsheet_id: Optional[str],
    only_name: Optional[str],
    approve_price_decreases: bool,
    approve_price_increases: bool,
):
    """トレーディングカード買取価格更新ツール"""

    # ------------------------------------------------------------------
    # インポート（起動時間最適化のためここで遅延インポート）
    # ------------------------------------------------------------------
    from src.config import SPREADSHEET_ID, MARGINS, PRICE_LABELS, COMPETITOR_NAMES
    from src.gviz_reader import GVizReader
    from src.price_comparator import PriceComparator, compare_daily
    from src.report_generator import ReportGenerator
    from src.sheets_writer import SheetsWriter, GasWriter
    from src.models import GameType, CompetitorType

    if not image and not from_json and not fetch_web:
        click.echo(
            "[ERROR] --image (-i) / --from-json / --fetch-web のいずれかを指定してください。",
            err=True,
        )
        sys.exit(1)

    sid = spreadsheet_id or SPREADSHEET_ID
    game = game.lower()

    # 有効マージン
    base_margins = MARGINS.get(game, {"box": 200, "carton": 200})
    effective_margin_box = margin_box if margin_box is not None else base_margins["box"]
    effective_margin_carton = margin_carton if margin_carton is not None else base_margins["carton"]
    custom_margins = {"box": effective_margin_box, "carton": effective_margin_carton}

    labels = PRICE_LABELS.get(game, {"price1": "価格1", "price2": "価格2"})

    click.echo("=" * 60)
    click.echo(f"  トレーディングカード価格更新ツール")
    click.echo("=" * 60)
    click.echo(f"  ゲーム : {game}")
    click.echo(f"  マージン: {labels['price1']} +{effective_margin_box}円 / {labels['price2']} +{effective_margin_carton}円")
    if fetch_web:
        click.echo(f"  入力   : ウェブ自動取得")
    elif from_json:
        click.echo(f"  入力   : JSONファイル ({from_json})")
    else:
        click.echo(f"  入力   : 画像 {len(image)} ファイル")
    if dry_run:
        click.echo("  モード : DRY-RUN（スプレッドシート更新なし）")
    click.echo("=" * 60)
    click.echo()

    # ------------------------------------------------------------------
    # Step 1: 競合価格データを取得（ウェブ取得 / JSONファイル / 画像解析）
    # ------------------------------------------------------------------
    analyzer = None
    if fetch_web:
        from src.homura_fetcher import HomuraFetcher
        from src.models import GameType as _GameType

        # 競合別フェッチャーを選択
        comp = (competitor or "homura").lower()
        game_type = _GameType.from_str(game)

        if comp == "homura":
            click.echo("【Step 1】 ほむら東京ウェブサイトから価格を取得中...")
            fetcher = HomuraFetcher()
        elif comp in ("kaitorihakase", "買取博士"):
            from src.kaitorihakase_fetcher import KaitorihakaseFetcher
            click.echo("【Step 1】 買取博士ウェブサイトから価格を取得中...")
            fetcher = KaitorihakaseFetcher()
            comp = "kaitorihakase"
        else:
            click.echo(f"[ERROR] --fetch-web は homura / kaitorihakase のみ対応しています（指定: {comp}）", err=True)
            sys.exit(1)

        try:
            competitor_data = fetcher.fetch(game_type, today=date)
        except Exception as e:
            click.echo(f"\n[ERROR] ウェブ取得に失敗しました: {e}", err=True)
            sys.exit(1)

        # データをJSONに保存（前日比較に利用可能にする）
        import json as _json
        import datetime as _dt
        _today = _dt.date.today()
        _json_path = f"data/{comp}_{_today.month}_{_today.day}_{game}.json"
        os.makedirs("data", exist_ok=True)
        with open(_json_path, "w", encoding="utf-8") as _jf:
            _json.dump(fetcher.to_json(competitor_data), _jf, ensure_ascii=False, indent=2)
        click.echo(f"  保存: {_json_path}")

    elif from_json:
        from src.image_analyzer import ImageAnalyzer
        analyzer = ImageAnalyzer()
        click.echo(f"【Step 1】 JSONファイルから競合データを読み込み中: {from_json}")
        try:
            import json as _json
            with open(from_json, encoding="utf-8") as _f:
                _data = _json.load(_f)
            competitor_data = analyzer._build_competitor_data(_data, game, competitor, date)
        except Exception as e:
            click.echo(f"\n[ERROR] JSONファイルの読み込みに失敗しました: {e}", err=True)
            sys.exit(1)
    else:
        from src.image_analyzer import ImageAnalyzer
        analyzer = ImageAnalyzer()
        click.echo("【Step 1】 競合価格表画像を解析中...")
        try:
            if len(image) == 1:
                competitor_data = analyzer.analyze(
                    image[0],
                    game_hint=game,
                    competitor_hint=competitor,
                    date_hint=date,
                )
            else:
                competitor_data = analyzer.analyze_multiple(
                    list(image),
                    game_hint=game,
                    competitor_hint=competitor,
                    date_hint=date,
                )
        except Exception as e:
            click.echo(f"\n[ERROR] 画像解析に失敗しました: {e}", err=True)
            sys.exit(1)

    competitor_name = COMPETITOR_NAMES.get(competitor_data.competitor.value, competitor_data.competitor.value)
    if only_name:
        selected = [item for item in competitor_data.items if item.name.strip() == only_name.strip()]
        if len(selected) != 1:
            click.echo(
                f"[ERROR] --only-name '{only_name}' は1商品に特定できません（該当: {len(selected)}件）",
                err=True,
            )
            sys.exit(1)
        competitor_data.items = selected
        click.echo(f"  対象限定: {only_name}（1商品）")
    click.echo(f"  完了: {competitor_name} / {len(competitor_data.items)} 商品を抽出")
    click.echo()

    # §1.6 Stage 1: OPEは個別variationのshadow照合中。Stage 2ガード完了まで本番書込禁止。
    if fetch_web and comp == "homura" and game == "onepiece" and not dry_run:
        click.echo(
            "[ERROR] ワンピースRunto v2はshadow-onlyです。§1.6 Stage 2完了まで自動書込できません。",
            err=True,
        )
        sys.exit(1)

    # ホムラ単独値を承認判定へ渡さない。先にラントゥと合流し、最終推奨値を確定する。
    # 取得・パース・照合が失敗した場合は安い値へフォールバックせずバッチを停止する。
    runto_evidence = []
    if fetch_web and comp == "homura" and game in ("pokemon", "onepiece", "dragonball"):
        click.echo("【Step 1.5】ラントゥ666と突合し max(ホムラ, ラントゥ) を確定中...")
        try:
            from src.runto_overlay import apply_runto_max
            # 既存auto_maxの確定仕様: ラントゥは BOX +200 / CTN +2,000。
            # ホムラ側のゲーム別マージンと異なる場合でも、各ソースの最終値でmaxを取る。
            runto_selections, runto_matched = apply_runto_max(
                game, competitor_data, effective_margin_box, effective_margin_carton,
                runto_margin_second=(2000 if game in ("onepiece", "dragonball") else effective_margin_carton),
                evidence_sink=runto_evidence,
            )
        except Exception as e:
            click.echo(f"\n[ERROR] ラントゥ突合に失敗したため安全停止しました: {e}", err=True)
            sys.exit(1)
        click.echo(f"  完了: 照合 {runto_matched}件 / ラントゥ採用 {len(runto_selections)}価格")
        for selection in runto_selections:
            homura = f"¥{selection.homura_final:,}" if selection.homura_final is not None else "未掲載"
            click.echo(
                f"  [RUNTO] {selection.name} ({selection.code or '型番なし'}) {selection.unit}: "
                f"ホムラ{homura} / ラントゥ¥{selection.runto_final:,} → ¥{selection.final:,}"
            )
        click.echo()

    # ------------------------------------------------------------------
    # Step 2: 現行価格を Google Sheets から読み込み
    # ------------------------------------------------------------------
    click.echo("【Step 2】 現行価格をスプレッドシートから読み込み中...")
    reader = GVizReader(spreadsheet_id=sid)
    try:
        current_items = reader.read_sheet(game)
    except Exception as e:
        click.echo(f"\n[ERROR] スプレッドシートの読み込みに失敗しました: {e}", err=True)
        click.echo("  --dry-run オプションを使用するか、credentials.json を設定してください。", err=True)
        sys.exit(1)

    click.echo(f"  完了: {len(current_items)} 商品を読み込みました")
    click.echo()

    # ------------------------------------------------------------------
    # Step 3: 価格比較
    # ------------------------------------------------------------------
    click.echo("【Step 3】 価格を比較中...")
    comparator = PriceComparator(custom_margins=custom_margins)
    results = comparator.compare(game, competitor_data, current_items)
    attention_items = comparator.get_attention_items(results)
    large_changes = comparator.get_large_changes(results)
    decrease_holds = comparator.get_decrease_holds(game, results)
    increase_holds = comparator.get_increase_holds(game, results)
    click.echo(f"  完了: 要対応 {len(attention_items)} 商品 / 大幅変動 {len(large_changes)} 商品")
    if decrease_holds and not approve_price_decreases:
        click.echo(f"  大幅値下げ要承認: {len(decrease_holds)}件（自動反映から除外）")
    if increase_holds and not approve_price_increases:
        click.echo(f"  5%超値上げ要承認: {len(increase_holds)}件（自動反映から除外）")
    click.echo()

    # ------------------------------------------------------------------
    # Step 3.4: Supabase 解決層（P2-A / 2026-06-09 起案）
    # report-only。書込先（GAS Webhook）は変えない。
    # HOMURA_RESOLVE_ENABLED=1 のときだけ実行。
    # ------------------------------------------------------------------
    try:
        from src.supabase_resolver import HomuraSupabaseResolver
    except Exception as _e:
        HomuraSupabaseResolver = None  # type: ignore

    if HomuraSupabaseResolver and HomuraSupabaseResolver.is_enabled_by_flag():
        click.echo("【Step 3.4】 Supabase products.homura_ref で SKU 解決中（report-only）...")
        resolver = HomuraSupabaseResolver.from_env()
        if not resolver.enabled:
            click.echo("  [SKIP] SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY が未設定")
        else:
            codes = [item.code for item in competitor_data.items if getattr(item, "code", None)]
            stats = resolver.stats(codes)
            total = stats["total_unique_codes"]
            hit = stats["resolved"]
            miss = stats["unresolved"]
            rate = stats["resolve_rate"] * 100
            click.echo(
                f"  完了: unique={total} / resolved={hit} ({rate:.1f}%) / unresolved={miss}"
            )

            # 未解決コードを TSV に保存
            if miss:
                import datetime as _dt
                import os as _os
                _data_dir = _os.path.join(_os.path.dirname(__file__), "data")
                _os.makedirs(_data_dir, exist_ok=True)
                _unresolved_path = _os.path.join(
                    _data_dir,
                    f"unresolved_homura_{game}_{_dt.date.today().isoformat()}.tsv",
                )
                with open(_unresolved_path, "w", encoding="utf-8") as _f:
                    _f.write("homura_code\n")
                    for c in stats["unresolved_codes"]:
                        _f.write(f"{c}\n")
                click.echo(f"  💡 未解決コード保存: {_unresolved_path}")
        click.echo()

    # ------------------------------------------------------------------
    # Step 3.5: 前日比較
    # ------------------------------------------------------------------
    daily_report_section = ""
    if from_json:
        prev_json_path = _find_previous_json(from_json, game)
        if prev_json_path:
            click.echo(f"  前日データを検出: {os.path.basename(prev_json_path)}")
            try:
                import json as _json2
                with open(prev_json_path, encoding="utf-8") as _f2:
                    _prev_data = _json2.load(_f2)
                prev_competitor_data = analyzer._build_competitor_data(
                    _prev_data, game, None, None
                )
                daily_results = compare_daily(competitor_data, prev_competitor_data)
                significant_count = sum(1 for r in daily_results if r.is_significant)
                click.echo(f"  前日比較: {len(daily_results)} 商品 / 5%以上変動: {significant_count} 商品")
                reporter_tmp = ReportGenerator()
                daily_report_section = reporter_tmp.generate_daily_report(
                    daily_results=daily_results,
                    labels=labels,
                    prev_date=prev_competitor_data.date or "前日",
                    curr_date=competitor_data.date or "本日",
                )
            except Exception as e:
                click.echo(f"  [警告] 前日比較に失敗しました: {e}")
        else:
            click.echo("  前日データが見つかりませんでした（初回実行）")
    click.echo()

    # ------------------------------------------------------------------
    # Step 4: レポート生成
    # ------------------------------------------------------------------
    click.echo("【Step 4】 レポートを生成中...")
    reporter = ReportGenerator()
    report = reporter.generate(
        game=game,
        competitor_data=competitor_data,
        results=results,
        margin_box=effective_margin_box,
        margin_carton=effective_margin_carton,
    )
    change_summary = reporter.generate_change_summary(results, labels)
    parts = [report, change_summary]
    if decrease_holds and not approve_price_decreases:
        from src.price_decrease_guard import format_holds
        parts.append("## 大幅値下げ・要承認\n\n" + format_holds(decrease_holds))
    if increase_holds and not approve_price_increases:
        from src.price_increase_guard import format_holds as format_increase_holds
        parts.append("## 大幅値上げ・要承認\n\n" + format_increase_holds(increase_holds))
    if daily_report_section:
        parts.append(daily_report_section)
    full_report = "\n\n".join(parts)

    if output:
        with open(output, "w", encoding="utf-8") as f:
            f.write(full_report)
        click.echo(f"  レポートを保存: {output}")
        if runto_evidence:
            import json as _json
            from dataclasses import asdict as _asdict
            evidence_path = os.path.splitext(output)[0] + ".runto-evidence.json"
            with open(evidence_path, "w", encoding="utf-8") as f:
                _json.dump([_asdict(row) for row in runto_evidence], f, ensure_ascii=False, indent=2)
            click.echo(f"  価格根拠を保存: {evidence_path}")
    else:
        click.echo()
        click.echo("─" * 60)
        click.echo(full_report)
        click.echo("─" * 60)
    click.echo()

    # ------------------------------------------------------------------
    # Step 5: スプレッドシート更新
    # ------------------------------------------------------------------
    if dry_run:
        click.echo("【DRY-RUN】 スプレッドシートの更新をスキップしました。")
        # Phase A: フラグ時はSupabase二重書込もdry-runで検証（行は増やさない）
        if os.getenv("ENABLE_SB_DUAL_WRITE") == "1":
            try:
                from src import supabase_writer
                _payloads = comparator.build_update_payloads(
                    game=game, comparison_results=results,
                    current_items=current_items,
                    next_available_row=len(current_items) + 2,
                    approve_large_decreases=approve_price_decreases,
                    approve_large_increases=approve_price_increases)
                supabase_writer.write_prices(game, _payloads, dry_run=True)
            except Exception as e:
                click.echo(f"  ⚠️ SB dual-write(dry-run)失敗: {e}")
        return

    if not yes:
        click.echo(f"スプレッドシートを更新します。")
        click.echo(f"  対象: {len(results)} 商品（新規: {sum(1 for r in results if r.is_new)} 商品）")
        if not click.confirm("続行しますか？", default=True):
            click.echo("キャンセルしました。")
            return

    click.echo("【Step 5】 スプレッドシートを更新中...")
    from src.config import GAS_WEBHOOK_URL
    if GAS_WEBHOOK_URL:
        writer = GasWriter(webhook_url=GAS_WEBHOOK_URL)
        next_row = len(current_items) + 2
    else:
        writer = SheetsWriter(spreadsheet_id=sid)
        next_row = writer.get_next_available_row(game)
    try:
        payloads = comparator.build_update_payloads(
            game=game,
            comparison_results=results,
            current_items=current_items,
            next_available_row=next_row,
            approve_large_decreases=approve_price_decreases,
            approve_large_increases=approve_price_increases,
        )
        if decrease_holds and not approve_price_decreases:
            try:
                from src.price_decrease_guard import format_holds
                from src import supabase_writer as _sb_notify
                _sb_notify._notify(format_holds(decrease_holds))
            except Exception as e:
                click.echo(f"  ⚠️ 大幅値下げ一覧のTelegram通知失敗: {e}")
        updated_cells = writer.write_prices(game, payloads)
        click.echo(f"  完了: {updated_cells} セルを更新しました")

        # Phase A: Supabase price_history への二重書込（フラグ時のみ）。
        # ここでの失敗はシート書込（成功済）を絶対に巻き込まない。
        if os.getenv("ENABLE_SB_DUAL_WRITE") == "1":
            try:
                from src import supabase_writer
                supabase_writer.write_prices(game, payloads, dry_run=False)
            except Exception as e:
                click.echo(f"  ⚠️ SB dual-write失敗（シート書込は成功済）: {e}")
                try:
                    supabase_writer._notify(f"⚠️ SB dual-write失敗 ({game}): {e}")
                except Exception:
                    pass

        new_count = sum(1 for p in payloads if p.is_new)
        if new_count > 0:
            click.echo(f"  🆕 {new_count} 商品を新規追加しました")

        # ポケモンシートのA2に書き込み日付を更新
        if game == "pokemon" and isinstance(writer, GasWriter):
            date_str = competitor_data.date or ""
            if date_str:
                try:
                    ok = writer.write_date_cell(game, date_str)
                    if ok:
                        click.echo(f"  📅 A2セルに日付を書き込みました: {date_str}")
                    else:
                        click.echo(f"  ⚠️ A2セルの日付更新に失敗しました（GASが未対応の可能性）")
                except Exception as e:
                    click.echo(f"  ⚠️ A2セルの日付更新をスキップしました: {e}")
    except FileNotFoundError as e:
        click.echo(f"\n[ERROR] {e}", err=True)
        click.echo("\n[ヒント] credentials.json がない場合は --dry-run で比較レポートのみ確認できます。", err=True)
        sys.exit(1)
    except Exception as e:
        click.echo(f"\n[ERROR] スプレッドシートの更新に失敗しました: {e}", err=True)
        sys.exit(1)

    click.echo()
    click.echo("✅ 価格更新が完了しました！")


if __name__ == "__main__":
    main()
