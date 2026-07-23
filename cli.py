"""CLI for DocsHaven — search, add repos, manage knowledge base."""

from __future__ import annotations

import argparse
import atexit
import sys
from typing import Any

from storage import Storage
from uri import URIRouter

_storage: Storage | None = None


def get_storage() -> Storage:
    global _storage
    if _storage is None:
        _storage = Storage.default()
        atexit.register(_cleanup)
    return _storage


def _cleanup() -> None:
    global _storage
    if _storage is not None:
        _storage.close()
        _storage = None


def _unwrap_or_exit(result: Any, label: str = "") -> Any:
    """Unwrap a Result or exit with error message."""
    if hasattr(result, "is_err") and result.is_err:
        error = getattr(result, "error", "Unknown error")
        print(f"Error{f' ({label})' if label else ''}: {error}")
        sys.exit(1)
    return result.value


def cmd_search(args: argparse.Namespace) -> None:
    """Search the knowledge base."""
    storage = get_storage()
    limit = max(1, min(args.limit, 1000))
    results = _unwrap_or_exit(storage.search(args.query, limit=limit, explain=getattr(args, "explain", False)), "search")
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
    data = _unwrap_or_exit(storage.add_repo(args.url, description=args.description), "add repo")
    print(f"Added {data['name']}: {data['files_indexed']} files, {data.get('chunks', 0)} chunks")


def cmd_stats(args: argparse.Namespace) -> None:
    """Show knowledge base statistics."""
    storage = get_storage()
    stats = _unwrap_or_exit(storage.stats(), "stats")
    print(f"Collections: {stats['collections']}")
    print(f"Documents: {stats['total_documents']}")
    print(f"Chunks: {stats['total_chunks']}")
    print(f"DB size: {stats['db_size_kb']}KB")


def cmd_uri(args: argparse.Namespace) -> None:
    """URI operations."""
    storage = get_storage()
    router = URIRouter(storage)

    try:
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
    except ValueError as e:
        print(f"Error: {e}")
        sys.exit(1)


def cmd_list(args: argparse.Namespace) -> None:
    """List all collections."""
    storage = get_storage()
    result = _unwrap_or_exit(storage.list_collections(), "list")
    for c in result:
        ctx_count = c.get("context_count", 0)
        ctx_str = f", {ctx_count} contexts" if ctx_count > 0 else ""
        print(f"  {c['name']}: {c['count']} docs, {c['chunks']} chunks{ctx_str}")


def cmd_collection(args: argparse.Namespace) -> None:
    """Collection management."""
    storage = get_storage()

    if args.subcmd == "list":
        result = _unwrap_or_exit(storage.list_collections(), "list collections")
        for c in result:
            domain = c.get("domain", "")
            domain_str = f" [{domain}]" if domain else ""
            print(f"  {c['name']}{domain_str}: {c['count']} docs, {c['chunks']} chunks")

    elif args.subcmd == "show":
        result = _unwrap_or_exit(storage.list_collections(), "list collections")
        for c in result:
            if c["name"] == args.name:
                print(f"Collection: {c['name']}")
                print(f"  Documents: {c['count']}")
                print(f"  Chunks: {c['chunks']}")
                ctx_count = c.get("context_count", 0)
                if ctx_count > 0:
                    print(f"  Contexts: {ctx_count} attachments")
                if c.get("contexts"):
                    print(f"  Context paths: {', '.join(c['contexts'][:5])}")
                if c.get("domain"):
                    print(f"  Domain: {c['domain']}")
                return
        print(f"Collection not found: {args.name}")

    elif args.subcmd == "remove":
        _unwrap_or_exit(storage.remove_collection(args.name), "remove collection")
        print(f"Removed collection: {args.name}")

    elif args.subcmd == "rename":
        _unwrap_or_exit(storage.rename_collection(args.old_name, args.new_name), "rename collection")
        print(f"Renamed: {args.old_name} → {args.new_name}")


def cmd_delete(args: argparse.Namespace) -> None:
    """Delete a document."""
    storage = get_storage()
    file_path = args.file_path
    if ".." in file_path or file_path.startswith("/"):
        print("Error: Invalid file path")
        sys.exit(1)
    result = _unwrap_or_exit(storage.delete_document(file_path), "delete")
    print(f"Deleted: {result['file_path']}")


def cmd_context(args: argparse.Namespace) -> None:
    """Context attachment management."""
    storage = get_storage()

    if args.subcmd == "add":
        _unwrap_or_exit(storage.add_context(args.collection, args.path, args.summary), "add context")
        print(f"Added context: {args.collection}/{args.path}")

    elif args.subcmd == "list":
        collection_filter = getattr(args, "collection", None)
        if collection_filter:
            ctx_result = _unwrap_or_exit(storage.get_context(collection_filter), "get context")
        else:
            ctx_result = _unwrap_or_exit(storage.list_contexts(), "list contexts")
        for c in ctx_result:
            print(f"  [{c['collection']}] {c['path']}: {c['summary'][:80]}...")

    elif args.subcmd == "rm":
        _unwrap_or_exit(storage.remove_context(args.collection, getattr(args, "path", None)), "remove context")
        print(f"Removed context from: {args.collection}")


def cmd_export(args: argparse.Namespace) -> None:
    """Export knowledge base."""
    import csv
    import json

    storage = get_storage()
    data = _unwrap_or_exit(storage.list_documents(), "export")

    if not data:
        print("No documents to export.", file=sys.stderr)
        return

    if args.format == "json":
        sys.stdout.write(json.dumps(data, indent=2))
    elif args.format == "csv":
        writer = csv.DictWriter(sys.stdout, fieldnames=["collection", "path", "title", "content"])
        writer.writeheader()
        writer.writerows(data)
    elif args.format == "md":
        for item in data:
            sys.stdout.write(f"# {item['title']}\n\n{item['content']}\n\n---\n\n")


def cmd_import(args: argparse.Namespace) -> None:
    """Import from JSON backup."""
    import json
    from pathlib import Path

    file_path = Path(args.file)
    if not file_path.exists():
        print(f"Error: File not found: {args.file}")
        sys.exit(1)
    if not file_path.suffix == ".json":
        print(f"Error: Expected .json file, got: {file_path.suffix}")
        sys.exit(1)

    storage = get_storage()
    try:
        with open(args.file) as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON: {e}")
        sys.exit(1)

    # Batch insert all documents at once
    docs = [
        {
            "collection": item.get("collection", ""),
            "path": item.get("path", ""),
            "content": item.get("content", ""),
            "title": item.get("title", ""),
        }
        for item in data
    ]
    _unwrap_or_exit(storage.bulk_insert(docs), "import")

    print(f"Imported {len(data)} documents")


def cmd_serve(args: argparse.Namespace) -> None:
    """Start MCP server."""
    import uvicorn

    from server import mcp

    uvicorn.run(mcp.streamable_http_app(), host="127.0.0.1", port=args.port)


def main() -> None:
    parser = argparse.ArgumentParser(description="DocsHaven CLI")
    parser.add_argument("--version", action="version", version="%(prog)s 0.7.0")
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

    # list (alias for collection list)
    sp = subparsers.add_parser("list", help="List all collections")
    sp.set_defaults(func=cmd_list)

    # collection management
    sp = subparsers.add_parser("collection", help="Collection management")
    col_sub = sp.add_subparsers(dest="subcmd")
    col_sub.add_parser("list", help="List all collections")
    show_p = col_sub.add_parser("show", help="Show collection details")
    show_p.add_argument("name", help="Collection name")
    rm_p = col_sub.add_parser("remove", help="Remove a collection")
    rm_p.add_argument("name", help="Collection name")
    rename_p = col_sub.add_parser("rename", help="Rename a collection")
    rename_p.add_argument("old_name", help="Current collection name")
    rename_p.add_argument("new_name", help="New collection name")
    sp.set_defaults(func=cmd_collection)

    # context management
    sp = subparsers.add_parser("context", help="Context attachment management")
    ctx_sub = sp.add_subparsers(dest="subcmd")
    ctx_add = ctx_sub.add_parser("add", help="Add context attachment")
    ctx_add.add_argument("collection", help="Collection name")
    ctx_add.add_argument("path", help="Context path (e.g., 'overview')")
    ctx_add.add_argument("summary", help="Summary text")
    ctx_list = ctx_sub.add_parser("list", help="List context attachments")
    ctx_list.add_argument("--collection", help="Filter by collection")
    ctx_rm = ctx_sub.add_parser("rm", help="Remove context attachment")
    ctx_rm.add_argument("collection", help="Collection name")
    ctx_rm.add_argument("--path", help="Specific path to remove")
    sp.set_defaults(func=cmd_context)

    # export/import
    sp = subparsers.add_parser("export", help="Export knowledge base")
    sp.add_argument("--format", choices=["json", "csv", "md"], default="json", help="Output format")
    sp.set_defaults(func=cmd_export)

    sp = subparsers.add_parser("import", help="Import from JSON backup")
    sp.add_argument("file", help="JSON file to import")
    sp.set_defaults(func=cmd_import)

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
