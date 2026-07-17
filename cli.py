"""CLI for DocsHaven — search, add repos, manage knowledge base."""

import argparse
import sys

from storage import Storage
from uri import URIRouter


def get_storage() -> Storage:
    return Storage.default()


def cmd_search(args: argparse.Namespace) -> None:
    """Search the knowledge base."""
    storage = get_storage()
    result = storage.search(args.query, limit=args.limit, explain=getattr(args, "explain", False))
    if result.is_err():
        print(f"Error: {result.error}")
        sys.exit(1)
    results = result.value
    if not results:
        print("No results found.")
        return
    for r in results:
        print(f"{r['score']:.2f} [{r['collection']}] {r['title']}")
        content = r.get("content", "")
        print(f"  {content[:100]}...")
        if "explain" in r:
            e = r["explain"]
            print(f"  explain: base={e['base_score']}, boost={e['type_boost']}, source={e['source']}")
        print()


def cmd_add(args: argparse.Namespace) -> None:
    """Add a repository."""
    storage = get_storage()
    result = storage.add_repo(args.url, description=args.description)
    if result.is_err():
        print(f"Error (Add repo): {result.error}")
        sys.exit(1)
    data = result.value
    print(f"Added {data['name']}: {data['files_indexed']} files, {data.get('chunks', 0)} chunks")


def cmd_stats(args: argparse.Namespace) -> None:
    """Show knowledge base statistics."""
    storage = get_storage()
    result = storage.stats()
    if result.is_err():
        print(f"Error: {result.error}")
        sys.exit(1)
    stats = result.value
    print(f"Collections: {stats['collections']}")
    print(f"Documents: {stats['total_documents']}")
    print(f"Chunks: {stats['total_chunks']}")
    print(f"DB size: {stats['db_size_kb']}KB")


def cmd_uri(args: argparse.Namespace) -> None:
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


def cmd_list(args: argparse.Namespace) -> None:
    """List all collections."""
    storage = get_storage()
    result = storage.list_collections()
    if result.is_err():
        print(f"Error: {result.error}")
        sys.exit(1)
    for c in result.value:
        print(f"  {c['name']}: {c['count']} docs, {c['chunks']} chunks")


def cmd_delete(args: argparse.Namespace) -> None:
    """Delete a document."""
    storage = get_storage()
    result = storage.delete_document(args.file_path)
    if result.is_err():
        print(f"Error: {result.error}")
        sys.exit(1)
    print(f"Deleted: {result.value['file_path']}")


def cmd_serve(args: argparse.Namespace) -> None:
    """Start MCP server."""
    import uvicorn

    from server import mcp

    uvicorn.run(mcp.streamable_http_app(), host="127.0.0.1", port=args.port)


def main() -> None:
    parser = argparse.ArgumentParser(description="DocsHaven CLI")
    subparsers = parser.add_subparsers(dest="command")

    # search
    sp = subparsers.add_parser("search", help="Search knowledge base")
    sp.add_argument("query", help="Search query")
    sp.add_argument("-l", "--limit", type=int, default=10)
    sp.add_argument("-e", "--explain", action="store_true", help="Show scoring breakdown")
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

    # list
    sp = subparsers.add_parser("list", help="List all collections")
    sp.set_defaults(func=cmd_list)

    # delete
    sp = subparsers.add_parser("delete", help="Delete a document")
    sp.add_argument("file_path", help="Document path to delete")
    sp.set_defaults(func=cmd_delete)

    # serve
    sp = subparsers.add_parser("serve", help="Start MCP server")
    sp.add_argument("-p", "--port", type=int, default=8000, help="Port (default: 8000)")
    sp.set_defaults(func=cmd_serve)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    args.func(args)


if __name__ == "__main__":
    main()
