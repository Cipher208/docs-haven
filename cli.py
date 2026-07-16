"""CLI for DocsHaven — search, add repos, manage knowledge base."""

import sys
from pathlib import Path

from storage import Storage
from uri import URIRouter


def get_storage() -> Storage:
    return Storage(Path.home() / ".docshaven")


def cmd_search(args):
    """Search the knowledge base."""
    storage = get_storage()
    results = storage.search(args.query, limit=args.limit)
    if not results:
        print("No results found.")
        return
    for r in results:
        print(f"{r['score']:.2f} [{r['collection']}] {r['title']}")
        print(f"  {r['content'][:100]}...")
        print()


def cmd_add(args):
    """Add a repository."""
    storage = get_storage()
    result = storage.add_repo(args.url, description=args.description)
    if "error" in result:
        print(f"Error: {result['error']}")
        sys.exit(1)
    print(f"Added {result['name']}: {result['files_indexed']} files, {result.get('chunks', 0)} chunks")


def cmd_stats(args):
    """Show knowledge base statistics."""
    storage = get_storage()
    stats = storage.stats()
    print(f"Collections: {stats['collections']}")
    print(f"Documents: {stats['total_documents']}")
    print(f"Chunks: {stats['total_chunks']}")
    print(f"DB size: {stats['db_size_kb']}KB")


def cmd_uri(args):
    """URI operations."""
    storage = get_storage()
    router = URIRouter(storage)

    if args.subcmd == "resolve":
        result = router.resolve(args.uri)
        for k, v in result.items():
            print(f"{k}: {v}")
    elif args.subcmd == "list":
        results = router.list_by_domain(args.domain)
        for r in results:
            print(f"  {r['uri']}")
    elif args.subcmd == "domains":
        domains = router.list_all_domains()
        for d, info in domains.items():
            print(f"  {d}: {info['count']} collections")


def main():
    import argparse

    parser = argparse.ArgumentParser(description="DocsHaven CLI")
    subparsers = parser.add_subparsers(dest="command")

    # search
    sp = subparsers.add_parser("search", help="Search knowledge base")
    sp.add_argument("query", help="Search query")
    sp.add_argument("-l", "--limit", type=int, default=10)
    sp.set_defaults(func=cmd_search)

    # add
    sp = subparsers.add_parser("add", help="Add a repository")
    sp.add_argument("url", help="GitHub repo URL")
    sp.add_argument("-d", "--description", help="Description")
    sp.set_defaults(func=cmd_add)

    # stats
    sp = subparsers.add_parser("stats", help="Show statistics")
    sp.set_defaults(func=cmd_stats)

    # uri
    sp = subparsers.add_parser("uri", help="URI operations")
    uri_sub = sp.add_subparsers(dest="subcmd")
    rp = uri_sub.add_parser("resolve", help="Resolve URI")
    rp.add_argument("uri", help="URI to resolve")
    lp = uri_sub.add_parser("list", help="List URIs in domain")
    lp.add_argument("domain", help="Domain to list")
    uri_sub.add_parser("domains", help="List all domains")
    sp.set_defaults(func=cmd_uri)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    args.func(args)


if __name__ == "__main__":
    main()
