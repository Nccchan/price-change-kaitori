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
def main(
    image: tuple,
    from_json: Optional[str],
    game: str,
    date: Optional[str],
    competitor: Optional[str],
    dry_run: bool,
    yes: bool,
    margin_box: Optional[int],
    margin_carton: Optional[int],
    output: Optional[str],
    spreadsheet_id: Optional[str],
):
    """トレーディングカード買取価格更新ツール"""

    # ------------------------------------------------------------------
    # インポート（起動時間最適化のためここで遅延インポート）
    # ------------------------------------------------------------------
    from src.config import SPREADSHEET_ID, MARGINS, PRICE_LABELS, COMPETITOR_NAMES
    from src.gviz_reader import GVizReader
    from src.image_analyzer import ImageAnalyzer
    from src.price_comparator import PriceComparator, compare_daily
    from src.report_generator import ReportGenerator
    from src.sheets_writer import SheetsWriter, GasWriter
    from src.models import GameType, CompetitorType

    if not image and not from_json:
        click.echo("[ERROR] --image (-i) または --from-json のどちらかを指定してください。", err=True)
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
    click.echo(f"  画像数 : {len(image)} ファイル")
    if dry_run:
        click.echo("  モード : DRY-RUN（スプレッドシート更新なし）")
    click.echo("=" * 60)
    click.echo()

    # ------------------------------------------------------------------
    # Step 1: 競合価格データを取得（画像解析 or JSONファイル）
    # ------------------------------------------------------------------
    analyzer = ImageAnalyzer()
    if from_json:
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
    click.echo(f"  完了: {competitor_name} / {len(competitor_data.items)} 商品を抽出")
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
    click.echo(f"  完了: 要対応 {len(attention_items)} 商品 / 大幅変動 {len(large_changes)} 商品")
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
    if daily_report_section:
        parts.append(daily_report_section)
    full_report = "\n\n".join(parts)

    if output:
        with open(output, "w", encoding="utf-8") as f:
            f.write(full_report)
        click.echo(f"  レポートを保存: {output}")
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
        )
        updated_cells = writer.write_prices(game, payloads)
        click.echo(f"  完了: {updated_cells} セルを更新しました")

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
