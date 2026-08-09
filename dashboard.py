"""
dashboard.py — Live terminal dashboard using the Rich library.
Displays real-time trading status, positions, signals, and trade history.
"""
import sys
import os
from datetime import datetime, timezone
# Force UTF-8 output on Windows
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    os.environ["PYTHONIOENCODING"] = "utf-8"
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.layout import Layout
from rich.live import Live
from rich.columns import Columns
from rich.text import Text
from rich import box
from rich.progress import SpinnerColumn, TextColumn, Progress, BarColumn
from loguru import logger
from config import (
    DRY_RUN,
    TRADING_PAIRS,
    LEVERAGE,
    RISK_PER_TRADE_PCT,
    MAX_OPEN_POSITIONS,
    STOP_LOSS_PCT,
    TAKE_PROFIT_PCT,
    MAX_DAILY_LOSS_PCT,
    BOT_VERSION,
    MUDREX_TRADE_CURRENCY,
    MIN_CONFIDENCE_TO_TRADE,
)

console = Console()

# ─── State shared with the main bot loop ───
class DashboardState:
    def __init__(self):
        self.wallet_balance = 0.0
        self.wallet_available = 0.0
        self.open_positions = []
        self.recent_trades = []
        self.last_signals = []
        self.last_news = []
        self.last_cycle_time = None
        self.next_cycle_in = 300
        self.bot_status = "STARTING"
        self.risk_stats = {}
        self.cycle_count = 0
        self.errors_today = 0

state = DashboardState()


def _format_pnl(pnl: float, currency: str = "₹") -> Text:
    """Format PnL with color."""
    if pnl > 0:
        return Text(f"+{currency}{pnl:,.2f}", style="bold green")
    elif pnl < 0:
        return Text(f"{currency}{pnl:,.2f}", style="bold red")
    else:
        return Text(f"{currency}{pnl:,.2f}", style="dim")


def _format_signal(signal: str) -> Text:
    """Format trade signal with color."""
    colors = {"BUY": "bold green", "SELL": "bold red", "HOLD": "yellow"}
    return Text(signal, style=colors.get(signal, "white"))


def _format_status(status: str) -> Text:
    """Format bot status with color."""
    colors = {
        "RUNNING": "bold green",
        "ANALYZING": "bold cyan",
        "EXECUTING": "bold yellow",
        "PAUSED": "bold red",
        "ERROR": "bold red on white",
        "STARTING": "bold blue",
        "DRY RUN": "bold magenta",
    }
    return Text(status, style=colors.get(status, "white"))


def build_header() -> Panel:
    """Top header with bot info."""
    mode = "[bold magenta]🧪 DRY RUN MODE[/bold magenta]" if DRY_RUN else "[bold green]🔴 LIVE TRADING[/bold green]"
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    header_text = (
        f"[bold cyan]⚡ AI CRYPTO ALGO TRADING BOT[/bold cyan]  v{BOT_VERSION}  |  "
        f"{mode}  |  [dim]{now}[/dim]  |  "
        f"[bold yellow]Cycles: {state.cycle_count}[/bold yellow]  |  "
        f"Pairs: [cyan]{len(TRADING_PAIRS)}[/cyan]  |  "
        f"Leverage: [yellow]{LEVERAGE}x[/yellow]  |  "
        f"Risk/Trade: [yellow]{RISK_PER_TRADE_PCT}%[/yellow]"
    )
    return Panel(header_text, style="bold", box=box.DOUBLE)


def build_wallet_panel() -> Panel:
    """Wallet balance panel."""
    currency_symbol = "₹" if MUDREX_TRADE_CURRENCY == "INR" else "$"

    risk_stats = state.risk_stats
    daily_pnl = risk_stats.get("daily_pnl", 0)
    trades_today = risk_stats.get("trades_today", 0)
    win_rate = risk_stats.get("win_rate", 0)
    limit_hit = risk_stats.get("daily_loss_limit_hit", False)

    pnl_color = "green" if daily_pnl >= 0 else "red"
    limit_str = "[bold red]⚠ LIMIT HIT[/bold red]" if limit_hit else "[green]OK[/green]"

    content = (
        f"[bold]Available:[/bold] [cyan]{currency_symbol}{state.wallet_available:,.2f}[/cyan]  "
        f"[bold]Total:[/bold] [white]{currency_symbol}{state.wallet_balance:,.2f}[/white]\n"
        f"[bold]Daily P&L:[/bold] [{pnl_color}]{currency_symbol}{daily_pnl:+,.2f}[/{pnl_color}]  "
        f"[bold]Daily Limit:[/bold] {limit_str}\n"
        f"[bold]Trades Today:[/bold] [yellow]{trades_today}[/yellow]  "
        f"[bold]Win Rate:[/bold] [cyan]{win_rate:.1f}%[/cyan]  "
        f"[bold]Max Loss Limit:[/bold] [dim]-{MAX_DAILY_LOSS_PCT}%[/dim]"
    )
    return Panel(content, title="[bold]💰 WALLET[/bold]", border_style="cyan")


def build_positions_table() -> Table:
    """Open positions table."""
    table = Table(
        title="📊 OPEN POSITIONS",
        box=box.ROUNDED,
        border_style="green",
        show_header=True,
        header_style="bold cyan",
    )
    table.add_column("Pair", style="bold white")
    table.add_column("Side", justify="center")
    table.add_column("Size")
    table.add_column("Entry")
    table.add_column("Mark")
    table.add_column("Liq. Price")
    table.add_column("Unr. PnL", justify="right")
    table.add_column("Leverage")

    currency_symbol = "₹" if MUDREX_TRADE_CURRENCY == "INR" else "$"

    if not state.open_positions:
        table.add_row(
            "[dim]No open positions[/dim]", "", "", "", "", "", "", "",
        )
    else:
        for pos in state.open_positions:
            side = pos.get("side", "")
            side_color = "green" if side == "LONG" else "red"
            pnl = pos.get("unrealized_pnl", 0)
            pnl_color = "green" if pnl >= 0 else "red"

            table.add_row(
                pos.get("symbol", ""),
                Text(f"{'↑' if side == 'LONG' else '↓'} {side}", style=f"bold {side_color}"),
                f"{pos.get('size', 0):.6f}",
                f"{currency_symbol}{pos.get('entry_price', 0):,.2f}",
                f"{currency_symbol}{pos.get('mark_price', 0):,.2f}",
                f"{currency_symbol}{pos.get('liquidation_price', 0):,.2f}",
                Text(f"{currency_symbol}{pnl:+,.2f}", style=f"bold {pnl_color}"),
                f"{pos.get('leverage', LEVERAGE)}x",
            )

    return table


def build_signals_panel() -> Panel:
    """Last LLM signals panel."""
    if not state.last_signals:
        content = "[dim]Waiting for next analysis cycle...[/dim]"
    else:
        lines = []
        for s in state.last_signals[:5]:
            sig = s.get("signal", "HOLD")
            sig_color = {"BUY": "green", "SELL": "red", "HOLD": "yellow"}.get(sig, "white")
            actionable = "✅" if s.get("actionable") else "⬜"
            lines.append(
                f"{actionable} [{sig_color}]{sig}[/{sig_color}] "
                f"[bold]{s.get('coin', '?')}[/bold]  "
                f"Confidence: [yellow]{s.get('confidence', 0)}%[/yellow]  "
                f"Urgency: [cyan]{s.get('urgency', 'LOW')}[/cyan]\n"
                f"   [dim]→ {s.get('reasoning', '')[:80]}[/dim]"
            )
        content = "\n".join(lines)

    return Panel(
        content,
        title="[bold]🤖 NVIDIA AI SIGNALS[/bold]",
        border_style="magenta",
        subtitle=f"[dim]Min confidence: {MIN_CONFIDENCE_TO_TRADE}%[/dim]",
    )


def build_news_panel() -> Panel:
    """Last news articles panel."""
    if not state.last_news:
        content = "[dim]No news fetched yet...[/dim]"
    else:
        lines = []
        for article in state.last_news[:6]:
            coins = ", ".join(article.get("coins", ["?"]))
            lines.append(
                f"[bold cyan][{coins}][/bold cyan] {article['title'][:70]}...\n"
                f"   [dim]{article.get('source', '?')} · {article.get('published_at', '')[:16]}[/dim]"
            )
        content = "\n".join(lines)

    return Panel(
        content,
        title="[bold]📰 LATEST NEWS[/bold]",
        border_style="blue",
    )


def build_recent_trades_table() -> Table:
    """Recent trade history table."""
    table = Table(
        title="📋 RECENT TRADES",
        box=box.SIMPLE_HEAD,
        border_style="yellow",
        header_style="bold yellow",
    )
    table.add_column("Time", style="dim")
    table.add_column("Pair", style="bold white")
    table.add_column("Side", justify="center")
    table.add_column("Entry")
    table.add_column("Exit")
    table.add_column("PnL", justify="right")
    table.add_column("Status")
    table.add_column("Reason")

    currency_symbol = "₹" if MUDREX_TRADE_CURRENCY == "INR" else "$"

    if not state.recent_trades:
        table.add_row("[dim]No trades yet[/dim]", "", "", "", "", "", "", "")
    else:
        for trade in state.recent_trades[:8]:
            side = trade.get("side", "")
            side_color = "green" if side == "BUY" else "red"
            pnl = trade.get("pnl")
            pnl_str = Text(f"{currency_symbol}{pnl:+,.2f}", style="green" if pnl and pnl >= 0 else "red") if pnl is not None else Text("OPEN", style="cyan")
            status = trade.get("status", "?").upper()
            status_color = {"OPEN": "cyan", "CLOSED": "green", "FAILED": "red", "DRY_RUN": "magenta"}.get(status, "white")
            ts = trade.get("timestamp", "")[:16].replace("T", " ")

            table.add_row(
                ts,
                trade.get("pair", "?"),
                Text(f"{'↑' if side == 'BUY' else '↓'} {side}", style=f"bold {side_color}"),
                f"{currency_symbol}{trade.get('entry_price', 0):,.2f}",
                f"{currency_symbol}{trade.get('exit_price', 0):,.2f}" if trade.get("exit_price") else "[dim]—[/dim]",
                pnl_str,
                Text(status, style=status_color),
                trade.get("reasoning", "")[:30],
            )

    return table


def build_status_footer() -> Panel:
    """Bottom status bar."""
    next_cycle = max(0, state.next_cycle_in)
    cycle_bar = "▓" * int(next_cycle / 10) + "░" * (30 - int(next_cycle / 10))
    last_cycle = state.last_cycle_time.strftime("%H:%M:%S") if state.last_cycle_time else "Never"

    content = (
        f"Status: {_format_status(state.bot_status).plain}  |  "
        f"Last Cycle: [dim]{last_cycle}[/dim]  |  "
        f"Next: [{cycle_bar}] {next_cycle}s  |  "
        f"Errors: [{'red' if state.errors_today > 0 else 'dim'}]{state.errors_today}[/{'red' if state.errors_today > 0 else 'dim'}]  |  "
        f"[dim]Press Ctrl+C to stop[/dim]"
    )
    return Panel(content, style="dim", box=box.MINIMAL)


def render_dashboard() -> str:
    """Build the full dashboard layout (for non-live mode)."""
    console.clear()
    console.print(build_header())

    # Row 1: Wallet + Status
    console.print(build_wallet_panel())

    # Row 2: Positions table
    console.print(build_positions_table())
    console.print()

    # Row 3: Signals + News side by side
    console.print(Columns([build_signals_panel(), build_news_panel()], equal=True))
    console.print()

    # Row 4: Recent trades
    console.print(build_recent_trades_table())
    console.print()

    # Footer
    console.print(build_status_footer())


def print_startup_banner():
    """Print a startup banner (Windows-safe)."""
    banner = """
============================================================
  AI CRYPTO ALGO TRADING BOT  |  v{version}
  NVIDIA NIM + MUDREX INR FUTURES
  News-Driven Signals | Auto Trade | Risk Managed
============================================================
""".format(version=BOT_VERSION)
    console.print(banner, style="bold cyan")
    if DRY_RUN:
        console.print(
            Panel(
                "[bold magenta]DRY RUN MODE ACTIVE -- No real orders will be placed[/bold magenta]",
                border_style="magenta",
            )
        )
