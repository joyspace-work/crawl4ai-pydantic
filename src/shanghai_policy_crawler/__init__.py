"""Scrapy-first policy crawler package."""

def main() -> None:
    from .crawler import main as _main

    _main()

__all__ = ["main"]
