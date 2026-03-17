from dataclasses import dataclass, field

import anthropic
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
from rich.table import Table
from rich import box

from gmail_spam_detector.auth import get_gmail_service
from gmail_spam_detector.claude_classifier import ClassificationResult, classify_emails
from gmail_spam_detector.config import Config
from gmail_spam_detector.gmail_client import (
    EmailMessage,
    batch_get_messages,
    list_messages,
    trash_messages_batch,
)

console = Console()


@dataclass
class RunStats:
    total_seen: int = 0
    total_spam: int = 0
    total_junk: int = 0
    total_trashed: int = 0
    total_failed: int = 0
    dry_run: bool = True


def _make_table(
    emails: list[EmailMessage],
    classifications: dict[str, ClassificationResult],
    junk_ids: list[str],
    dry_run: bool,
) -> Table:
    title = "[bold red][DRY RUN] Would be trashed[/bold red]" if dry_run else "[bold red]Trashing[/bold red]"
    table = Table(title=title, box=box.ROUNDED, show_lines=True)
    table.add_column("From", max_width=30, no_wrap=True)
    table.add_column("Subject", max_width=40, no_wrap=True)
    table.add_column("Type", justify="center")
    table.add_column("Confidence", justify="center")
    table.add_column("Reason", max_width=40)

    junk_set = set(junk_ids)
    for email in emails:
        result = classifications.get(email.id)
        if not result or email.id not in junk_set:
            continue
        color = "red" if result.classification == "spam" else "yellow"
        table.add_row(
            f"[{color}]{email.sender[:30]}[/{color}]",
            f"[{color}]{email.subject[:40]}[/{color}]",
            f"[{color}]{result.classification.upper()}[/{color}]",
            f"[{color}]{result.confidence:.0%}[/{color}]",
            result.reason,
        )
    return table


def run(
    config: Config,
    dry_run: bool = True,
    query: str = "in:inbox",
    max_emails: int = 200,
    delete_junk: bool = True,
    min_confidence: float = 0.85,
) -> RunStats:
    stats = RunStats(dry_run=dry_run)
    config.validate()

    anthropic_client = anthropic.Anthropic(api_key=config.anthropic_api_key)

    console.print("[bold]Authenticating with Gmail...[/bold]")
    service = get_gmail_service(config)
    console.print("[green]Authenticated.[/green]")

    console.print(f"[bold]Fetching up to {max_emails} messages (query: {query!r})...[/bold]")
    message_ids = list_messages(service, query=query, max_results=max_emails)
    stats.total_seen = len(message_ids)
    console.print(f"Found [bold]{stats.total_seen}[/bold] messages.")

    if not message_ids:
        console.print("Nothing to process.")
        return stats

    batch_size = config.batch_size
    batches = [message_ids[i : i + batch_size] for i in range(0, len(message_ids), batch_size)]

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Processing batches...", total=len(batches))

        for batch_num, batch_ids in enumerate(batches, 1):
            progress.update(task, description=f"Batch {batch_num}/{len(batches)}: fetching...")
            emails = batch_get_messages(service, batch_ids)

            progress.update(task, description=f"Batch {batch_num}/{len(batches)}: classifying...")
            classifications = classify_emails(anthropic_client, emails, config.claude_model)

            junk_ids = []
            for email in emails:
                result = classifications.get(email.id)
                if not result:
                    continue
                if result.classification == "spam":
                    stats.total_spam += 1
                elif result.classification == "junk":
                    stats.total_junk += 1

                target_classes = {"spam", "junk"} if delete_junk else {"spam"}
                if (
                    result.classification in target_classes
                    and result.confidence >= min_confidence
                ):
                    junk_ids.append(email.id)

            if junk_ids:
                table = _make_table(emails, classifications, junk_ids, dry_run)
                console.print(table)

                if not dry_run:
                    progress.update(task, description=f"Batch {batch_num}/{len(batches)}: trashing...")
                    trashed, failed = trash_messages_batch(service, junk_ids)
                    stats.total_trashed += len(trashed)
                    stats.total_failed += len(failed)
                else:
                    stats.total_trashed += len(junk_ids)

            progress.advance(task)

    _print_summary(stats)
    return stats


def _print_summary(stats: RunStats) -> None:
    console.print()
    console.rule("[bold]Summary[/bold]")
    console.print(f"  Emails scanned:    [bold]{stats.total_seen}[/bold]")
    console.print(f"  Spam detected:     [bold red]{stats.total_spam}[/bold red]")
    console.print(f"  Junk detected:     [bold yellow]{stats.total_junk}[/bold yellow]")
    if stats.dry_run:
        console.print(f"  Would trash:       [bold]{stats.total_trashed}[/bold] (dry run)")
        console.print("\n[dim]Re-run with --execute to actually delete.[/dim]")
    else:
        console.print(f"  Trashed:           [bold green]{stats.total_trashed}[/bold green]")
        if stats.total_failed:
            console.print(f"  Failed:            [bold red]{stats.total_failed}[/bold red]")
