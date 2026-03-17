import sys

import click
from rich.console import Console

from gmail_spam_detector.auth import check_auth_status, get_credentials
from gmail_spam_detector.config import Config, ConfigError
from gmail_spam_detector.processor import run

console = Console()


@click.group()
def cli():
    """Gmail spam detector powered by Claude AI."""


@cli.command()
def auth():
    """Authenticate with Gmail (run this first)."""
    config = Config()
    try:
        if not config.credentials_file.exists():
            console.print(
                f"[red]Error:[/red] credentials file not found at {config.credentials_file}\n"
                "Download OAuth2 credentials from Google Cloud Console and save them there."
            )
            sys.exit(1)
        console.print("Opening browser for Gmail authentication...")
        get_credentials(config)
        console.print("[green]Authentication successful! Token saved.[/green]")
    except Exception as e:
        console.print(f"[red]Authentication failed:[/red] {e}")
        sys.exit(1)


@cli.command()
def status():
    """Check authentication status and configuration."""
    config = Config()
    console.print("[bold]Configuration[/bold]")
    console.print(f"  Credentials file: {config.credentials_file} ({'exists' if config.credentials_file.exists() else '[red]missing[/red]'})")
    console.print(f"  Token file:       {config.token_file} ({'exists' if config.token_file.exists() else 'not yet created'})")
    console.print(f"  Claude model:     {config.claude_model}")
    console.print(f"  Anthropic key:    {'[green]set[/green]' if config.anthropic_api_key else '[red]missing[/red]'}")

    console.print("\n[bold]Auth status[/bold]")
    auth_status = check_auth_status(config)
    if auth_status.get("authenticated"):
        expired = auth_status.get("expired", False)
        if expired:
            console.print("  [yellow]Token expired but can be refreshed automatically.[/yellow]")
        else:
            console.print("  [green]Authenticated and token is valid.[/green]")
    else:
        console.print(f"  [red]Not authenticated:[/red] {auth_status.get('reason')}")
        console.print("  Run: python -m gmail_spam_detector auth")


@cli.command()
@click.option(
    "--execute",
    is_flag=True,
    default=False,
    help="Actually move detected junk to Trash (default is dry-run preview).",
)
@click.option(
    "--query",
    default="in:inbox",
    show_default=True,
    help="Gmail search query to select emails.",
)
@click.option(
    "--max-emails",
    default=200,
    show_default=True,
    help="Maximum number of emails to scan.",
)
@click.option(
    "--delete-junk/--spam-only",
    default=True,
    help="Also delete junk/newsletters (default: yes). Use --spam-only to restrict to spam.",
)
@click.option(
    "--min-confidence",
    default=0.85,
    show_default=True,
    type=float,
    help="Minimum confidence (0-1) required before trashing an email.",
)
@click.option(
    "--model",
    default=None,
    help="Override the Claude model (e.g. claude-opus-4-6).",
)
def scan(execute, query, max_emails, delete_junk, min_confidence, model):
    """Scan inbox and detect spam/junk emails.

    Runs in DRY-RUN mode by default — pass --execute to actually trash emails.
    """
    config = Config()
    if model:
        config.claude_model = model

    dry_run = not execute

    if dry_run:
        console.print("[bold yellow]DRY RUN MODE[/bold yellow] — no emails will be deleted.")
        console.print("Pass [bold]--execute[/bold] to actually move junk to Trash.\n")

    try:
        run(
            config=config,
            dry_run=dry_run,
            query=query,
            max_emails=max_emails,
            delete_junk=delete_junk,
            min_confidence=min_confidence,
        )
    except ConfigError as e:
        console.print(f"[red]Configuration error:[/red] {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted.[/yellow]")
        sys.exit(0)
